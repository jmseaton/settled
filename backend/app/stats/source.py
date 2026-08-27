"""§6.4 — the Analyze by Leg / Analyze by Strategy seam.

The statistics engine takes a list of `TradeStat` and knows nothing about
where they came from (§13: pure functions, no database access). So the
toggle is not a change to the engine at all — it is a change to which rows
get turned into `TradeStat`.

**Totals are invariant.** The sum of legs equals the sum of groups, so net
P&L, gross P&L and commissions are identical in both modes. What moves is
every *per-unit* figure — win rate, expectancy, average P&L, trade count,
duration — because a vertical is one outcome as a strategy and one win plus
one loss as legs. That is the entire point of the toggle, and it is why the
default is STRATEGY: the spread is the decision that was actually made.
"""

from sqlalchemy.orm import Session

from app.models.enums import AnalysisMode, StrategyType, TradeStatus
from app.models.trade import Trade
from app.models.trade_group import TradeGroup
from app.stats.engine import TradeStat
from app.trading_day import derive_trading_day


def _leg_stats(db: Session, account_id: str, book: str | None) -> list[TradeStat]:
    query = db.query(Trade).filter(Trade.account_id == account_id, Trade.closed_at.isnot(None))
    if book:
        query = query.filter(Trade.book == book)

    return [
        TradeStat(
            net_pnl=float(t.net_pnl),
            gross_pnl=float(t.gross_pnl),
            commissions=float(t.commissions),
            opened_at=t.opened_at,
            closed_at=t.closed_at,
            trading_day=derive_trading_day(t.closed_at, t.instrument.asset_class),
            excluded_from_win_rate=t.excluded_from_win_rate,
            return_pct=float(t.return_pct) if t.return_pct is not None else None,
        )
        for t in query.all()
        if t.status in (TradeStatus.CLOSED, TradeStatus.ORPHAN_CLOSE)
    ]


def _strategy_stats(db: Session, account_id: str, book: str | None) -> list[TradeStat]:
    query = db.query(TradeGroup).filter(
        TradeGroup.account_id == account_id, TradeGroup.closed_at.isnot(None)
    )
    if book:
        query = query.filter(TradeGroup.book == book)

    stats: list[TradeStat] = []
    for group in query.all():
        # §5.4 — a chain is a second view over trades that already belong to
        # their own spreads. Counting it as another strategy would add its
        # legs' P&L to the totals twice. It currently owns no legs, so the
        # check below would skip it anyway; stated explicitly because
        # relying on that would make a future change to chain membership
        # silently double the headline P&L.
        if group.strategy_type == StrategyType.ASSIGNMENT_CHAIN:
            continue
        if not group.legs:
            continue
        # A group inherits its legs' exclusions: if every leg is excluded
        # from win rate (an orphan close, say), so is the group. Otherwise a
        # transferred position would reappear in strategy mode having been
        # correctly excluded in leg mode.
        excluded = all(leg.excluded_from_win_rate for leg in group.legs)
        cost_basis = sum(float(leg.cost_basis or 0) for leg in group.legs)
        net = float(group.net_pnl)

        stats.append(
            TradeStat(
                net_pnl=net,
                gross_pnl=float(group.gross_pnl),
                commissions=float(group.commissions),
                opened_at=group.opened_at,
                closed_at=group.closed_at,
                trading_day=derive_trading_day(group.closed_at, group.legs[0].instrument.asset_class),
                excluded_from_win_rate=excluded,
                return_pct=(net / cost_basis) if cost_basis else None,
            )
        )
    return stats


def trade_stats_for_mode(
    db: Session, account_id: str, mode: AnalysisMode, book: str | None = None
) -> list[TradeStat]:
    if mode == AnalysisMode.LEG:
        return _leg_stats(db, account_id, book)
    return _strategy_stats(db, account_id, book)
