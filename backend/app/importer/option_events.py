"""§5.4 — storing and linking assignments, exercises and expirations.

**The event and its consequence arrive separately.** An assignment shows up
in the *next* statement, and the underlying position it created may land in
the same import or a later one. So detection and linking are two passes:
rows are stored as the broker reports them, then linked against whatever
executions exist right now. A later import re-runs the link and picks up
the half that was missing (test 49).

Nothing here infers an outcome. §5.4 is explicit that a position can look
out-of-the-money on a price feed and still exercise on the 3 p.m. CT
fixing: the broker's records are the only authority, and this module
reports what settled rather than what it calculates should have settled.
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.contracts.reference import SETTLEMENT_CASH
from app.importer.instruments import get_or_create_instrument
from app.models.enums import AssetClass, Right
from app.models.execution import Execution
from app.models.instrument import Instrument
from app.models.option_event import OptionEvent, OptionEventType

#: How far either side of the event date to look for the underlying leg.
#: Assignment and the resulting position are same-dated in IB's records,
#: but a settlement can post the next business day, and a weekend expiry
#: lands on the Monday. Three days covers both without reaching so far that
#: an unrelated fill in the same underlying gets captured.
LINK_WINDOW_DAYS = 3

#: Quantity match tolerance. Whole contracts, so this only absorbs decimal
#: representation, never a genuinely different size.
QTY_TOLERANCE = 1e-6


def replace_option_events(db: Session, parsed, record, account_id: str, warnings: list[str]) -> int:
    """Upsert OptionEAE rows for the file's window.

    Keyed on `(instrument, date, tradeID)`, so a re-import of an overlapping
    window restates rather than duplicates — and, crucially, an already
    acknowledged event stays acknowledged. Re-raising a dismissed
    assignment banner on every nightly sync would train the user to ignore
    it.
    """
    if "OptionEAE" not in parsed.sections_seen:
        return 0

    existing = {
        (e.instrument_id, e.occurred_on, e.broker_trade_id): e
        for e in db.query(OptionEvent).filter(OptionEvent.account_id == account_id).all()
    }

    written = 0
    for event in parsed.option_events:
        instrument = get_or_create_instrument(db, event.symbol, 1.0, warnings)
        key = (instrument.id, event.occurred_on, event.broker_trade_id)
        row = existing.get(key)

        if row is None:
            row = OptionEvent(
                account_id=account_id,
                instrument_id=instrument.id,
                occurred_on=event.occurred_on,
                broker_trade_id=event.broker_trade_id,
                acknowledged=False,
            )
            db.add(row)
            written += 1

        row.import_file_id = record.id
        try:
            row.event_type = OptionEventType(event.transaction_type)
        except ValueError:
            row.event_type = OptionEventType.UNKNOWN
            warnings.append(
                f"OptionEAE row for {event.symbol.broker_symbol!r} reports transactionType "
                f"{event.transaction_type!r}, which is not a recognized event; recorded as "
                "UNKNOWN rather than guessed (§5.4)."
            )
        row.raw_type = event.transaction_type
        row.quantity = event.quantity
        row.trade_price = event.trade_price
        row.proceeds = event.proceeds
        row.commission = event.commission
        row.cost_basis = event.cost_basis
        row.realized_pnl = event.realized_pnl
        row.underlying_conid = event.underlying_conid
        row.underlying_symbol = event.underlying_symbol

    db.flush()
    return written


#: §5.4's direction table. Keyed on (right, the option position's sign):
#: True for a long option, False for a short one. The value is the sign of
#: the underlying position the event produces.
#:
#:   Short put  assigned  -> long underlying
#:   Short call assigned  -> short underlying
#:   Long put   exercised -> short underlying
#:   Long call  exercised -> long underlying
_UNDERLYING_DIRECTION = {
    (Right.PUT, False): 1,
    (Right.CALL, False): -1,
    (Right.PUT, True): -1,
    (Right.CALL, True): 1,
}


def expected_underlying_direction(right: Right | None, option_is_long: bool) -> int | None:
    if right is None:
        return None
    return _UNDERLYING_DIRECTION.get((right, option_is_long))


def link_option_events(db: Session, account_id: str) -> dict:
    """Match each position-creating event to the underlying execution it made.

    Matched on §5.4's four conditions: same date (within the settlement
    window), the option's underlying contract, quantity equal to contracts ×
    multiplier, and a direction consistent with the leg. All four, because
    any three of them will happily match an ordinary fill in the same
    underlying on the same day.

    Expirations are deliberately not linked — they create nothing.
    """
    events = (
        db.query(OptionEvent)
        .filter(
            OptionEvent.account_id == account_id,
            OptionEvent.event_type.in_(list(OptionEventType)),
        )
        .all()
    )

    linked = unlinked = 0
    for event in events:
        option = db.get(Instrument, event.instrument_id)
        event.option_execution_id = _find_option_execution(db, account_id, event)

        if not event.creates_position:
            continue

        # Cash settlement is an *outcome*, not a failed link (test 45). A
        # cash-settled index option pays intrinsic value and creates no
        # position, so there is nothing to find and saying "not found yet"
        # would imply one is still coming.
        if option is not None and option.settlement == SETTLEMENT_CASH:
            event.underlying_execution_id = None
            event.link_note = (
                "Cash-settled: this event produced a cash adjustment at intrinsic value, not "
                "a position, so there is nothing to link (§5.4)."
            )
            continue

        underlying_exec = _find_underlying_execution(db, account_id, event, option)
        if underlying_exec is None:
            event.underlying_execution_id = None
            event.link_note = (
                "No underlying execution matches this event yet. The resulting position "
                "usually appears in the next statement (§5.4); the link is retried on "
                "every import."
            )
            unlinked += 1
            continue

        event.underlying_execution_id = underlying_exec.id
        event.link_note = None
        linked += 1

        # §5.4 — "Record the link on `executions.related_execution_id` in
        # both directions", so a chain can be walked from either end.
        if event.option_execution_id:
            option_exec = db.get(Execution, event.option_execution_id)
            if option_exec is not None:
                option_exec.related_execution_id = underlying_exec.id
                underlying_exec.related_execution_id = option_exec.id

    db.flush()
    return {"linked": linked, "unlinked": unlinked, "events": len(events)}


def _find_option_execution(db: Session, account_id: str, event: OptionEvent) -> int | None:
    """The option's own closing row, matched on the broker's trade id first.

    Falls back to instrument plus date, because the OptionEAE `tradeID` and
    the Trades section's identifiers are not always the same value for the
    same event.
    """
    if event.broker_trade_id:
        hit = (
            db.query(Execution)
            .filter(
                Execution.account_id == account_id,
                Execution.instrument_id == event.instrument_id,
                (Execution.broker_trade_id == event.broker_trade_id)
                | (Execution.transaction_id == event.broker_trade_id)
                | (Execution.ib_order_id == event.broker_trade_id),
            )
            .first()
        )
        if hit is not None:
            return hit.id

    hit = (
        db.query(Execution)
        .filter(
            Execution.account_id == account_id,
            Execution.instrument_id == event.instrument_id,
            Execution.trading_day >= event.occurred_on - dt.timedelta(days=LINK_WINDOW_DAYS),
            Execution.trading_day <= event.occurred_on + dt.timedelta(days=LINK_WINDOW_DAYS),
        )
        .order_by(Execution.executed_at.desc())
        .first()
    )
    return hit.id if hit is not None else None


def _find_underlying_execution(
    db: Session, account_id: str, event: OptionEvent, option: Instrument | None
) -> Execution | None:
    if option is None:
        return None

    # The EAE row's quantity is the **closing** quantity, not the position.
    # A short put being assigned is reported as +1 — one contract bought
    # back to flatten the short — so a positive quantity means the position
    # was *short*. Reading it as the position sign inverts the whole
    # direction table and links a short put to a short stock position.
    option_is_long = float(event.quantity) < 0
    direction = expected_underlying_direction(option.right, option_is_long)
    if direction is None:
        return None

    multiplier = float(option.multiplier or 1)
    # §5.4, test 44 — a futures option exercises into **one futures
    # contract per option**, not into `multiplier` units. The option's
    # multiplier prices the premium; it is not a share count.
    if option.asset_class == AssetClass.FOP:
        expected_qty = abs(float(event.quantity))
    else:
        expected_qty = abs(float(event.quantity)) * multiplier

    candidates = (
        db.query(Execution)
        .join(Instrument, Execution.instrument_id == Instrument.id)
        .filter(
            Execution.account_id == account_id,
            Execution.instrument_id != event.instrument_id,
            Execution.trading_day >= event.occurred_on - dt.timedelta(days=LINK_WINDOW_DAYS),
            Execution.trading_day <= event.occurred_on + dt.timedelta(days=LINK_WINDOW_DAYS),
        )
        .all()
    )

    for candidate in candidates:
        underlying = candidate.instrument
        if not _is_underlying_of(option, underlying, event):
            continue
        quantity = float(candidate.quantity)
        if abs(abs(quantity) - expected_qty) > QTY_TOLERANCE:
            continue
        if (1 if quantity > 0 else -1) != direction:
            continue
        return candidate
    return None


def _is_underlying_of(option: Instrument, underlying: Instrument, event: OptionEvent) -> bool:
    """§5.4 — `option.underlying_conid == underlying.conid`.

    `conid` is the reliable test and is preferred whenever both sides carry
    one. Symbol matching is the fallback for a `.tlg`-sourced instrument,
    which has no conid at all.
    """
    if event.underlying_conid and underlying.conid:
        return event.underlying_conid == underlying.conid
    if option.underlying_conid and underlying.conid:
        return option.underlying_conid == underlying.conid

    target = (event.underlying_symbol or option.underlying_root or "").strip().upper()
    if not target:
        return False
    candidates = {
        (underlying.broker_symbol or "").strip().upper(),
        (underlying.root_symbol or "").strip().upper(),
        (underlying.underlying_root or "").strip().upper(),
    }
    return target in candidates


def assignment_alert(db: Session, account_id: str, link_result: dict | None = None) -> dict:
    """§5.4 — the top-of-report and dashboard banner payload.

    "An unexpected futures or stock position is exactly the thing you want
    to be told about rather than discover. This is the single highest-value
    notification in the app; do not bury it in a warnings list."

    Expirations are excluded on purpose. Ten of them in the real fixture,
    all forecast in advance by §9.5 — bannering those teaches the user to
    dismiss the banner, and the one assignment that matters goes with it.
    """
    outstanding = (
        db.query(OptionEvent)
        .filter(
            OptionEvent.account_id == account_id,
            OptionEvent.event_type.in_(
                [OptionEventType.ASSIGNMENT, OptionEventType.EXERCISE]
            ),
            OptionEvent.acknowledged.is_(False),
        )
        .order_by(OptionEvent.occurred_on.desc())
        .all()
    )

    return {
        "needs_attention": len(outstanding),
        "linking": link_result or {},
        "items": [_alert_item(db, e) for e in outstanding],
        "note": (
            "An assignment appears in the statement *after* the option's last trading day, "
            "so this may be a day old. It stays here until acknowledged (§5.4)."
            if outstanding
            else None
        ),
    }


def _alert_item(db: Session, event: OptionEvent) -> dict:
    instrument = db.get(Instrument, event.instrument_id)
    return {
        "id": event.id,
        "event_type": event.event_type.value,
        "occurred_on": event.occurred_on.isoformat(),
        "symbol": instrument.broker_symbol if instrument else None,
        "underlying": event.underlying_symbol,
        "quantity": float(event.quantity),
        "linked": event.underlying_execution_id is not None,
        "link_note": event.link_note,
    }


def serialize_event(db: Session, event: OptionEvent) -> dict:
    """Full detail for the events grid, including the position it created."""
    instrument = db.get(Instrument, event.instrument_id)
    underlying = None
    if event.underlying_execution_id:
        execution = db.get(Execution, event.underlying_execution_id)
        if execution is not None:
            quantity = float(execution.quantity)
            underlying = {
                "execution_id": execution.id,
                "symbol": execution.instrument.broker_symbol,
                "quantity": quantity,
                "price": float(execution.price),
                "side": "LONG" if quantity > 0 else "SHORT",
                "trading_day": execution.trading_day.isoformat(),
            }

    return {
        "id": event.id,
        "event_type": event.event_type.value,
        "raw_type": event.raw_type,
        "occurred_on": event.occurred_on.isoformat(),
        "symbol": instrument.broker_symbol if instrument else None,
        "strike": float(instrument.strike) if instrument and instrument.strike is not None else None,
        "right": instrument.right.value if instrument and instrument.right else None,
        "settlement": instrument.settlement if instrument else None,
        "quantity": float(event.quantity),
        "realized_pnl_ib": float(event.realized_pnl),
        "underlying_symbol": event.underlying_symbol,
        "option_execution_id": event.option_execution_id,
        "linked": event.underlying_execution_id is not None,
        "underlying": underlying,
        "link_note": event.link_note,
        "trade_group_id": event.trade_group_id,
        "acknowledged": event.acknowledged,
        "needs_attention": event.needs_attention,
    }


def detach_option_events_from_groups(db: Session, account_id: str) -> None:
    """Release `trade_group_id` before a group rebuild deletes the rows.

    Chains are re-established immediately afterwards. Doing this as its own
    step rather than relying on cascade keeps the deletion explicit: an
    event losing its chain is a normal part of every import, and a cascade
    that quietly deleted the *event* instead would lose the broker's record
    of an assignment.
    """
    db.query(OptionEvent).filter(
        OptionEvent.account_id == account_id,
        OptionEvent.trade_group_id.isnot(None),
    ).update({OptionEvent.trade_group_id: None}, synchronize_session=False)
    db.flush()
