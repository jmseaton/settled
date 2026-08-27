"""§7.1 — cash transactions, from Flex and by hand.

Two sources feed one table, and the whole design turns on keeping them
apart. Flex is authoritative for what IB reported; the manual path exists
for pre-Flex history, for corrections, and for transactions IB categorized
in a way you want to override. A resync must restate the first without ever
touching the second, so `source` is checked on every write path rather than
being a label nobody reads.

The second subtlety is the **restatement window**. IB amends cash
transactions after the fact — the fixture contains a $117.71 deposit and
its later cancellation, both real rows. A pure upsert keyed on
`transactionID` would leave a withdrawn transaction in place forever once
it stopped being reported, so FLEX rows inside the file's own period are
deleted and rewritten from the file. Rows outside that window are left
alone: the file says nothing about them.
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.models.cash_transaction import (
    CashSource,
    CashTransaction,
    CashTransactionType,
    classify_ib_cash_type,
)
from app.models.position_transfer import PositionTransfer
from app.parsers.base import ParseResult

#: Synthesized `broker_transaction_id` prefix for the TRANSFER_IN/OUT rows
#: derived from the Transfers section, so re-importing the same file does
#: not create a second copy of the same flow.
TRANSFER_KEY_PREFIX = "transfer:"


def replace_flex_cash_transactions(
    db: Session,
    parsed: ParseResult,
    account_id: str,
    warnings: list[str],
) -> int:
    """Restate FLEX-sourced cash for the file's period. Returns rows written.

    Called only when the payload actually carried a CashTransactions
    section: a `.tlg` has none, and treating its absence as "IB reports no
    cash this period" would delete a Flex sync's work on the next upload.
    """
    if "CashTransactions" not in parsed.sections_seen:
        return 0

    window_from, window_to = _window(parsed)

    stale = (
        db.query(CashTransaction)
        .filter(
            CashTransaction.account_id == account_id,
            CashTransaction.source == CashSource.FLEX,
            CashTransaction.occurred_on >= window_from,
            CashTransaction.occurred_on <= window_to,
        )
        .all()
    )
    manual_keys = {
        row.broker_transaction_id
        for row in db.query(CashTransaction)
        .filter(
            CashTransaction.account_id == account_id,
            CashTransaction.source == CashSource.MANUAL,
        )
        .all()
        if row.broker_transaction_id
    }
    for row in stale:
        db.delete(row)
    db.flush()

    written = 0
    unrecognized: set[str] = set()
    for ct in parsed.cash_transactions:
        if not (window_from <= ct.occurred_on <= window_to):
            # A row IB dated outside the window it was asked for. Keep it —
            # the data is real — but it falls outside the restatement above,
            # so guard against inserting a duplicate on the next import.
            if _already_present(db, account_id, ct.broker_transaction_id):
                continue

        kind, recognized = classify_ib_cash_type(ct.type, ct.amount)
        if not recognized:
            unrecognized.add(ct.type)

        # §7.1 — a manual row wins over IB's version of the same
        # transaction. That is the entire point of entering one.
        if ct.broker_transaction_id and ct.broker_transaction_id in manual_keys:
            continue

        note = ct.description or None
        if not recognized:
            note = f"[unmapped IB type: {ct.type}] {note}" if note else f"[unmapped IB type: {ct.type}]"

        db.add(
            CashTransaction(
                account_id=account_id,
                occurred_on=ct.occurred_on,
                type=kind,
                amount=ct.amount,
                currency=ct.currency,
                source=CashSource.FLEX,
                broker_transaction_id=ct.broker_transaction_id,
                note=note,
            )
        )
        written += 1

    for raw in sorted(unrecognized):
        warnings.append(
            f"Cash transaction type {raw!r} is not mapped; recorded as ADJUSTMENT with the "
            "raw string preserved (§7.1)"
        )

    db.flush()
    return written


def sync_transfer_flows(db: Session, account_id: str) -> int:
    """Mirror `position_transfers` into TRANSFER_IN/OUT cash rows (§4.7).

    A position arriving by ACATS is money entering the account, and TWR has
    to see it as a flow or the arrival reads as a windfall return. The
    per-unit detail stays in `position_transfers` where §5.5 needs it; this
    is only the account-level flow, valued at the transfer-date mark.
    """
    transfers = (
        db.query(PositionTransfer)
        .filter(PositionTransfer.account_id == account_id)
        .all()
    )
    keys = {f"{TRANSFER_KEY_PREFIX}{t.id}" for t in transfers}

    existing = {
        row.broker_transaction_id: row
        for row in db.query(CashTransaction)
        .filter(
            CashTransaction.account_id == account_id,
            CashTransaction.broker_transaction_id.like(f"{TRANSFER_KEY_PREFIX}%"),
        )
        .all()
    }

    # Transfers are replaced wholesale on every import, so a mirrored row
    # whose transfer no longer exists is an orphan, not history.
    for key, row in existing.items():
        if key not in keys and row.source == CashSource.FLEX:
            db.delete(row)

    written = 0
    for transfer in transfers:
        key = f"{TRANSFER_KEY_PREFIX}{transfer.id}"
        amount = abs(float(transfer.position_amount))
        if transfer.direction.upper() == "OUT":
            kind, signed = CashTransactionType.TRANSFER_OUT, -amount
        else:
            kind, signed = CashTransactionType.TRANSFER_IN, amount

        row = existing.get(key)
        if row is not None and row.source == CashSource.MANUAL:
            continue
        if row is None:
            row = CashTransaction(
                account_id=account_id,
                broker_transaction_id=key,
                source=CashSource.FLEX,
            )
            db.add(row)
            written += 1
        row.occurred_on = transfer.transferred_on
        row.type = kind
        row.amount = signed
        row.currency = "USD"
        row.related_instrument_id = transfer.instrument_id
        row.note = (
            f"{transfer.transfer_type} {transfer.direction} of "
            f"{float(transfer.quantity):g} valued at the transfer-date mark (§5.5)"
        )

    db.flush()
    return written


def _window(parsed: ParseResult) -> tuple[dt.date, dt.date]:
    dates = [ct.occurred_on for ct in parsed.cash_transactions]
    start = parsed.period_start or (min(dates) if dates else dt.date.min)
    end = parsed.period_end or (max(dates) if dates else dt.date.max)
    return start, end


def _already_present(db: Session, account_id: str, broker_transaction_id: str | None) -> bool:
    if not broker_transaction_id:
        return False
    return (
        db.query(CashTransaction.id)
        .filter(
            CashTransaction.account_id == account_id,
            CashTransaction.broker_transaction_id == broker_transaction_id,
        )
        .first()
        is not None
    )
