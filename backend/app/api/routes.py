import datetime as dt

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.cash.manual import (
    CashInputError,
    create_transaction,
    import_csv,
    parse_amount,
    parse_date,
    parse_type,
    serialize as serialize_cash,
)
from app.charts.calendar import build_calendar
from app.charts.dashboard import InvalidPanel, parse_layout, validate_panel
from app.charts.filters import ChartFilters, apply_filters, parse_csv
from app.charts.grid import UnknownAxis, axis_catalog, build_chart
from app.charts.trend import DEFAULT_WINDOW, UnknownMetric, build_distribution, build_trend
from app.charts.units import load_units
from app.db import get_db
from app.expiry_watch.service import build_expiry_watch
from app.config import get_settings as get_app_settings
from app.flex.client import FlexClient
from app.flex.errors import FlexError
from app.flex.sync import refresh_benchmark_prices, run_sync, sync_health
from app.importer.option_events import assignment_alert, serialize_event
from app.importer.pipeline import import_file, import_tlg_file
from app.journal.anchors import anchor_key_for_trade
from app.journal.manual import (
    ManualEntryError,
    create_manual_fill,
    create_manual_split,
    rebuild_after_manual_change,
    set_trade_levels,
)
from app.journal.service import (
    JournalError,
    get_or_create_tag,
    tag_day,
    tag_trade,
    tag_usage,
    untag_trade,
    write_note,
)
from app.importer.corporate_actions import apply_split_factors
from app.models import (
    CashSource,
    CashTransaction,
    CorporateAction,
    DailySnapshot,
    DayTag,
    DEFAULT_TAG_GROUPS,
    Execution,
    FlexSyncRun,
    Instrument,
    Note,
    NoteScope,
    OptionEvent,
    OptionEventType,
    RegimeMarker,
    SyncTrigger,
    Tag,
    Trade,
    TradeExecution,
    TradeGroup,
    TradeTag,
    GroupOverride,
)
from app.models.enums import AnalysisMode, LegRole, StrategyType, TradeStatus
from app.models.settings_row import (
    TrackingValueSource,
    TransferBasisPolicy,
    get_settings_row,
)
from app.parsers.flex import FlexParseError, parse_flex
from app.parsers.tlg import TlgParseError
from app.performance.epoch import resolve_epoch
from app.performance.service import CurveVariant, build_performance, rebuild_benchmark_shadow
from app.stats.engine import DayStat, TradeStat, compute_pnl_summary, compute_ratios
from app.stats.groups import compute_segmented_risk_stats
from app.stats.source import trade_stats_for_mode
from app.stats.snapshots import rebuild_daily_snapshots
from app.trades.basis import BasisResolver
from app.trades.constructor import rebuild_trades
from app.trades.grouping import rebuild_trade_groups
from app.trading_day import EASTERN

router = APIRouter()


def _et(value: dt.datetime | None) -> str | None:
    """§14.1 — store tz-aware, display in America/New_York. Postgres hands
    back timestamptz in UTC, so serializing it raw would show a futures fill
    at 18:07 ET as 22:07 and disagree with the broker statement."""
    return value.astimezone(EASTERN).isoformat() if value else None


def _resolve_account(db: Session, account_id: str | None) -> str:
    if account_id:
        return account_id
    row = db.query(Execution.account_id).first()
    if not row:
        raise HTTPException(404, "No data imported yet")
    return row[0]


@router.get("/sync/health")
def read_sync_health(db: Session = Depends(get_db)):
    """§3.8 — backs the staleness banner. A dashboard over stale data is
    worse than no dashboard, because it looks authoritative."""
    return sync_health(db).as_dict()


@router.post("/sync/run")
def trigger_sync(db: Session = Depends(get_db)):
    """§3.8 — the on-demand "Sync now" path.

    Credentials come from the environment, never the database (§3.10): the
    token is a bearer credential granting read access to the full statement
    history, and it travels as a query parameter, so the fewer places it is
    written down the better.
    """
    settings = get_app_settings()
    if not settings.flex_token or not settings.flex_query_id:
        raise HTTPException(
            400,
            "Flex credentials are not configured. Set SETTLED_FLEX_TOKEN and "
            "SETTLED_FLEX_QUERY_ID in the environment (see §3.6 of the spec).",
        )

    with FlexClient(settings.flex_token, settings.flex_query_id) as client:
        result = run_sync(db, client, trigger=SyncTrigger.MANUAL, token=settings.flex_token)

    return {
        "run_id": result.run_id,
        "status": result.status.value,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "needs_attention": result.needs_attention,
        "report": _serialize_report(result.report) if result.report else None,
    }


@router.get("/sync/runs")
def list_sync_runs(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    rows = db.query(FlexSyncRun).order_by(FlexSyncRun.started_at.desc()).limit(limit).all()
    return {
        "items": [
            {
                "id": r.id,
                "started_at": _et(r.started_at),
                "finished_at": _et(r.finished_at),
                "trigger": r.trigger.value,
                "status": r.status.value,
                "window_from": r.window_from.isoformat() if r.window_from else None,
                "window_to": r.window_to.isoformat() if r.window_to else None,
                "attempt_count": r.attempt_count,
                "error_code": r.error_code,
                "error_message": r.error_message_redacted,
                "rows_ingested": r.rows_ingested,
                "rows_new": r.rows_new,
            }
            for r in rows
        ]
    }


@router.get("/groups")
def list_groups(account_id: str | None = None, db: Session = Depends(get_db)):
    """§6 — strategies, not legs. A vertical's per-leg numbers are
    individually meaningless; this is the unit that has a result."""
    account = _resolve_account(db, account_id)
    rows = (
        db.query(TradeGroup)
        .filter(TradeGroup.account_id == account)
        .order_by(TradeGroup.opened_at.desc())
        .all()
    )
    return {
        "total": len(rows),
        "items": [
            {
                "id": g.id,
                "strategy_type": g.strategy_type.value,
                "book": g.book,
                "underlying": g.underlying_root,
                "opened_at": _et(g.opened_at),
                "closed_at": _et(g.closed_at),
                "leg_count": g.leg_count,
                "dte_at_open": g.dte_at_open,
                "net_debit_credit": float(g.net_debit_credit),
                # null means unbounded, and the UI must render it that way
                # rather than as a blank cell (§9.5).
                "max_risk": float(g.max_risk) if g.max_risk is not None else None,
                "gross_pnl": float(g.gross_pnl),
                "commissions": float(g.commissions),
                "net_pnl": float(g.net_pnl),
                "return_on_risk": float(g.return_on_risk) if g.return_on_risk is not None else None,
                # §5.4 — an assigned group carries two capital figures and
                # two durations. Neither pair is ever collapsed into one:
                # the at-open risk stopped applying at assignment, and a
                # combined duration describes neither leg.
                "assigned": g.assigned,
                "parent_group_id": g.parent_group_id,
                "post_assignment_capital": (
                    float(g.post_assignment_capital)
                    if g.post_assignment_capital is not None
                    else None
                ),
                "return_on_post_assignment_capital": (
                    float(g.return_on_post_assignment_capital)
                    if g.return_on_post_assignment_capital is not None
                    else None
                ),
                "option_duration_seconds": (
                    float(g.option_duration_seconds)
                    if g.option_duration_seconds is not None
                    else None
                ),
                "underlying_duration_seconds": (
                    float(g.underlying_duration_seconds)
                    if g.underlying_duration_seconds is not None
                    else None
                ),
                "detection_source": g.detection_source,
                "legs": [
                    {
                        "trade_id": t.id,
                        "symbol": t.instrument.broker_symbol,
                        "side": t.side.value,
                        "strike": float(t.instrument.strike) if t.instrument.strike is not None else None,
                        "right": t.instrument.right.value if t.instrument.right else None,
                        "net_pnl": float(t.net_pnl),
                    }
                    for t in g.legs
                ],
            }
            for g in rows
        ],
    }


@router.post("/groups/override")
def create_group_override(
    payload: dict, account_id: str | None = None, db: Session = Depends(get_db)
):
    """§6.4 — "select N trades -> Group as strategy -> pick type", and the
    ungroup counterpart.

    Stored against the broker's own execution identifiers, not trade ids:
    trades are fully re-derived on every import and none of their ids
    survive, so a trade-id-keyed override would break on the next sync.
    """
    account = _resolve_account(db, account_id)
    trade_ids = payload.get("trade_ids") or []
    ungroup = bool(payload.get("ungroup"))
    strategy = payload.get("strategy_type") or StrategyType.CUSTOM.value

    if len(trade_ids) < 1:
        raise HTTPException(400, "Select at least one trade")
    if not ungroup and len(trade_ids) < 2:
        raise HTTPException(400, "Grouping needs at least two trades; use ungroup for one")

    try:
        parsed_strategy = StrategyType(strategy)
    except ValueError:
        raise HTTPException(400, f"Unknown strategy type {strategy!r}") from None

    identifiers: list[str] = []
    for trade_id in trade_ids:
        trade = db.get(Trade, trade_id)
        if trade is None or trade.account_id != account:
            raise HTTPException(404, f"Trade {trade_id} not found")
        for leg in trade.legs:
            if leg.leg_role == LegRole.OPEN and leg.execution_id is not None:
                execution = db.get(Execution, leg.execution_id)
                if execution is not None:
                    identifiers.append(execution.transaction_id or execution.broker_trade_id)

    if not identifiers:
        raise HTTPException(
            400,
            "Those trades have no real opening executions to key an override on "
            "(a synthesized opening leg cannot anchor one).",
        )

    member_key = GroupOverride.build_member_key(identifiers)
    existing = (
        db.query(GroupOverride)
        .filter(GroupOverride.account_id == account, GroupOverride.member_key == member_key)
        .one_or_none()
    )
    if existing is not None:
        existing.strategy_type = parsed_strategy
        existing.ungroup = ungroup
        override = existing
    else:
        override = GroupOverride(
            account_id=account,
            member_key=member_key,
            strategy_type=parsed_strategy,
            ungroup=ungroup,
            note=payload.get("note"),
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        db.add(override)
    db.flush()

    rebuild_trade_groups(db, account)
    db.commit()
    return {"id": override.id, "strategy_type": override.strategy_type.value, "ungroup": override.ungroup}


@router.get("/groups/overrides")
def list_group_overrides(account_id: str | None = None, db: Session = Depends(get_db)):
    account = _resolve_account(db, account_id)
    rows = db.query(GroupOverride).filter(GroupOverride.account_id == account).all()
    return {
        "items": [
            {
                "id": o.id,
                "member_key": o.member_key,
                "strategy_type": o.strategy_type.value,
                "ungroup": o.ungroup,
                "note": o.note,
                "created_at": _et(o.created_at),
            }
            for o in rows
        ]
    }


@router.delete("/groups/override/{override_id}")
def delete_group_override(override_id: int, db: Session = Depends(get_db)):
    override = db.get(GroupOverride, override_id)
    if override is None:
        raise HTTPException(404, "Override not found")
    account = override.account_id
    db.delete(override)
    db.flush()
    rebuild_trade_groups(db, account)
    db.commit()
    return {"deleted": override_id}


@router.get("/stats/risk")
def read_risk_stats(
    account_id: str | None = None, book: str | None = None, db: Session = Depends(get_db)
):
    """§6.3 — segmented by strategy type, never pooled."""
    account = _resolve_account(db, account_id)
    return compute_segmented_risk_stats(db, account, book=book).as_dict()


def _resolve_mode(db: Session, mode: str | None) -> AnalysisMode:
    if mode:
        try:
            return AnalysisMode(mode.upper())
        except ValueError:
            raise HTTPException(400, f"Unknown analysis mode {mode!r}") from None
    row = get_settings_row(db)
    db.commit()
    return row.analysis_mode


@router.put("/settings/analysis-mode")
def set_analysis_mode(mode: str, db: Session = Depends(get_db)):
    """§6.4 — global: every chart, stat and table respects it."""
    try:
        parsed = AnalysisMode(mode.upper())
    except ValueError:
        raise HTTPException(400, f"Unknown analysis mode {mode!r}") from None
    row = get_settings_row(db)
    row.analysis_mode = parsed
    db.commit()
    return {"analysis_mode": parsed.value}


@router.get("/settings")
def read_settings(db: Session = Depends(get_db)):
    row = get_settings_row(db)
    db.commit()
    app_settings = get_app_settings()
    return {
        "transfer_basis_policy": row.transfer_basis_policy.value,
        "analysis_mode": row.analysis_mode.value,
        "default_book": row.default_book,
        "benchmark_symbol": row.benchmark_symbol,
        "tracking_start_date": (
            row.tracking_start_date.isoformat() if row.tracking_start_date else None
        ),
        "tracking_start_value": (
            float(row.tracking_start_value) if row.tracking_start_value is not None else None
        ),
        "tracking_start_value_source": row.tracking_start_value_source.value,
        "tracking_start_value_ibkr": (
            float(row.tracking_start_value_ibkr)
            if row.tracking_start_value_ibkr is not None
            else None
        ),
        "price_source": app_settings.price_source,
        "price_fetch_enabled": app_settings.price_fetch_enabled,
        "transfer_basis_note": (
            "TRANSFER_MARK measures decisions made in this account and matches IBKR's own "
            "performance methodology, which backs pre-transfer gains out via "
            "transferredPnlAdjustments. ORIGINAL_COST measures the total economic result of "
            "the holding and makes trade P&L agree with IBKR's fifoPnlRealized."
        ),
        "epoch_note": (
            "Performance is measured from the tracking start date, not from account "
            "funding — early experimentation would otherwise contaminate every statistic "
            "with no way to un-mix it later. Without a starting value, percentage returns "
            "are withheld rather than computed against an assumed zero (§0.3)."
        ),
    }


@router.put("/settings/transfer-basis-policy")
def set_transfer_basis_policy(policy: str, db: Session = Depends(get_db)):
    """Changing the policy re-derives every trade from raw executions."""
    try:
        parsed = TransferBasisPolicy(policy)
    except ValueError:
        raise HTTPException(400, f"Unknown policy {policy!r}") from None

    row = get_settings_row(db)
    row.transfer_basis_policy = parsed
    db.flush()

    account = db.query(Execution.account_id).first()
    if account:
        # Groups are built from trades, so they must be rebuilt whenever
        # trades are. Skipping this leaves every group orphaned with no legs
        # and every trade ungrouped — silently emptying the strategy views.
        rebuild_trades(db, account[0], BasisResolver(db, account[0], parsed))
        rebuild_trade_groups(db, account[0])
        rebuild_daily_snapshots(db, account[0])
    db.commit()
    return {"transfer_basis_policy": parsed.value, "rebuilt": bool(account)}


@router.post("/import/tlg")
async def upload_tlg(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Retained for the `.tlg`-only path; `/import` autodetects either format."""
    content = await file.read()
    try:
        report = import_tlg_file(db, file.filename or "upload.tlg", content)
    except TlgParseError as exc:
        raise HTTPException(400, f"Parse error: {exc}") from exc
    return _serialize_report(report)


@router.post("/import")
async def upload_any(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Accepts either a `.tlg` TradeLog or an Activity Flex XML; the format is
    detected from the content, and the pipeline is shared from parsing on."""
    content = await file.read()
    try:
        report = import_file(db, file.filename or "upload", content)
    except (TlgParseError, FlexParseError) as exc:
        raise HTTPException(400, f"Parse error: {exc}") from exc
    return _serialize_report(report)


def _serialize_report(report) -> dict:
    return {
        "import_file_id": report.import_file_id,
        "account_id": report.account_id,
        "status": report.status,
        "source_format": report.source_format,
        "rows_parsed": report.rows_parsed,
        "rows_inserted": report.rows_inserted,
        "rows_duplicate": report.rows_duplicate,
        "date_range": [d.isoformat() for d in report.date_range] if report.date_range else None,
        "warnings": report.warnings,
        "reconciliation": {
            "passed": report.reconciliation_passed,
            "issues": report.reconciliation_issues,
        },
        "closed_lot_check": report.closed_lot_check,
        # §5.4 — surfaced at the top of the report, not buried in warnings.
        "assignment_alert": report.assignment_alert,
        "assignment_check": report.assignment_check,
    }


@router.get("/executions")
def list_executions(
    account_id: str | None = None,
    limit: int = Query(500, le=5000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    account = _resolve_account(db, account_id)
    rows = (
        db.query(Execution)
        .filter(Execution.account_id == account)
        .order_by(Execution.executed_at, Execution.broker_trade_id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = db.query(Execution).filter(Execution.account_id == account).count()
    return {
        "total": total,
        "items": [
            {
                "id": e.id,
                "broker_trade_id": e.broker_trade_id,
                "symbol": e.instrument.broker_symbol,
                "asset_class": e.instrument.asset_class.value,
                "action": e.action.value,
                "flags": e.flags,
                "executed_at": _et(e.executed_at),
                "trading_day": e.trading_day.isoformat(),
                "quantity": float(e.quantity),
                "multiplier": float(e.multiplier),
                "price": float(e.price),
                "proceeds": float(e.proceeds),
                "commission": float(e.commission),
                "cash_flow": float(e.cash_flow),
                "exchange": e.exchange,
                "out_of_window_warning": e.out_of_window_warning,
                # §5.4 — the other half of an assignment, linked both ways.
                "related_execution_id": e.related_execution_id,
                # §11.3 — a hand-entered fill must never read as a broker
                # record; the grid shows this distinctly (§4.5's rule).
                "source": e.source,
                "split_factor": float(e.split_factor or 1),
            }
            for e in rows
        ],
    }


@router.get("/trades")
def list_trades(account_id: str | None = None, db: Session = Depends(get_db)):
    account = _resolve_account(db, account_id)
    rows = db.query(Trade).filter(Trade.account_id == account).order_by(Trade.opened_at).all()
    policy = get_settings_row(db).transfer_basis_policy
    db.commit()
    synthetic_trade_ids = {
        te.trade_id
        for te in db.query(TradeExecution).filter(TradeExecution.synthetic.is_(True)).all()
    }
    return {
        "total": len(rows),
        # §14.3 — the active policy travels with the data, so a transferred
        # trade's P&L is never read without knowing which basis produced it.
        "transfer_basis_policy": policy.value,
        "items": [
            {
                "id": t.id,
                "symbol": t.instrument.broker_symbol,
                "underlying": t.instrument.underlying_root,
                "asset_class": t.instrument.asset_class.value,
                "side": t.side.value,
                "status": t.status.value,
                "origin": t.origin.value,
                "book": t.book,
                "strategy_type": t.strategy_type.value if t.strategy_type else None,
                "opened_at": _et(t.opened_at),
                "closed_at": _et(t.closed_at),
                "duration_seconds": float(t.duration_seconds) if t.duration_seconds is not None else None,
                "open_qty": float(t.open_qty),
                "avg_open_price": float(t.avg_open_price) if t.avg_open_price is not None else None,
                "avg_close_price": float(t.avg_close_price) if t.avg_close_price is not None else None,
                "gross_pnl": float(t.gross_pnl),
                "commissions": float(t.commissions),
                "net_pnl": float(t.net_pnl),
                "return_pct": float(t.return_pct) if t.return_pct is not None else None,
                "pnl_points": float(t.pnl_points) if t.pnl_points is not None else None,
                "pnl_ticks": float(t.pnl_ticks) if t.pnl_ticks is not None else None,
                "excluded_from_win_rate": t.excluded_from_win_rate,
                "excluded_from_duration": t.excluded_from_duration,
                "execution_count": t.execution_count,
                "trade_group_id": t.trade_group_id,
                "has_synthetic_leg": t.id in synthetic_trade_ids,
            }
            for t in rows
        ],
    }


def _load_stat_inputs(db: Session, account: str, book: str | None, mode: AnalysisMode):
    trade_stats = trade_stats_for_mode(db, account, mode, book)
    snapshots = (
        db.query(DailySnapshot)
        .filter(DailySnapshot.account_id == account)
        .order_by(DailySnapshot.trading_day)
        .all()
    )
    day_stats = [
        DayStat(
            trading_day=s.trading_day,
            realized_net=float(s.realized_net),
            realized_gross=float(s.realized_gross),
            cumulative_net_pnl=float(s.cumulative_net_pnl),
        )
        for s in snapshots
    ]
    return trade_stats, day_stats


@router.get("/stats")
def get_stats(
    account_id: str | None = None,
    book: str | None = Query(
        "Options Income",
        description="§6.5 — defaults to Options Income so a buy-and-hold position cannot "
        "silently dominate options statistics. Pass an empty string for the pooled view.",
    ),
    mode: str | None = Query(
        None,
        description="§6.4 — STRATEGY or LEG. Defaults to the stored setting. Totals are "
        "identical either way; win rate, expectancy, counts and duration are not.",
    ),
    db: Session = Depends(get_db),
):
    account = _resolve_account(db, account_id)
    effective_book = book or None
    effective_mode = _resolve_mode(db, mode)
    trade_stats, day_stats = _load_stat_inputs(db, account, effective_book, effective_mode)
    return {
        "account_id": account,
        "book": effective_book or "ALL",
        "analysis_mode": effective_mode.value,
        # Stated alongside the numbers, because the same account produces
        # two different win rates depending on this and neither is wrong.
        "analysis_mode_note": (
            "Counting whole strategies: a vertical is one outcome."
            if effective_mode == AnalysisMode.STRATEGY
            else "Counting individual legs: a vertical is one win and one loss."
        ),
        "unit_count": len(trade_stats),
        "pnl": compute_pnl_summary(trade_stats, day_stats),
        "ratios": compute_ratios(trade_stats, day_stats),
    }


@router.get("/equity-curve")
def equity_curve(account_id: str | None = None, db: Session = Depends(get_db)):
    """§9.3 #1 — cumulative net P&L.

    Still the realized-P&L series, not the account equity curve. The account
    curve, its variants and the return figures live at `/performance`
    (§7.2-7.4), which needs the epoch anchor this endpoint does not.
    """
    account = _resolve_account(db, account_id)
    rows = (
        db.query(DailySnapshot)
        .filter(DailySnapshot.account_id == account)
        .order_by(DailySnapshot.trading_day)
        .all()
    )
    return {
        "series": "cumulative_net_pnl",
        "items": [
            {
                "trading_day": s.trading_day.isoformat(),
                "realized_net": float(s.realized_net),
                "cumulative_net_pnl": float(s.cumulative_net_pnl),
                "cash_flow": float(s.cash_flow),
                "account_value": float(s.account_value),
                "drawdown": float(s.drawdown),
                "drawdown_pct": float(s.drawdown_pct),
                "trade_count": s.trade_count,
                "win_count": s.win_count,
                "loss_count": s.loss_count,
            }
            for s in rows
        ],
    }


# ---------------------------------------------------------------------------
# §7 — account-level performance: equity curve, TWR/XIRR/CAGR, benchmark
# ---------------------------------------------------------------------------


@router.get("/performance")
def performance(
    account_id: str | None = None,
    variant: str = Query(
        CurveVariant.WITH_FLOWS.value,
        description="§7.4 — PNL_ONLY, WITH_FLOWS (default, the §7.2 account curve) or "
        "WITH_INCOME (adds dividends, interest and fees).",
    ),
    benchmark: str | None = Query(None, description="§7.5 — defaults to the stored benchmark symbol."),
    db: Session = Depends(get_db),
):
    account = _resolve_account(db, account_id)
    try:
        parsed_variant = CurveVariant(variant.upper())
    except ValueError:
        raise HTTPException(
            400,
            f"Unknown curve variant {variant!r}; expected one of "
            + ", ".join(v.value for v in CurveVariant),
        ) from None
    body = build_performance(db, account, parsed_variant, benchmark)
    db.commit()
    return body


@router.post("/performance/benchmark/refresh")
def refresh_benchmark(account_id: str | None = None, db: Session = Depends(get_db)):
    """§7.5 — top up `price_bars` on demand and replay the shadow portfolio.

    The same code the nightly sync runs. Never raises on a provider
    failure: it reports one, because a dead price API must not look like a
    broken app.
    """
    account = _resolve_account(db, account_id)
    result = refresh_benchmark_prices(db, account)
    db.commit()
    return result or {"skipped": "nothing to do"}


# ---------------------------------------------------------------------------
# §7.1 — cash transactions
# ---------------------------------------------------------------------------


@router.get("/cash-transactions")
def list_cash_transactions(
    account_id: str | None = None,
    source: str | None = Query(None, description="FLEX or MANUAL"),
    db: Session = Depends(get_db),
):
    account = _resolve_account(db, account_id)
    query = db.query(CashTransaction).filter(CashTransaction.account_id == account)
    if source:
        try:
            query = query.filter(CashTransaction.source == CashSource(source.upper()))
        except ValueError:
            raise HTTPException(400, f"Unknown source {source!r}; expected FLEX or MANUAL") from None

    rows = query.order_by(CashTransaction.occurred_on.desc(), CashTransaction.id.desc()).all()
    totals: dict[str, float] = {}
    for row in rows:
        totals[row.type.value] = totals.get(row.type.value, 0.0) + float(row.amount)

    return {
        "total": len(rows),
        "totals_by_type": totals,
        "net_external_flow": sum(float(r.amount) for r in rows if r.is_external_flow),
        "items": [serialize_cash(r) for r in rows],
        "note": (
            "Deposits positive, withdrawals negative (§7.1). MANUAL rows survive every "
            "resync; FLEX rows are restated from the broker on each import."
        ),
    }


@router.post("/cash-transactions")
def create_cash_transaction(payload: dict, account_id: str | None = None, db: Session = Depends(get_db)):
    account = _resolve_account(db, account_id)
    try:
        row = create_transaction(
            db,
            account,
            occurred_on=parse_date(payload.get("occurred_on") or payload.get("date") or ""),
            kind=parse_type(payload.get("type") or ""),
            amount=parse_amount(payload.get("amount")),
            note=payload.get("note"),
            currency=(payload.get("currency") or "USD").upper(),
        )
    except CashInputError as exc:
        raise HTTPException(400, str(exc)) from None

    _recompute_cash_dependent(db, account)
    db.commit()
    return serialize_cash(row)


@router.put("/cash-transactions/{transaction_id}")
def update_cash_transaction(transaction_id: int, payload: dict, db: Session = Depends(get_db)):
    row = db.get(CashTransaction, transaction_id)
    if row is None:
        raise HTTPException(404, "Cash transaction not found")
    if row.source != CashSource.MANUAL:
        # Editing a FLEX row in place would be undone by the next resync
        # without warning. Refusing is the honest answer: enter a manual
        # correction, which is the mechanism built for exactly this (§7.1).
        raise HTTPException(
            409,
            "This row came from IBKR and the next sync would overwrite an edit. Add a "
            "manual adjustment instead — manual rows survive every resync (§7.1).",
        )

    try:
        if "occurred_on" in payload or "date" in payload:
            row.occurred_on = parse_date(payload.get("occurred_on") or payload.get("date"))
        if "type" in payload:
            row.type = parse_type(payload["type"])
        if "amount" in payload:
            row.amount = parse_amount(payload["amount"])
    except CashInputError as exc:
        raise HTTPException(400, str(exc)) from None
    if "note" in payload:
        row.note = payload["note"]

    db.flush()
    _recompute_cash_dependent(db, row.account_id)
    db.commit()
    return serialize_cash(row)


@router.delete("/cash-transactions/{transaction_id}")
def delete_cash_transaction(transaction_id: int, db: Session = Depends(get_db)):
    row = db.get(CashTransaction, transaction_id)
    if row is None:
        raise HTTPException(404, "Cash transaction not found")
    if row.source != CashSource.MANUAL:
        raise HTTPException(
            409,
            "This row came from IBKR; the next sync would restore it. Enter an offsetting "
            "manual adjustment instead (§7.1).",
        )
    account = row.account_id
    db.delete(row)
    db.flush()
    _recompute_cash_dependent(db, account)
    db.commit()
    return {"deleted": transaction_id}


@router.post("/cash-transactions/csv")
def paste_cash_csv(payload: dict, account_id: str | None = None, db: Session = Depends(get_db)):
    """§7.1 — the CSV paste path, for pre-Flex history."""
    account = _resolve_account(db, account_id)
    result = import_csv(db, account, payload.get("text") or "")
    if result.inserted:
        _recompute_cash_dependent(db, account)
    db.commit()
    return result.as_dict()


def _recompute_cash_dependent(db: Session, account_id: str) -> None:
    """A cash change moves the equity curve, so the derived series is rebuilt.

    Trades are untouched: cash transactions carry no executions, and
    rebuilding them here would be a second of work to produce identical
    rows.
    """
    rebuild_daily_snapshots(db, account_id)
    rebuild_benchmark_shadow(db, account_id)


# ---------------------------------------------------------------------------
# §0.3 — the performance epoch
# ---------------------------------------------------------------------------


@router.get("/settings/epoch")
def read_epoch(account_id: str | None = None, db: Session = Depends(get_db)):
    account = db.query(Execution.account_id).first()
    epoch = resolve_epoch(db, account[0] if account else (account_id or ""))
    row = get_settings_row(db)
    body = epoch.as_dict()
    body["tracking_start_value_source"] = row.tracking_start_value_source.value
    db.commit()
    return body


@router.put("/settings/epoch")
def set_epoch(payload: dict, db: Session = Depends(get_db)):
    """§0.3 — changing the epoch is a **full rebuild**, not an update.

    Trades are re-derived because pre-epoch classification changes which
    ones count; snapshots and the shadow portfolio follow. Cheap at this
    data volume, and it is what keeps the setting genuinely reversible
    rather than a one-way door.
    """
    row = get_settings_row(db)

    if "tracking_start_date" in payload:
        raw = payload["tracking_start_date"]
        if raw in (None, ""):
            row.tracking_start_date = None
        else:
            try:
                row.tracking_start_date = parse_date(raw)
            except CashInputError as exc:
                raise HTTPException(400, str(exc)) from None

    if "tracking_start_value" in payload:
        raw = payload["tracking_start_value"]
        if raw in (None, ""):
            row.tracking_start_value = None
        else:
            try:
                row.tracking_start_value = parse_amount(raw)
            except CashInputError as exc:
                raise HTTPException(400, str(exc)) from None
        source = (payload.get("tracking_start_value_source") or "MANUAL").upper()
        try:
            row.tracking_start_value_source = TrackingValueSource(source)
        except ValueError:
            raise HTTPException(400, f"Unknown value source {source!r}") from None

    db.flush()

    account = db.query(Execution.account_id).first()
    rebuilt = False
    if account:
        policy = row.transfer_basis_policy
        rebuild_trades(db, account[0], BasisResolver(db, account[0], policy))
        rebuild_trade_groups(db, account[0])
        rebuild_daily_snapshots(db, account[0])
        rebuild_benchmark_shadow(db, account[0])
        rebuilt = True

    epoch = resolve_epoch(db, account[0] if account else "")
    body = epoch.as_dict()
    body["rebuilt"] = rebuilt
    body["tracking_start_value_source"] = row.tracking_start_value_source.value
    body["rebuild_note"] = (
        "Trades, daily snapshots and the benchmark shadow portfolio were re-derived from "
        "raw executions (§0.3)."
        if rebuilt
        else "No executions imported yet, so there was nothing to rebuild."
    )
    db.commit()
    return body


@router.post("/settings/epoch/fetch-value")
def fetch_epoch_value(payload: dict | None = None, db: Session = Depends(get_db)):
    """§0.3 — "fetch from IBKR" rather than typing a NAV.

    A date-overridden Flex request returns `ChangeInNAV.startingValue` for
    exactly the epoch date. Typing a NAV by hand is an error vector that
    silently skews every percentage in the app, so the button exists and
    the fetched figure is also kept as the permanent cross-check.
    """
    row = get_settings_row(db)
    payload = payload or {}
    target = payload.get("date")
    if target:
        try:
            epoch_date = parse_date(target)
        except CashInputError as exc:
            raise HTTPException(400, str(exc)) from None
    else:
        epoch_date = row.tracking_start_date
    if epoch_date is None:
        raise HTTPException(400, "Set a tracking start date first, or pass one as `date`.")

    settings = get_app_settings()
    if not settings.flex_token or not settings.flex_query_id:
        raise HTTPException(
            400,
            "Flex credentials are not configured. Set SETTLED_FLEX_TOKEN and "
            "SETTLED_FLEX_QUERY_ID in the environment (see §3.6 of the spec).",
        )

    stamp = epoch_date.strftime("%Y%m%d")
    try:
        with FlexClient(settings.flex_token, settings.flex_query_id) as client:
            xml, _reference, _attempts = client.fetch(from_date=stamp, to_date=stamp)
    except FlexError as exc:
        # Unlike the sync runner, which records a failed run and returns it,
        # this path had no handler at all — the FlexError escaped as a plain
        # 500 whose body is the text "Internal Server Error", and the caller
        # trying to read that as JSON reported a parse error instead of the
        # cause. §3.8 classifies these; the classification is worth carrying
        # through so a credential problem does not read like an outage.
        raise HTTPException(
            502,
            f"IBKR rejected the request for {epoch_date.isoformat()} "
            f"[{exc.error_class.value}"
            + (f", code {exc.code}" if exc.code else "")
            + f"]: {exc}. A `1003` here is most likely the query refusing the "
            "date override (§3.9), which this request cannot work without — it "
            "needs one specific past day, and a relative period cannot name "
            "one. The sync survives the same rejection by falling back to the "
            "query's own period, so a working sync does not mean this will "
            "work. Enter the NAV by hand instead; `python -m app.flex.diagnose` "
            "confirms the cause.",
        ) from None

    parsed = parse_flex(xml)
    if parsed.nav is None or parsed.nav.starting_value is None:
        raise HTTPException(
            502,
            "IBKR returned no ChangeInNAV.startingValue for that date. Confirm the Flex "
            "query includes the Change in NAV section (§3.9).",
        )

    row.tracking_start_value_ibkr = parsed.nav.starting_value
    if payload.get("apply", True):
        row.tracking_start_value = parsed.nav.starting_value
        row.tracking_start_value_source = TrackingValueSource.FETCHED
    db.flush()

    account = db.query(Execution.account_id).first()
    if account:
        rebuild_daily_snapshots(db, account[0])
        rebuild_benchmark_shadow(db, account[0])

    body = resolve_epoch(db, account[0] if account else "").as_dict()
    body["fetched_value"] = parsed.nav.starting_value
    body["applied"] = bool(payload.get("apply", True))
    db.commit()
    return body


# ---------------------------------------------------------------------------
# §9 — the chart grid, global filters, calendar
# ---------------------------------------------------------------------------


def _filters_from_query(
    date_from: dt.date | None,
    date_to: dt.date | None,
    books: str | None,
    symbols: str | None,
    underlyings: str | None,
    asset_classes: str | None,
    strategy_types: str | None,
    sides: str | None,
    filter_dimension: str | None,
    filter_keys: str | None,
) -> ChartFilters:
    return ChartFilters(
        date_from=date_from,
        date_to=date_to,
        books=parse_csv(books),
        symbols=parse_csv(symbols),
        underlyings=parse_csv(underlyings),
        asset_classes=parse_csv(asset_classes),
        strategy_types=parse_csv(strategy_types),
        sides=parse_csv(sides),
        dimension=filter_dimension,
        dimension_keys=parse_csv(filter_keys),
    )


@router.get("/charts/axes")
def chart_axes():
    """§9.1-9.2 — what the two dropdowns offer. Any metric × any dimension."""
    return axis_catalog()


@router.get("/charts")
def chart(
    dimension: str = Query("day_of_week", description="§9.1 — the x-axis."),
    metric: str = Query("net_pnl", description="§9.2 — the y-axis."),
    account_id: str | None = None,
    mode: str | None = None,
    limit: int | None = Query(None, description="Keep the top and bottom N buckets by value (§9.3 #8)."),
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    books: str | None = None,
    symbols: str | None = None,
    underlyings: str | None = None,
    asset_classes: str | None = None,
    strategy_types: str | None = None,
    sides: str | None = None,
    filter_dimension: str | None = None,
    filter_keys: str | None = None,
    db: Session = Depends(get_db),
):
    account = _resolve_account(db, account_id)
    effective_mode = _resolve_mode(db, mode)
    filters = _filters_from_query(
        date_from, date_to, books, symbols, underlyings, asset_classes,
        strategy_types, sides, filter_dimension, filter_keys,
    )

    units = apply_filters(load_units(db, account, effective_mode), filters)
    try:
        body = build_chart(units, dimension, metric, limit=limit)
    except UnknownAxis as exc:
        raise HTTPException(400, str(exc)) from None

    body["analysis_mode"] = effective_mode.value
    body["filters"] = filters.as_dict()
    return body


@router.get("/charts/filter-options")
def chart_filter_options(
    account_id: str | None = None, mode: str | None = None, db: Session = Depends(get_db)
):
    """The distinct values each global filter can take, from the data itself."""
    account = _resolve_account(db, account_id)
    units = load_units(db, account, _resolve_mode(db, mode))

    def distinct(attr: str) -> list[str]:
        return sorted({getattr(u, attr) for u in units if getattr(u, attr)})

    days = [u.trading_day for u in units if u.trading_day]
    return {
        "books": distinct("book"),
        "symbols": distinct("symbol"),
        "underlyings": distinct("underlying"),
        "asset_classes": distinct("asset_class"),
        "strategy_types": distinct("strategy_type"),
        "sides": distinct("side"),
        "date_from": min(days).isoformat() if days else None,
        "date_to": max(days).isoformat() if days else None,
    }


@router.get("/calendar")
def calendar_heatmap(
    account_id: str | None = None,
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
):
    """§9.3 #3 — the monthly P&L calendar heatmap."""
    account = _resolve_account(db, account_id)
    if month is not None and year is None:
        raise HTTPException(400, "Pass `year` alongside `month`.")
    if month is not None and not 1 <= month <= 12:
        raise HTTPException(400, "`month` must be between 1 and 12.")
    return build_calendar(db, account, year, month)


@router.get("/expiry-watch")
def expiry_watch(
    account_id: str | None = None,
    as_of: dt.date | None = None,
    db: Session = Depends(get_db),
):
    account = _resolve_account(db, account_id)
    effective_as_of = as_of or dt.date.today()
    rows = build_expiry_watch(db, account, effective_as_of)
    return {
        "as_of": effective_as_of.isoformat(),
        "disclaimer": (
            "Forecast of a mechanical consequence only. Reports what happens if a position "
            "finishes in the money; emits no probability, signal, or suggested action. "
            "In-the-money status settles on a price fixing this app cannot observe (§5.4)."
        ),
        "items": [
            {
                "instrument_id": r.instrument_id,
                "symbol": r.symbol,
                "underlying": r.underlying,
                "asset_class": r.asset_class,
                "book": r.book,
                "quantity": r.quantity,
                "strike": r.strike,
                "right": r.right,
                "expiry": r.expiry.isoformat() if r.expiry else None,
                "dte_calendar": r.dte_calendar,
                "dte_trading": r.dte_trading,
                "exercise_style": r.exercise_style,
                "projection": r.projection,
                "projection_notional": r.projection_notional,
                "projection_cash_required": r.projection_cash_required,
                "reference_price": r.reference_price,
                "reference_price_as_of": (
                    r.reference_price_as_of.isoformat() if r.reference_price_as_of else None
                ),
                "reference_price_source": r.reference_price_source,
                "distance_to_strike": r.distance_to_strike,
                "distance_to_strike_pct": r.distance_to_strike_pct,
                "moneyness": r.moneyness,
                "warnings": r.warnings,
            }
            for r in rows
        ],
    }


@router.get("/positions")
def open_positions(account_id: str | None = None, db: Session = Depends(get_db)):
    from app.importer.reconciliation import compute_open_positions

    account = _resolve_account(db, account_id)
    positions = compute_open_positions(db, account)
    items = []
    for instrument_id, pos in positions.items():
        if abs(pos.quantity) < 1e-9:
            continue
        instrument = db.get(Instrument, instrument_id)
        items.append(
            {
                "instrument_id": instrument_id,
                "symbol": instrument.broker_symbol,
                "description": instrument.description,
                "asset_class": instrument.asset_class.value,
                "quantity": pos.quantity,
                "cost_basis_price": pos.cost_basis_price,
                "cost_basis_money": pos.cost_basis_money,
            }
        )
    return {"items": items}


# ---------------------------------------------------------------------------
# §5.4 — assignments, exercises and expirations
# ---------------------------------------------------------------------------


@router.get("/option-events")
def list_option_events(
    account_id: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db),
):
    account = _resolve_account(db, account_id)
    query = db.query(OptionEvent).filter(OptionEvent.account_id == account)
    if event_type:
        try:
            query = query.filter(OptionEvent.event_type == OptionEventType(event_type.upper()))
        except ValueError:
            raise HTTPException(400, f"Unknown event type {event_type!r}") from None

    rows = query.order_by(OptionEvent.occurred_on.desc(), OptionEvent.id).all()
    return {
        "total": len(rows),
        "items": [serialize_event(db, e) for e in rows],
        "disclaimer": (
            "What the broker reported, never what this app calculated should have happened. "
            "In-the-money status settles on a price fixing the app cannot observe, so the "
            "broker's records are the only authority (§5.4)."
        ),
    }


@router.get("/option-events/alerts")
def option_event_alerts(account_id: str | None = None, db: Session = Depends(get_db)):
    """§5.4 — backs the persistent dashboard banner."""
    account = _resolve_account(db, account_id)
    return assignment_alert(db, account)


@router.post("/option-events/{event_id}/acknowledge")
def acknowledge_option_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(OptionEvent, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    event.acknowledged = True
    event.acknowledged_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return {"id": event.id, "acknowledged": True}


# ---------------------------------------------------------------------------
# §11.1-11.2 — tags and notes
# ---------------------------------------------------------------------------


@router.get("/tags")
def list_tags(account_id: str | None = None, db: Session = Depends(get_db)):
    account = _resolve_account(db, account_id)
    usage = tag_usage(db, account)
    rows = db.query(Tag).filter(Tag.account_id == account).order_by(Tag.group_name, Tag.name).all()
    return {
        "total": len(rows),
        "groups": sorted({r.group_name for r in rows if r.group_name} | set(DEFAULT_TAG_GROUPS)),
        "items": [r.as_dict(usage.get(r.id, 0)) for r in rows],
    }


@router.post("/tags")
def create_tag(payload: dict, account_id: str | None = None, db: Session = Depends(get_db)):
    account = _resolve_account(db, account_id)
    try:
        tag = get_or_create_tag(
            db, account, payload.get("name") or "", payload.get("group_name"), payload.get("color")
        )
    except JournalError as exc:
        raise HTTPException(400, str(exc)) from None
    db.commit()
    return tag.as_dict(0)


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, "Tag not found")
    if tag.system:
        # §5.4 applies `assigned` automatically. Deleting it would silently
        # empty a filter the user believes is complete, and the next import
        # would recreate it anyway.
        raise HTTPException(
            409,
            "This tag is applied automatically by the app and cannot be deleted; the next "
            "import would recreate it.",
        )
    db.query(TradeTag).filter(TradeTag.tag_id == tag_id).delete(synchronize_session=False)
    db.query(DayTag).filter(DayTag.tag_id == tag_id).delete(synchronize_session=False)
    db.delete(tag)
    db.commit()
    return {"deleted": tag_id}


@router.get("/notes")
def list_notes(
    account_id: str | None = None,
    scope: str | None = None,
    scope_ref: str | None = None,
    q: str | None = Query(None, description="§11.2 — full-text search over note bodies."),
    db: Session = Depends(get_db),
):
    account = _resolve_account(db, account_id)
    query = db.query(Note).filter(Note.account_id == account)
    if scope:
        try:
            query = query.filter(Note.scope == NoteScope(scope.upper()))
        except ValueError:
            raise HTTPException(400, f"Unknown note scope {scope!r}") from None
    if scope_ref:
        query = query.filter(Note.scope_ref == scope_ref)
    if q:
        query = query.filter(Note.body_md.ilike(f"%{q}%"))

    rows = query.order_by(Note.updated_at.desc()).all()
    return {"total": len(rows), "items": [r.as_dict() for r in rows]}


@router.post("/notes")
def create_note(payload: dict, account_id: str | None = None, db: Session = Depends(get_db)):
    account = _resolve_account(db, account_id)
    try:
        scope = NoteScope((payload.get("scope") or "GENERAL").upper())
    except ValueError:
        raise HTTPException(400, f"Unknown note scope {payload.get('scope')!r}") from None

    scope_ref = payload.get("scope_ref")
    anchor = None
    if scope == NoteScope.TRADE:
        trade = db.get(Trade, int(scope_ref)) if scope_ref else None
        if trade is None or trade.account_id != account:
            raise HTTPException(404, f"Trade {scope_ref} not found")
        anchor = anchor_key_for_trade(db, trade)
        if anchor is None:
            raise HTTPException(
                400,
                "This trade's opening leg is synthetic, so a note on it would be lost on the "
                "next import. Note the day or the strategy group instead (§5.5).",
            )

    note = write_note(db, account, scope, scope_ref, payload.get("body_md") or "", anchor_key=anchor)
    db.commit()
    return note.as_dict()


@router.put("/notes/{note_id}")
def update_note(note_id: int, payload: dict, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if note is None:
        raise HTTPException(404, "Note not found")
    if "body_md" in payload:
        note.body_md = payload["body_md"]
        # Editing an auto-draft makes it the user's note (§5.4).
        note.draft = 0
    note.updated_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return note.as_dict()


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if note is None:
        raise HTTPException(404, "Note not found")
    db.delete(note)
    db.commit()
    return {"deleted": note_id}


# ---------------------------------------------------------------------------
# §11.1 — applying tags, and §10.3 bulk operations
# ---------------------------------------------------------------------------


def _require_trade(db: Session, account: str, trade_id: int) -> Trade:
    trade = db.get(Trade, trade_id)
    if trade is None or trade.account_id != account:
        raise HTTPException(404, f"Trade {trade_id} not found")
    return trade


@router.post("/trades/{trade_id}/tags")
def add_trade_tag(
    trade_id: int, payload: dict, account_id: str | None = None, db: Session = Depends(get_db)
):
    account = _resolve_account(db, account_id)
    trade = _require_trade(db, account, trade_id)
    try:
        tag = get_or_create_tag(
            db, account, payload.get("name") or "", payload.get("group_name"), payload.get("color")
        )
        tag_trade(db, account, trade, tag)
    except JournalError as exc:
        raise HTTPException(400, str(exc)) from None
    db.commit()
    return {"trade_id": trade_id, "tag": tag.as_dict()}


@router.delete("/trades/{trade_id}/tags/{tag_id}")
def remove_trade_tag(
    trade_id: int, tag_id: int, account_id: str | None = None, db: Session = Depends(get_db)
):
    account = _resolve_account(db, account_id)
    trade = _require_trade(db, account, trade_id)
    removed = untag_trade(db, account, trade, tag_id)
    db.commit()
    return {"trade_id": trade_id, "tag_id": tag_id, "removed": removed}


@router.post("/days/{trading_day}/tags")
def add_day_tag(
    trading_day: dt.date, payload: dict, account_id: str | None = None, db: Session = Depends(get_db)
):
    account = _resolve_account(db, account_id)
    try:
        tag = get_or_create_tag(
            db, account, payload.get("name") or "", payload.get("group_name"), payload.get("color")
        )
    except JournalError as exc:
        raise HTTPException(400, str(exc)) from None
    tag_day(db, account, trading_day, tag)
    db.commit()
    return {"trading_day": trading_day.isoformat(), "tag": tag.as_dict()}


@router.get("/days/{trading_day}/tags")
def list_day_tags(
    trading_day: dt.date, account_id: str | None = None, db: Session = Depends(get_db)
):
    account = _resolve_account(db, account_id)
    rows = (
        db.query(DayTag)
        .filter(DayTag.account_id == account, DayTag.trading_day == trading_day)
        .all()
    )
    return {"items": [r.tag.as_dict() for r in rows]}


@router.post("/trades/bulk")
def bulk_trade_operation(
    payload: dict, account_id: str | None = None, db: Session = Depends(get_db)
):
    """§10.3 — "Multi-select → add/remove tags, set stop/target, add note".

    One endpoint rather than N, because the interesting part is the
    selection and every action shares it. Each action is applied to every
    selected trade or the whole call fails: a bulk edit that half-applied
    would leave the user unable to tell which half.
    """
    account = _resolve_account(db, account_id)
    trade_ids = payload.get("trade_ids") or []
    action = (payload.get("action") or "").lower()
    if not trade_ids:
        raise HTTPException(400, "Select at least one trade")

    trades = [_require_trade(db, account, tid) for tid in trade_ids]
    applied, skipped = 0, []

    try:
        if action == "add_tag":
            tag = get_or_create_tag(
                db, account, payload.get("name") or "", payload.get("group_name")
            )
            for trade in trades:
                try:
                    tag_trade(db, account, trade, tag)
                    applied += 1
                except JournalError as exc:
                    skipped.append({"trade_id": trade.id, "reason": str(exc)})

        elif action == "remove_tag":
            tag_id = payload.get("tag_id")
            if tag_id is None:
                raise HTTPException(400, "remove_tag needs a tag_id")
            for trade in trades:
                if untag_trade(db, account, trade, int(tag_id)):
                    applied += 1

        elif action == "set_levels":
            for trade in trades:
                try:
                    set_trade_levels(
                        db,
                        account,
                        trade,
                        stop_loss=payload.get("stop_loss"),
                        profit_target=payload.get("profit_target"),
                    )
                    applied += 1
                except JournalError as exc:
                    skipped.append({"trade_id": trade.id, "reason": str(exc)})

        elif action == "add_note":
            body = payload.get("body_md") or ""
            for trade in trades:
                anchor = anchor_key_for_trade(db, trade)
                if anchor is None:
                    skipped.append(
                        {
                            "trade_id": trade.id,
                            "reason": "Synthetic opening leg — a note here would not survive "
                            "the next import (§5.5).",
                        }
                    )
                    continue
                write_note(db, account, NoteScope.TRADE, str(trade.id), body, anchor_key=anchor)
                applied += 1

        else:
            raise HTTPException(
                400,
                f"Unknown bulk action {action!r}. Expected add_tag, remove_tag, set_levels or "
                "add_note.",
            )
    except JournalError as exc:
        raise HTTPException(400, str(exc)) from None

    db.commit()
    return {
        "action": action,
        "selected": len(trades),
        "applied": applied,
        "skipped": skipped,
        "note": (
            "Trades whose opening leg is synthetic cannot carry journal data — there is no "
            "broker identifier to anchor it to, so it would vanish on the next import."
            if skipped
            else None
        ),
    }


# ---------------------------------------------------------------------------
# §11.3 — stop/target, manual trade entry, corporate actions
# ---------------------------------------------------------------------------


@router.put("/trades/{trade_id}/levels")
def set_levels(
    trade_id: int, payload: dict, account_id: str | None = None, db: Session = Depends(get_db)
):
    """§11.3 — stop loss and profit target, which unlock R-value."""
    account = _resolve_account(db, account_id)
    trade = _require_trade(db, account, trade_id)
    try:
        annotation = set_trade_levels(
            db,
            account,
            trade,
            stop_loss=payload.get("stop_loss"),
            profit_target=payload.get("profit_target"),
        )
    except JournalError as exc:
        raise HTTPException(400, str(exc)) from None
    db.commit()
    return {
        "trade_id": trade_id,
        "stop_loss": float(annotation.stop_loss) if annotation.stop_loss is not None else None,
        "profit_target": (
            float(annotation.profit_target) if annotation.profit_target is not None else None
        ),
        "note": "Stored against the broker's execution ids, so it survives every rebuild (§11.3).",
    }


@router.post("/executions/manual")
def create_manual_execution(
    payload: dict, account_id: str | None = None, db: Session = Depends(get_db)
):
    """§11.3 — "Manual trade entry for anything not in a `.tlg`".

    Marked `source = MANUAL`, which the importer never deletes and the
    executions grid renders distinctly. §4.5's rule applies: a
    reconstructed row must never be mistaken for a broker record.
    """
    account = _resolve_account(db, account_id)
    try:
        execution = create_manual_fill(db, account, payload)
    except ManualEntryError as exc:
        raise HTTPException(400, str(exc)) from None

    rebuild_after_manual_change(db, account)
    db.commit()
    return {
        "id": execution.id,
        "broker_trade_id": execution.broker_trade_id,
        "source": execution.source,
        "note": (
            "Entered by hand and marked MANUAL. It survives every resync and is shown "
            "distinctly in the executions grid so it is never read as a broker fill (§11.3)."
        ),
    }


@router.delete("/executions/manual/{execution_id}")
def delete_manual_execution(execution_id: int, db: Session = Depends(get_db)):
    execution = db.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(404, "Execution not found")
    if execution.source != "MANUAL":
        raise HTTPException(
            409,
            "This row came from the broker. Executions are immutable once imported (§3.2) — "
            "the audit trail is the point.",
        )
    account = execution.account_id
    db.delete(execution)
    db.flush()
    rebuild_after_manual_change(db, account)
    db.commit()
    return {"deleted": execution_id}


@router.get("/corporate-actions")
def list_corporate_actions(account_id: str | None = None, db: Session = Depends(get_db)):
    account = _resolve_account(db, account_id)
    rows = (
        db.query(CorporateAction)
        .filter(CorporateAction.account_id == account)
        .order_by(CorporateAction.occurred_on.desc())
        .all()
    )
    return {
        "total": len(rows),
        "items": [
            {
                "id": r.id,
                "occurred_on": r.occurred_on.isoformat(),
                "symbol": r.instrument.broker_symbol if r.instrument else None,
                "action_type": r.action_type,
                "description": r.description,
                "split_ratio": float(r.split_ratio) if r.split_ratio is not None else None,
                "applied": r.applied,
                "source": r.source,
            }
            for r in rows
        ],
        "note": (
            "Only splits are applied. Everything else is recorded and left alone: a "
            "half-applied spin-off looks like a P&L error weeks later with nothing pointing "
            "at the cause (§11.3)."
        ),
    }


@router.post("/corporate-actions")
def create_corporate_action(
    payload: dict, account_id: str | None = None, db: Session = Depends(get_db)
):
    """§11.3 — enter a split by hand, for one IB did not report or could not
    be parsed from its description."""
    account = _resolve_account(db, account_id)
    try:
        action = create_manual_split(db, account, payload)
    except ManualEntryError as exc:
        raise HTTPException(400, str(exc)) from None

    apply_split_factors(db, account)
    rebuild_after_manual_change(db, account)
    db.commit()
    return {
        "id": action.id,
        "split_ratio": float(action.split_ratio),
        "applied": action.applied,
        "note": (
            "Applied as a derivation over the raw executions, which are left exactly as the "
            "broker sent them. Delete the action to undo it (§3.2, §11.3)."
        ),
    }


@router.delete("/corporate-actions/{action_id}")
def delete_corporate_action(action_id: int, db: Session = Depends(get_db)):
    action = db.get(CorporateAction, action_id)
    if action is None:
        raise HTTPException(404, "Corporate action not found")
    if action.source != "MANUAL":
        raise HTTPException(
            409,
            "This action came from IBKR; the next sync would restore it. Enter a manual "
            "override instead.",
        )
    account = action.account_id
    db.delete(action)
    db.flush()
    apply_split_factors(db, account)
    rebuild_after_manual_change(db, account)
    db.commit()
    return {"deleted": action_id}


# ---------------------------------------------------------------------------
# §9.4 — trend analysis, and §6.6's regime markers
# ---------------------------------------------------------------------------


@router.get("/charts/trend")
def chart_trend(
    metric: str = Query("net_pnl", description="§9.2 — any metric from the grid."),
    window: int = Query(DEFAULT_WINDOW, ge=2, le=500),
    account_id: str | None = None,
    mode: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    books: str | None = None,
    symbols: str | None = None,
    underlyings: str | None = None,
    asset_classes: str | None = None,
    strategy_types: str | None = None,
    sides: str | None = None,
    db: Session = Depends(get_db),
):
    """§9.4 — any metric per-trade with a moving average.

    The view §6.6 leans on: a calendar axis spaces points by elapsed time,
    this one by decisions made, which is where a change in behaviour shows.
    """
    account = _resolve_account(db, account_id)
    effective_mode = _resolve_mode(db, mode)
    filters = _filters_from_query(
        date_from, date_to, books, symbols, underlyings, asset_classes,
        strategy_types, sides, None, None,
    )
    units = apply_filters(load_units(db, account, effective_mode), filters)
    try:
        body = build_trend(units, metric, window)
    except UnknownMetric as exc:
        raise HTTPException(400, str(exc)) from None

    body["analysis_mode"] = effective_mode.value
    body["filters"] = filters.as_dict()
    # §6.6 — the markers travel with any time-ordered series so the chart
    # can draw them without a second round trip.
    body["regime_markers"] = [m.as_dict() for m in _regime_markers(db, account)]
    return body


def _regime_markers(db: Session, account_id: str) -> list[RegimeMarker]:
    return (
        db.query(RegimeMarker)
        .filter(RegimeMarker.account_id == account_id)
        .order_by(RegimeMarker.occurred_on, RegimeMarker.sort_order)
        .all()
    )


@router.get("/regime-markers")
def list_regime_markers(account_id: str | None = None, db: Session = Depends(get_db)):
    account = _resolve_account(db, account_id)
    return {
        "items": [m.as_dict() for m in _regime_markers(db, account)],
        "note": (
            "§6.6 — the app deliberately does not detect regime changes; automatic "
            "detection is over-engineering for one user. These are yours to write down, "
            "and they render as a vertical line on every time-series chart."
        ),
    }


@router.post("/regime-markers")
def create_regime_marker(
    payload: dict, account_id: str | None = None, db: Session = Depends(get_db)
):
    account = _resolve_account(db, account_id)
    label = (payload.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "A marker needs a label — that is the whole point of it.")
    try:
        occurred_on = parse_date(payload.get("occurred_on") or payload.get("date") or "")
    except CashInputError as exc:
        raise HTTPException(400, str(exc)) from None

    marker = RegimeMarker(
        account_id=account,
        occurred_on=occurred_on,
        label=label[:120],
        note=payload.get("note"),
        color=payload.get("color"),
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    db.add(marker)
    db.commit()
    return marker.as_dict()


@router.delete("/regime-markers/{marker_id}")
def delete_regime_marker(marker_id: int, db: Session = Depends(get_db)):
    marker = db.get(RegimeMarker, marker_id)
    if marker is None:
        raise HTTPException(404, "Marker not found")
    db.delete(marker)
    db.commit()
    return {"deleted": marker_id}


# ---------------------------------------------------------------------------
# §9.3 / §14.6.1 — the dashboard layout
# ---------------------------------------------------------------------------


@router.get("/dashboard")
def read_dashboard(db: Session = Depends(get_db)):
    row = get_settings_row(db)
    layout = parse_layout(row.dashboard_layout)
    db.commit()
    return layout.as_dict()


@router.put("/dashboard")
def set_dashboard(payload: dict, db: Session = Depends(get_db)):
    """Save a curated layout (§14.6.1).

    Validated panel by panel so a typo is refused with the reason rather
    than stored and rendered as an empty box later.
    """
    panels = payload.get("panels")
    if not isinstance(panels, list) or not panels:
        raise HTTPException(400, "Send a non-empty `panels` list, or DELETE to reset.")
    try:
        validated = [validate_panel(p) for p in panels]
    except InvalidPanel as exc:
        raise HTTPException(400, str(exc)) from None

    row = get_settings_row(db)
    row.dashboard_layout = [p.as_dict() for p in validated]
    db.commit()
    return parse_layout(row.dashboard_layout).as_dict()


@router.delete("/dashboard")
def reset_dashboard(db: Session = Depends(get_db)):
    """Back to §9.3's default set."""
    row = get_settings_row(db)
    row.dashboard_layout = None
    db.commit()
    return parse_layout(None).as_dict()


@router.get("/charts/distribution")
def chart_distribution(
    metric: str = Query("net_pnl"),
    bins: int = Query(15, ge=3, le=60),
    account_id: str | None = None,
    mode: str | None = None,
    db: Session = Depends(get_db),
):
    """§9.3 #7 / §9.4 — a distribution histogram with a normal overlay."""
    account = _resolve_account(db, account_id)
    units = load_units(db, account, _resolve_mode(db, mode))
    try:
        return build_distribution(units, metric, bins)
    except UnknownMetric as exc:
        raise HTTPException(400, str(exc)) from None
