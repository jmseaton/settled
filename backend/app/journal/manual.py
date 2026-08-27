"""§11.3 — manual adjustments: stop/target, hand-entered fills, splits.

Three features, one shared constraint: **nothing here may be undone by the
next sync.** Trades and groups are deleted and re-derived on every import,
and the raw execution rows are supposed to be exactly what the broker sent.
So each of these writes somewhere that survives:

  * Stops and targets go to `trade_annotations`, keyed on the trade's
    opening broker execution ids (the §6.4 anchor pattern).
  * Hand-entered fills go to `executions` with `source = MANUAL`, which the
    importer's upsert never touches and the grid renders distinctly.
  * Splits go to `corporate_actions` and are applied as a derivation onto
    `executions.split_factor`, leaving the imported figures untouched.
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.importer.corporate_actions import apply_split_factors
from app.journal.anchors import anchor_key_for_trade
from app.journal.service import JournalError
from app.models.enums import Action
from app.models.execution import Execution
from app.models.instrument import Instrument
from app.models.journal import TradeAnnotation
from app.models.option_event import CorporateAction
from app.models.trade import Trade
from app.trading_day import EASTERN, derive_trading_day


class ManualEntryError(ValueError):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _as_float(value, field: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ManualEntryError(f"{field} must be a number, got {value!r}") from None


def set_trade_levels(
    db: Session, account_id: str, trade: Trade, *, stop_loss=None, profit_target=None
) -> TradeAnnotation:
    """§11.3 — the stop and target that unlock R-value.

    Stored against the trade's opening broker execution ids rather than the
    trade row, because that row will not exist after the next import.
    """
    anchor = anchor_key_for_trade(db, trade)
    if anchor is None:
        raise JournalError(
            "This trade's opening leg is synthetic (§5.5/§0.3), so there is no broker "
            "identifier to key a stop to — it would be lost on the next import."
        )

    row = (
        db.query(TradeAnnotation)
        .filter(
            TradeAnnotation.account_id == account_id, TradeAnnotation.anchor_key == anchor
        )
        .one_or_none()
    )
    if row is None:
        row = TradeAnnotation(
            account_id=account_id, anchor_key=anchor, created_at=_now(), updated_at=_now()
        )
        db.add(row)

    try:
        if stop_loss is not None:
            row.stop_loss = _as_float(stop_loss, "stop_loss")
        if profit_target is not None:
            row.profit_target = _as_float(profit_target, "profit_target")
    except ManualEntryError as exc:
        raise JournalError(str(exc)) from None

    row.updated_at = _now()
    db.flush()
    return row


#: The manual path fabricates identifiers, so they are prefixed to make the
#: provenance obvious anywhere one is displayed or logged. A hand-entered
#: row must never be confusable with a broker record (§4.5's rule).
MANUAL_ID_PREFIX = "manual:"


def create_manual_fill(db: Session, account_id: str, payload: dict) -> Execution:
    """§11.3 — a fill for something that appears in no broker export.

    Requires an instrument that already exists: inventing a contract from a
    typed symbol would recreate exactly the §2.4 symbol-parsing problem that
    §3.9 eliminated, and a mistyped one would silently split a position
    across two instruments.
    """
    instrument_id = payload.get("instrument_id")
    if not instrument_id:
        raise ManualEntryError(
            "Pick an existing instrument. Contracts come from the broker's own reference "
            "data (§3.9); typing one by hand would split the position across two."
        )
    instrument = db.get(Instrument, int(instrument_id))
    if instrument is None:
        raise ManualEntryError(f"Instrument {instrument_id} not found")

    quantity = _as_float(payload.get("quantity"), "quantity")
    price = _as_float(payload.get("price"), "price")
    if not quantity:
        raise ManualEntryError("Quantity is required and cannot be zero.")
    if price is None:
        raise ManualEntryError("Price is required.")

    raw_action = (payload.get("action") or "").upper()
    try:
        action = Action(raw_action)
    except ValueError:
        raise ManualEntryError(
            f"Unknown action {raw_action!r}. Expected one of: "
            + ", ".join(a.value for a in Action)
        ) from None

    when = payload.get("executed_at")
    try:
        executed_at = (
            dt.datetime.fromisoformat(when).replace(tzinfo=EASTERN)
            if when
            else dt.datetime.now(EASTERN)
        )
    except ValueError:
        raise ManualEntryError(f"Could not read executed_at {when!r}; use ISO 8601.") from None

    multiplier = float(instrument.multiplier or 1)
    commission = _as_float(payload.get("commission"), "commission") or 0.0
    # Canonical `.tlg` sign convention (§2.2 field 13): signed cost, buy
    # positive. Derived here rather than accepted from the caller, so a
    # manual row cannot enter with the opposite sign to every imported one.
    proceeds = quantity * multiplier * price

    identifier = f"{MANUAL_ID_PREFIX}{payload.get('reference') or _now().strftime('%Y%m%d%H%M%S%f')}"
    if db.query(Execution).filter(
        Execution.account_id == account_id, Execution.broker_trade_id == identifier
    ).first():
        raise ManualEntryError(f"A manual execution with reference {identifier!r} already exists.")

    latest_import = payload.get("import_file_id")
    if latest_import is None:
        from app.models.import_file import ImportFile

        row = (
            db.query(ImportFile.id)
            .filter(ImportFile.account_id == account_id)
            .order_by(ImportFile.id.desc())
            .first()
        )
        if row is None:
            raise ManualEntryError(
                "Import at least one broker file first — a manual row is a correction to a "
                "statement, not a substitute for one."
            )
        latest_import = row[0]

    execution = Execution(
        import_file_id=latest_import,
        source_line=0,
        account_id=account_id,
        broker_trade_id=identifier,
        instrument_id=instrument.id,
        action=action,
        flags=[],
        executed_at=executed_at,
        trading_day=derive_trading_day(executed_at, instrument.asset_class),
        quantity=quantity,
        multiplier=multiplier,
        price=price,
        proceeds=proceeds,
        commission=commission,
        fx_rate=1,
        cash_flow=-proceeds + commission,
        exchange=payload.get("exchange") or "MANUAL",
        source="MANUAL",
    )
    db.add(execution)
    db.flush()
    return execution


def create_manual_split(db: Session, account_id: str, payload: dict) -> CorporateAction:
    """§11.3 — a split IB did not report, or whose ratio could not be parsed."""
    instrument_id = payload.get("instrument_id")
    if not instrument_id:
        raise ManualEntryError("A split needs an instrument.")
    instrument = db.get(Instrument, int(instrument_id))
    if instrument is None:
        raise ManualEntryError(f"Instrument {instrument_id} not found")

    ratio = _as_float(payload.get("split_ratio"), "split_ratio")
    if not ratio or ratio <= 0:
        raise ManualEntryError(
            "split_ratio must be positive — 2 for a 2-for-1, 0.125 for a 1-for-8 reverse."
        )

    raw_date = payload.get("occurred_on")
    try:
        occurred_on = dt.date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        occurred_on = None
    if occurred_on is None:
        raise ManualEntryError("occurred_on is required, as YYYY-MM-DD.")

    action = CorporateAction(
        account_id=account_id,
        instrument_id=instrument.id,
        occurred_on=occurred_on,
        action_type="SPLIT",
        description=payload.get("description")
        or f"Manual split {ratio:g} for 1 on {instrument.broker_symbol}",
        split_ratio=ratio,
        applied=True,
        source="MANUAL",
        import_file_id=_latest_import_id(db, account_id),
    )
    db.add(action)
    db.flush()
    return action


def _latest_import_id(db: Session, account_id: str) -> int:
    from app.models.import_file import ImportFile

    row = (
        db.query(ImportFile.id)
        .filter(ImportFile.account_id == account_id)
        .order_by(ImportFile.id.desc())
        .first()
    )
    if row is None:
        raise ManualEntryError("Import at least one broker file first.")
    return row[0]


def rebuild_after_manual_change(db: Session, account_id: str) -> None:
    """Re-derive everything a hand edit can move.

    A manual fill or a split changes the executions the FIFO matcher reads,
    so trades, groups, chains, journal anchors and the daily series all have
    to be rebuilt — the same sequence an import runs, minus the parsing.
    """
    from app.importer.option_events import (
        detach_option_events_from_groups,
        link_option_events,
    )
    from app.journal.reanchor import reanchor_journal
    from app.journal.service import record_assignment_journal
    from app.models.settings_row import get_settings_row
    from app.performance.service import rebuild_benchmark_shadow
    from app.stats.snapshots import rebuild_daily_snapshots
    from app.trades.assignment import rebuild_assignment_chains
    from app.trades.basis import BasisResolver
    from app.trades.constructor import rebuild_trades
    from app.trades.grouping import rebuild_trade_groups

    apply_split_factors(db, account_id)
    policy = get_settings_row(db).transfer_basis_policy
    rebuild_trades(db, account_id, BasisResolver(db, account_id, policy))
    link_option_events(db, account_id)
    detach_option_events_from_groups(db, account_id)
    rebuild_trade_groups(db, account_id)
    rebuild_assignment_chains(db, account_id)
    reanchor_journal(db, account_id)
    record_assignment_journal(db, account_id)
    rebuild_daily_snapshots(db, account_id)
    rebuild_benchmark_shadow(db, account_id)
