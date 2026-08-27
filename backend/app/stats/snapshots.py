"""§4.11 — `daily_snapshots`, materialized and rebuilt on every import.

Backs the equity curve (§7.2), the calendar heatmap (§9.3 #3), the drawdown
chart (§9.4) and every daily-frequency ratio. Full rebuild rather than
incremental update: at a few thousand executions a year it takes under a
second and eliminates an entire category of incremental-update bugs (§13).

Two decisions here shape everything read off this table.

**Rows exist for every trading day in the measured period**, not only days
with activity. A series built from activity days alone compresses the
x-axis, turns a quiet month into a fast one, and hands Sharpe a return
series with all its zeroes deleted — which inflates it.

**External flows and income are kept apart.** Deposits, withdrawals and
position transfers are `cash_flow`: money crossing the account boundary,
which TWR must strip out (§7.3). Dividends, interest and fees are
`income_cash`: they change the account's value without anyone moving money,
so they are returns. Merging the two would either erase dividends from
performance or credit deposits as gains.
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.market_calendar import HOLIDAY_RULES_VALID_FROM, is_trading_day
from app.models.cash_transaction import (
    EXTERNAL_FLOW_TYPES,
    INCOME_TYPES,
    CashTransaction,
)
from app.models.enums import LegRole, Origin, TradeStatus
from app.models.execution import Execution
from app.models.import_file import ImportFile
from app.models.instrument import Instrument
from app.models.position_snapshot import BrokerPositionSnapshot
from app.models.snapshot import DailySnapshot
from app.models.trade import Trade, TradeExecution
from app.performance.epoch import Epoch, apply_epoch_to_trades, resolve_epoch
from app.trading_day import derive_trading_day


def rebuild_daily_snapshots(db: Session, account_id: str) -> list[DailySnapshot]:
    epoch = resolve_epoch(db, account_id)
    apply_epoch_to_trades(db, account_id, epoch)

    db.query(DailySnapshot).filter(DailySnapshot.account_id == account_id).delete(
        synchronize_session=False
    )
    db.flush()

    trade_days = _trade_days(db, account_id, epoch)
    flow_days, income_days = _cash_days(db, account_id, epoch)
    unrealized_days = _unrealized_days(db, account_id)
    calendar = _calendar(db, account_id, epoch, trade_days, flow_days, income_days)

    rows: list[DailySnapshot] = []
    cumulative = 0.0
    cumulative_realized = 0.0
    cumulative_flow = 0.0
    cumulative_income = 0.0
    peak = epoch.start_value if epoch.configured else 0.0

    for day in calendar:
        bucket = trade_days.get(day, _EMPTY)
        flow = flow_days.get(day, 0.0)
        income = income_days.get(day, 0.0)

        cumulative += bucket["net"]
        cumulative_realized += bucket["net"] + bucket["partial_net"]
        cumulative_flow += flow
        cumulative_income += income

        # §7.2 — `starting_balance + Σ(cash_flow ≤ t) + Σ(realized_net ≤ t)
        # [+ unrealized_t]`. Realized means every dollar actually banked,
        # partial closes included: the cash is in the account whether or not
        # the position it came from has finished. Income is excluded and
        # carried separately, so the base curve matches the spec's formula
        # and the "+ dividends" overlay (§7.4) stays a real alternative.
        #
        # Unrealized is a *level*, not a flow — the open book's mark on day
        # t, replaced each day rather than accumulated. Adding it
        # cumulatively would compound the same open position every day it
        # stayed open.
        unrealized = unrealized_days.get(day)
        account_value = (
            epoch.start_value + cumulative_flow + cumulative_realized + (unrealized or 0.0)
        )
        peak = max(peak, account_value)
        drawdown = peak - account_value

        row = DailySnapshot(
            account_id=account_id,
            trading_day=day,
            realized_gross=bucket["gross"],
            realized_net=bucket["net"],
            realized_partial_net=bucket["partial_net"],
            commissions=bucket["commissions"],
            trade_count=bucket["count"],
            win_count=bucket["win"],
            loss_count=bucket["loss"],
            volume=bucket["volume"],
            cumulative_net_pnl=cumulative,
            cash_flow=flow,
            income_cash=income,
            unrealized_pnl=unrealized or 0.0,
            unrealized_priced=unrealized is not None,
            account_value=account_value,
            drawdown=drawdown,
            drawdown_pct=(drawdown / peak) if peak > 0 else 0.0,
        )
        db.add(row)
        rows.append(row)

    db.flush()
    return rows


_EMPTY = {
    "gross": 0.0,
    "net": 0.0,
    "partial_net": 0.0,
    "commissions": 0.0,
    "count": 0,
    "win": 0,
    "loss": 0,
    "volume": 0.0,
}


def _trade_days(db: Session, account_id: str, epoch: Epoch) -> dict[dt.date, dict]:
    """Realized P&L per trading day, closed trades and partials kept apart.

    A completed round trip lands on its close date and counts toward the
    day's trade count, wins and losses. A position that has been *partly*
    closed has realized cash but no outcome yet, so its P&L lands on the
    day of its most recent closing execution and counts toward nothing
    else: including it in the win rate would score a position that is still
    running.
    """
    trades = (
        db.query(Trade)
        .filter(
            Trade.account_id == account_id,
            # §0.3 — pre-epoch trades are ingested for their cost basis and
            # excluded from every statistic and from the equity curve.
            Trade.origin != Origin.PRE_EPOCH,
        )
        .all()
    )

    by_day: dict[dt.date, dict] = {}
    partial_days = _last_close_days(db, account_id)

    for trade in trades:
        closed = trade.closed_at is not None and trade.status in (
            TradeStatus.CLOSED,
            TradeStatus.ORPHAN_CLOSE,
        )
        net_pnl = float(trade.net_pnl or 0)

        if closed:
            day = derive_trading_day(trade.closed_at, trade.instrument.asset_class)
        else:
            if not net_pnl:
                continue
            day = partial_days.get(trade.id)
            if day is None:
                continue

        if epoch.start_date is not None and day < epoch.start_date:
            continue

        bucket = by_day.setdefault(day, dict(_EMPTY))
        if not closed:
            bucket["partial_net"] += net_pnl
            continue

        bucket["gross"] += float(trade.gross_pnl or 0)
        bucket["net"] += net_pnl
        bucket["commissions"] += float(trade.commissions or 0)
        bucket["count"] += 1
        bucket["volume"] += float(trade.open_qty or 0)
        if net_pnl > 0:
            bucket["win"] += 1
        elif net_pnl < 0:
            bucket["loss"] += 1
    return by_day


def _last_close_days(db: Session, account_id: str) -> dict[int, dt.date]:
    """The trading day of each trade's most recent closing execution.

    Where a still-open position's realized P&L gets dated. Exact when there
    has been one partial close, which is the ordinary case; when there have
    been several it puts the whole realized amount on the latest of them
    rather than splitting it, because the split would need per-leg realized
    P&L that §4.5 does not carry. The period total is right either way, and
    the approximation is confined to positions that are still open.
    """
    rows = (
        db.query(TradeExecution.trade_id, Execution.executed_at, Instrument.asset_class)
        .join(Execution, TradeExecution.execution_id == Execution.id)
        .join(Instrument, Execution.instrument_id == Instrument.id)
        .join(Trade, TradeExecution.trade_id == Trade.id)
        .filter(Trade.account_id == account_id, TradeExecution.leg_role == LegRole.CLOSE)
        .all()
    )
    latest: dict[int, tuple] = {}
    for trade_id, executed_at, asset_class in rows:
        current = latest.get(trade_id)
        if current is None or executed_at > current[0]:
            latest[trade_id] = (executed_at, asset_class)
    return {
        trade_id: derive_trading_day(executed_at, asset_class)
        for trade_id, (executed_at, asset_class) in latest.items()
    }


def _unrealized_days(db: Session, account_id: str) -> dict[dt.date, float]:
    """Unrealized P&L per day, from the marks on each position snapshot (§12).

    Only days an actual snapshot covers get an entry. The value is
    deliberately **not** carried forward to later days: a stale mark held
    flat across a week looks like a week of zero market movement, which is a
    claim the data does not support. Days without a snapshot are
    realized-only and say so via `unrealized_priced`.

    IB's own `fifoPnlUnrealized` is preferred over recomputing
    `(mark − basis) × qty × multiplier`, because it already handles the
    basis conventions this app deliberately diverges from (§5.5, §5.4) and
    is the figure `ChangeInNAV` reconciles against.
    """
    rows = (
        db.query(BrokerPositionSnapshot)
        .filter(
            BrokerPositionSnapshot.account_id == account_id,
            BrokerPositionSnapshot.snapshot_on.isnot(None),
        )
        .all()
    )
    by_day: dict[dt.date, float] = {}
    for row in rows:
        if row.unrealized_pnl is None:
            continue
        by_day[row.snapshot_on] = by_day.get(row.snapshot_on, 0.0) + float(row.unrealized_pnl)
    return by_day


def _cash_days(
    db: Session, account_id: str, epoch: Epoch
) -> tuple[dict[dt.date, float], dict[dt.date, float]]:
    """External flows and income cash, bucketed by day.

    Flows dated before the epoch are stored but excluded: the epoch value
    already embeds them (§7.1), so counting them again would double the
    account's starting capital.
    """
    rows = (
        db.query(CashTransaction)
        .filter(CashTransaction.account_id == account_id)
        .all()
    )
    flows: dict[dt.date, float] = {}
    income: dict[dt.date, float] = {}
    for row in rows:
        if epoch.start_date is not None and row.occurred_on < epoch.start_date:
            continue
        day = row.occurred_on
        amount = float(row.amount)
        if row.type in EXTERNAL_FLOW_TYPES:
            flows[day] = flows.get(day, 0.0) + amount
        elif row.type in INCOME_TYPES:
            income[day] = income.get(day, 0.0) + amount
    return flows, income


def _calendar(
    db: Session,
    account_id: str,
    epoch: Epoch,
    trade_days: dict[dt.date, dict],
    flow_days: dict[dt.date, float],
    income_days: dict[dt.date, float],
) -> list[dt.date]:
    """Every trading day from the epoch through the last day covered by data.

    "Last day covered" is the newest import's `period_end`, not the last day
    something happened. A month with no trades is still a month the strategy
    was measured over, and dropping it would make a flat stretch invisible
    on the curve and absent from the return series.
    """
    activity = set(trade_days) | set(flow_days) | set(income_days)
    start = epoch.start_date or (min(activity) if activity else None)
    if start is None:
        return []

    last_import_end = (
        db.query(ImportFile.period_end)
        .filter(ImportFile.account_id == account_id, ImportFile.period_end.isnot(None))
        .order_by(ImportFile.period_end.desc())
        .first()
    )
    candidates = [d for d in (max(activity) if activity else None,) if d is not None]
    if last_import_end and last_import_end[0]:
        candidates.append(last_import_end[0])
    if not candidates:
        return []
    end = max(candidates)
    if end < start:
        return []

    days: list[dt.date] = []
    cursor = start
    while cursor <= end:
        # The holiday rules refuse to answer for years before Juneteenth was
        # added (§ market_calendar). Rather than fail the whole rebuild on
        # old data, fall back to "any activity day counts".
        if cursor.year < HOLIDAY_RULES_VALID_FROM:
            if cursor in activity:
                days.append(cursor)
        elif is_trading_day(cursor) or cursor in activity:
            # An activity day the calendar calls closed is kept: the broker
            # recorded something, so the calendar is the thing that is
            # wrong here, not the data.
            days.append(cursor)
        cursor += dt.timedelta(days=1)
    return days
