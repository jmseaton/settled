"""§3.1-3.3 — the import pipeline.

    parse -> validate -> stage -> normalize instruments
        -> upsert executions (idempotent) -> rebuild trades -> recompute
        metrics -> reconcile vs. LOT records -> import report

The sync path and the upload path converge immediately after retrieval
(§4.1): one parser registry keyed on `source_format`, one pipeline
thereafter. Adding a third format is a new adapter, not a new pipeline.
"""

import datetime as dt
import hashlib
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.importer.cash import replace_flex_cash_transactions, sync_transfer_flows
from app.importer.closed_lots import ClosedLotCheckResult, check_closed_lots
from app.importer.corporate_actions import apply_split_factors, replace_corporate_actions
from app.importer.option_events import (
    assignment_alert,
    detach_option_events_from_groups,
    link_option_events,
    replace_option_events,
)
from app.journal.reanchor import reanchor_journal
from app.journal.service import record_assignment_journal
from app.trades.assignment import rebuild_assignment_chains
from app.importer.instruments import get_or_create_instrument
from app.importer.reconciliation import ReconciliationResult, reconcile
from app.importer.storage import store_payload
from app.models.closed_lot import ClosedLot
from app.models.enums import Action, ImportSource, ImportStatus, SourceFormat
from app.models.execution import Execution
from app.models.import_file import ImportFile
from app.models.position_snapshot import BrokerPositionSnapshot
from app.models.position_transfer import PositionTransfer
from app.models.settings_row import get_settings_row
from app.parsers.base import ParseResult
from app.trades.basis import BasisResolver
from app.parsers.flex import FlexParseError, parse_flex
from app.parsers.tlg import TlgParseError, parse_tlg
from app.performance.service import rebuild_benchmark_shadow
from app.stats.snapshots import rebuild_daily_snapshots
from app.trades.constructor import rebuild_trades
from app.trades.grouping import rebuild_trade_groups
from app.trading_day import derive_trading_day, localize

OUT_OF_WINDOW_GAP_DAYS = 14

#: §4.1 — the parser registry. Keyed on `source_format`; everything
#: downstream of a `ParseResult` is format-agnostic.
PARSERS = {
    SourceFormat.TLG: parse_tlg,
    SourceFormat.FLEX_XML: parse_flex,
}


class ImportError_(Exception):
    """Raised for a fatal parse failure; the whole import rolls back (§3.2)."""


@dataclass
class ImportReport:
    import_file_id: int
    account_id: str
    status: str
    source_format: str
    rows_parsed: dict = field(default_factory=dict)
    rows_inserted: int = 0
    rows_duplicate: int = 0
    date_range: tuple | None = None
    warnings: list[str] = field(default_factory=list)
    reconciliation_passed: bool | None = None
    reconciliation_issues: list[str] = field(default_factory=list)
    closed_lot_check: dict | None = None
    #: §5.4 — "Surface it at the top of the import report". Deliberately its
    #: own field rather than another entry in `warnings`: this is the single
    #: highest-value notification in the app and a warnings list is where
    #: things go to be skimmed past.
    assignment_alert: dict | None = None
    assignment_check: dict | None = None


def detect_format(filename: str, content: bytes) -> SourceFormat:
    head = content[:512].lstrip()
    if head.startswith(b"<"):
        return SourceFormat.FLEX_XML
    if filename.lower().endswith(".xml"):
        return SourceFormat.FLEX_XML
    return SourceFormat.TLG


def _find_out_of_window_dates(dates: list[dt.date], gap_threshold_days: int = OUT_OF_WINDOW_GAP_DAYS) -> set[dt.date]:
    """§16 test 16b — a trade date far outside the file's own range is a real
    signal (a hand edit, or an amended trade). Warn; never drop the row."""
    uniq = sorted(set(dates))
    if len(uniq) < 2:
        return set()
    gaps = [(uniq[i + 1] - uniq[i]).days for i in range(len(uniq) - 1)]
    max_gap = max(gaps)
    if max_gap < gap_threshold_days:
        return set()
    split_idx = gaps.index(max_gap)
    before, after = uniq[: split_idx + 1], uniq[split_idx + 1 :]
    smaller = before if len(before) <= len(after) else after
    if len(smaller) <= max(1, len(uniq) // 4):
        return set(smaller)
    return set()


def import_file(
    db: Session,
    filename: str,
    content: bytes,
    source: ImportSource = ImportSource.FILE_UPLOAD,
    source_format: SourceFormat | None = None,
) -> ImportReport:
    fmt = source_format or detect_format(filename, content)
    sha256 = hashlib.sha256(content).hexdigest()
    parser = PARSERS[fmt]

    try:
        # A binary or mis-encoded upload used to raise UnicodeDecodeError from
        # here, which nothing caught and the API returned as a 500. It is a
        # bad file, so it is the client's error: re-raise it as the parse
        # failure it is, and it takes the FAILED-record path below like any
        # other unreadable upload.
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            error = FlexParseError if fmt is SourceFormat.FLEX_XML else TlgParseError
            raise error(f"Not UTF-8 text: {exc}") from exc
        parsed: ParseResult = parser(text)
    except (TlgParseError, FlexParseError) as exc:
        db.rollback()
        record = ImportFile(
            source=source,
            source_format=fmt,
            filename=filename,
            sha256=sha256,
            retrieved_at=dt.datetime.now(dt.timezone.utc),
            account_id="UNKNOWN",
            status=ImportStatus.FAILED,
            warnings=[str(exc)],
        )
        db.add(record)
        db.commit()
        raise

    if parsed.account is None:
        raise ImportError_("No account information found; cannot determine account_id")
    account_id = parsed.account.account_id
    warnings = list(parsed.warnings)

    out_of_window = _find_out_of_window_dates([e.trade_date for e in parsed.executions])
    for d in sorted(out_of_window):
        warnings.append(f"Trade date {d.isoformat()} falls well outside the file's main date range (§16 test 16b)")

    trade_dates = [e.trade_date for e in parsed.executions]
    date_range = (min(trade_dates), max(trade_dates)) if trade_dates else None

    # Archive the payload before deriving anything from it (§3.2). A failure
    # is a warning, never a failed import: losing the archive copy is bad,
    # refusing data the user already has is worse.
    stored_path = store_payload(sha256, content)
    if stored_path is None:
        warnings.append(
            "Could not archive the original payload; a full rebuild from source "
            "will not be possible for this import (§3.2)."
        )

    record = ImportFile(
        source=source,
        source_format=fmt,
        filename=filename,
        sha256=sha256,
        retrieved_at=dt.datetime.now(dt.timezone.utc),
        account_id=account_id,
        period_start=parsed.period_start or (date_range[0] if date_range else None),
        period_end=parsed.period_end or (date_range[1] if date_range else None),
        row_count=len(parsed.executions) + len(parsed.positions),
        status=ImportStatus.SUCCESS,
        # A copy: `warnings` keeps being appended to below, and sharing the
        # object with the ORM attribute is what silently swallowed those
        # later entries. See the assignment further down.
        warnings=list(warnings),
        stored_path=stored_path,
    )
    db.add(record)
    db.flush()

    rows_parsed = {
        "executions": len(parsed.executions),
        "positions": len(parsed.positions),
        "closed_lots": len(parsed.closed_lots),
        "transfers": len(parsed.transfers),
        "cash_transactions": len(parsed.cash_transactions),
    }

    inserted, duplicate = _upsert_executions(db, parsed, record, account_id, out_of_window, warnings)
    _replace_position_snapshots(db, parsed, record, warnings)
    _replace_closed_lots(db, parsed, record, account_id, warnings)
    _replace_transfers(db, parsed, record, account_id, warnings)
    replace_flex_cash_transactions(db, parsed, account_id, warnings)
    # After the transfers are in place, never before: these mirror them.
    sync_transfer_flows(db, account_id)
    replace_option_events(db, parsed, record, account_id, warnings)
    replace_corporate_actions(db, parsed, record, account_id, warnings)
    # §11.3 — split factors are a derivation over raw executions, so they
    # are recomputed from the corporate-action table before trades are
    # built rather than written into the imported rows.
    apply_split_factors(db, account_id)

    db.flush()
    settings_row = get_settings_row(db)
    policy = settings_row.transfer_basis_policy
    # §0.3 — IBKR's own NAV for the epoch date, kept beside the stored value
    # so divergence stays visible. Only meaningful when the file's window
    # actually starts on the epoch, which is what the "fetch from IBKR"
    # button arranges; a routine 30-day sync starts somewhere else entirely.
    if (
        parsed.nav is not None
        and parsed.nav.starting_value is not None
        and settings_row.tracking_start_date is not None
        and parsed.period_start == settings_row.tracking_start_date
    ):
        settings_row.tracking_start_value_ibkr = parsed.nav.starting_value

    rebuild_trades(db, account_id, BasisResolver(db, account_id, policy))
    # §5.4 — linking needs the executions in place but not the trades, and
    # chains are built from trades, so the two straddle the rebuild.
    link_result = link_option_events(db, account_id)
    # Groups are about to be deleted wholesale, and option events point at
    # them. Release the references first or the rebuild trips the foreign
    # key; they are re-established when the chains are rebuilt below.
    detach_option_events_from_groups(db, account_id)
    rebuild_trade_groups(db, account_id)
    assignment_summary = rebuild_assignment_chains(db, account_id)
    # Journaling re-anchors here: trades were just deleted and re-derived,
    # so every tag and note keyed to one has to find its trade again (§11).
    reanchor_journal(db, account_id)
    # §5.4's journaling hook, after the chains it writes against exist.
    record_assignment_journal(db, account_id)
    rebuild_daily_snapshots(db, account_id)
    # §7.5 — replay the shadow portfolio over the rebuilt days. Reads only
    # cached `price_bars`; fetching happens on the sync schedule, so an
    # import with an empty cache simply leaves the benchmark unpriced
    # rather than reaching for the network mid-import.
    rebuild_benchmark_shadow(db, account_id)

    recon: ReconciliationResult = reconcile(db, account_id, record.id)
    for issue in recon.issues:
        warnings.append(f"[reconciliation:{issue.severity}] instrument={issue.instrument_id}: {issue.message}")

    lot_check: ClosedLotCheckResult | None = None
    if parsed.closed_lots:
        lot_check = check_closed_lots(db, account_id, policy)
        for issue in lot_check.issues:
            warnings.append(f"[closed_lot:{issue.severity}] {issue.message}")

    # A fresh list, and the constructor above took one too. The column is a
    # plain JSON that does not track in-place mutation, so when the record
    # shared this very object the ORM's committed state was mutated right
    # along with it: by the time of this assignment the two compared equal,
    # no UPDATE was emitted, and everything appended after the INSERT was
    # lost — the reconciliation and closed-lot issues, and the supersede
    # notes from _upsert_executions. That is how an import could go PARTIAL
    # without ever recording what was wrong with it.
    record.warnings = list(warnings)
    if not recon.passed or (lot_check is not None and not lot_check.passed):
        record.status = ImportStatus.PARTIAL
    db.commit()

    return ImportReport(
        import_file_id=record.id,
        account_id=account_id,
        status=record.status.value,
        source_format=fmt.value,
        rows_parsed=rows_parsed,
        rows_inserted=inserted,
        rows_duplicate=duplicate,
        date_range=date_range,
        warnings=warnings,
        reconciliation_passed=recon.passed,
        reconciliation_issues=[f"[{i.severity}] {i.message}" for i in recon.issues],
        closed_lot_check=lot_check.as_dict() if lot_check else None,
        assignment_alert=assignment_alert(db, account_id, link_result),
        assignment_check=assignment_summary["reconciliation"],
    )


def _canonical_dedupe_key(execution) -> str:
    """§3.2 — resolve in order: transactionID, ibExecID, then ibOrderID (the
    only key a `.tlg` row carries)."""
    return execution.transaction_id or execution.ib_exec_id or execution.broker_trade_id


def _upsert_executions(db, parsed, record, account_id, out_of_window, warnings) -> tuple[int, int]:
    existing = db.query(Execution).filter(Execution.account_id == account_id).all()
    seen_keys = {e.broker_trade_id for e in existing}
    by_order_id = {e.ib_order_id: e for e in existing if e.ib_order_id}

    inserted = duplicate = 0
    for pe in parsed.executions:
        key = _canonical_dedupe_key(pe)
        if key in seen_keys:
            duplicate += 1
            continue

        # A .tlg row and a Flex row for the same fill match only via
        # ibOrderID (§3.2). On collision, prefer the Flex rows and mark the
        # .tlg row superseded rather than inserting both — an order that was
        # partially filled produces several Flex rows under one ibOrderID.
        #
        # Only a *.tlg*-sourced prior can collide this way, which is what
        # `not prior.transaction_id` identifies. A prior that carries a
        # transactionID came from Flex, and a second Flex row under the same
        # ibOrderID is the next fill of a partially filled order, not a
        # duplicate — genuine repeats were already rejected by `seen_keys`,
        # which is keyed per fill. Treating those as duplicates silently
        # dropped every fill after the first, leaving the position short and
        # its later close stranded as a phantom opening trade.
        prior = by_order_id.get(pe.broker_trade_id)
        if prior is not None and not prior.transaction_id:
            if pe.transaction_id:
                db.delete(prior)
                by_order_id.pop(pe.broker_trade_id, None)
                seen_keys.discard(prior.broker_trade_id)
                warnings.append(
                    f"Order {pe.broker_trade_id}: superseded the .tlg-sourced row with the "
                    "richer Flex execution (§3.2)"
                )
            else:
                duplicate += 1
                continue

        instrument = get_or_create_instrument(db, pe.symbol, pe.multiplier, warnings)
        executed_at = localize(pe.trade_date, pe.trade_time)
        execution = Execution(
            import_file_id=record.id,
            source_line=pe.source_line,
            account_id=account_id,
            broker_trade_id=key,
            ib_order_id=pe.broker_trade_id,
            transaction_id=pe.transaction_id,
            ib_exec_id=pe.ib_exec_id,
            brokerage_order_id=pe.brokerage_order_id,
            instrument_id=instrument.id,
            action=Action(pe.action),
            flags=pe.flags,
            executed_at=executed_at,
            trading_day=derive_trading_day(executed_at, pe.symbol.asset_class),
            quantity=pe.quantity,
            multiplier=pe.multiplier,
            price=pe.price,
            proceeds=pe.proceeds,
            commission=pe.commission,
            fx_rate=pe.fx_rate,
            cash_flow=-pe.proceeds + pe.commission,
            exchange=pe.exchange,
            out_of_window_warning=pe.trade_date in out_of_window,
        )
        db.add(execution)
        db.flush()
        seen_keys.add(key)
        by_order_id[pe.broker_trade_id] = execution
        inserted += 1

    return inserted, duplicate


def _replace_position_snapshots(db, parsed, record, warnings) -> None:
    """Snapshots accumulate rather than replace.

    Each import's set is a dated picture of the book, and §12's marks turn
    that sequence into the unrealized-P&L series behind the account curve.
    Replacing would leave exactly one day of marks no matter how long the
    account had been syncing.
    """
    # A re-import of the same payload must not stack a second copy of the
    # same day's book on top of the first.
    if record.period_end is not None:
        db.query(BrokerPositionSnapshot).filter(
            BrokerPositionSnapshot.account_id == record.account_id,
            BrokerPositionSnapshot.snapshot_on == record.period_end,
        ).delete(synchronize_session=False)
        db.flush()

    for pp in parsed.positions:
        instrument = get_or_create_instrument(db, pp.symbol, pp.multiplier, warnings)
        db.add(
            BrokerPositionSnapshot(
                import_file_id=record.id,
                account_id=pp.account_id,
                instrument_id=instrument.id,
                asset_class=pp.symbol.asset_class,
                quantity=pp.quantity,
                multiplier=pp.multiplier,
                cost_basis_price=pp.cost_basis_price,
                cost_basis_money=pp.cost_basis_money,
                fx_rate=pp.fx_rate,
                mark_price=pp.mark_price,
                close_price=pp.close_price,
                unrealized_pnl=pp.unrealized_pnl,
                snapshot_on=record.period_end,
            )
        )


def _replace_closed_lots(db, parsed, record, account_id, warnings) -> None:
    if not parsed.closed_lots:
        return
    # Lots are a full restatement of IB's matching for the window, so replace
    # rather than accumulate; stale lots from an earlier overlapping window
    # would otherwise double-count in the check.
    db.query(ClosedLot).filter(ClosedLot.account_id == account_id).delete(synchronize_session=False)
    db.flush()
    for lot in parsed.closed_lots:
        instrument = get_or_create_instrument(db, lot.symbol, 1.0, warnings)
        db.add(
            ClosedLot(
                import_file_id=record.id,
                account_id=account_id,
                instrument_id=instrument.id,
                closing_broker_trade_id=lot.closing_broker_trade_id,
                opening_transaction_id=lot.opening_transaction_id,
                quantity=lot.quantity,
                trade_price=lot.trade_price,
                cost=lot.cost,
                fifo_pnl_realized=lot.fifo_pnl_realized,
                open_date=lot.open_datetime,
            )
        )


def _replace_transfers(db, parsed, record, account_id, warnings) -> None:
    if not parsed.transfers:
        return
    db.query(PositionTransfer).filter(PositionTransfer.account_id == account_id).delete(
        synchronize_session=False
    )
    db.flush()
    for transfer in parsed.transfers:
        instrument = get_or_create_instrument(db, transfer.symbol, 1.0, warnings)
        db.add(
            PositionTransfer(
                import_file_id=record.id,
                account_id=account_id,
                instrument_id=instrument.id,
                transferred_on=transfer.transferred_on,
                direction=transfer.direction,
                transfer_type=transfer.transfer_type,
                quantity=transfer.quantity,
                transfer_price=transfer.transfer_price,
                position_amount=transfer.position_amount,
                original_cost_basis=transfer.original_cost_basis,
                broker_transaction_id=transfer.broker_transaction_id,
            )
        )


def import_tlg_file(db: Session, filename: str, content: bytes) -> ImportReport:
    """Backwards-compatible entry point for the `.tlg` upload path."""
    return import_file(db, filename, content, source_format=SourceFormat.TLG)
