"""The unit a chart aggregates over, and how it is loaded.

§9.2's "any metric × any dimension = the chart grid" only works if every
dimension can be read off one flat record. `ChartUnit` is that record: a
closed trade or a closed strategy group, flattened to exactly the fields
§9.1 groups by and §9.2 measures.

The leg/strategy toggle is honoured here rather than in the aggregation
(§6.4), for the same reason the statistics engine does it: the maths does
not change, only which rows become units. Totals are invariant across the
toggle; every per-unit figure is not.
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.enums import AnalysisMode, Origin, StrategyType, TradeStatus
from app.models.trade import Trade
from app.models.trade_group import TradeGroup
from app.trading_day import derive_trading_day


@dataclass
class ChartUnit:
    unit_id: int
    #: "trade" or "group" — the drill-down target type, so the UI knows
    #: which grid to open when a data point is clicked (§9).
    unit_kind: str
    account_id: str
    symbol: str | None
    underlying: str | None
    asset_class: str | None
    strategy_type: str | None
    book: str | None
    side: str | None
    status: str
    origin: str

    opened_at: dt.datetime | None
    closed_at: dt.datetime | None
    trading_day: dt.date | None
    duration_seconds: float | None

    quantity: float
    multiplier: float
    open_price: float | None
    stop_loss: float | None
    net_pnl: float
    gross_pnl: float
    commissions: float
    cost_basis: float | None
    return_pct: float | None
    max_risk: float | None
    return_on_risk: float | None
    r_value: float | None
    dte_at_open: int | None

    excluded_from_win_rate: bool
    excluded_from_duration: bool
    straddles_epoch: bool
    #: §9.1's `tag` and `tag_group` dimensions. A unit lands in *every*
    #: bucket it is tagged with, which makes tag charts the one place
    #: buckets legitimately overlap — the totals across a tag chart do not
    #: sum to the period total, and the chart says so.
    tags: list[str] = field(default_factory=list)
    tag_groups: list[str] = field(default_factory=list)


def _trade_unit(trade: Trade) -> ChartUnit:
    instrument = trade.instrument
    return ChartUnit(
        unit_id=trade.id,
        unit_kind="trade",
        account_id=trade.account_id,
        symbol=instrument.broker_symbol,
        underlying=instrument.underlying_root or instrument.root_symbol,
        asset_class=instrument.asset_class.value,
        strategy_type=trade.strategy_type.value if trade.strategy_type else None,
        book=trade.book,
        side=trade.side.value,
        status=trade.status.value,
        origin=trade.origin.value,
        opened_at=trade.opened_at,
        closed_at=trade.closed_at,
        trading_day=(
            derive_trading_day(trade.closed_at, instrument.asset_class)
            if trade.closed_at
            else None
        ),
        duration_seconds=(
            float(trade.duration_seconds) if trade.duration_seconds is not None else None
        ),
        quantity=float(trade.open_qty or 0),
        multiplier=float(instrument.multiplier or 1),
        open_price=float(trade.avg_open_price) if trade.avg_open_price is not None else None,
        stop_loss=None,
        net_pnl=float(trade.net_pnl or 0),
        gross_pnl=float(trade.gross_pnl or 0),
        commissions=float(trade.commissions or 0),
        cost_basis=float(trade.cost_basis) if trade.cost_basis is not None else None,
        return_pct=float(trade.return_pct) if trade.return_pct is not None else None,
        # Max risk is a property of the strategy, not of one leg: a short
        # put's risk depends entirely on whether a long put sits under it
        # (§6.3). Left null in leg mode rather than guessed.
        max_risk=None,
        return_on_risk=None,
        # §11.3 — filled in by `_attach_annotations` once a stop exists.
        # Without one there is no R to measure against, and inventing a
        # denominator is worse than reporting nothing.
        r_value=None,
        dte_at_open=_dte_at_open(trade.opened_at, instrument.expiry),
        excluded_from_win_rate=trade.excluded_from_win_rate,
        excluded_from_duration=trade.excluded_from_duration,
        straddles_epoch=bool(trade.straddles_epoch),
    )


def _group_unit(group: TradeGroup) -> ChartUnit:
    legs = group.legs
    first = legs[0] if legs else None
    asset_class = first.instrument.asset_class if first else None
    cost_basis = sum(float(leg.cost_basis or 0) for leg in legs) or None
    net = float(group.net_pnl or 0)

    return ChartUnit(
        unit_id=group.id,
        unit_kind="group",
        account_id=group.account_id,
        symbol=first.instrument.broker_symbol if first else None,
        underlying=group.underlying_root,
        asset_class=asset_class.value if asset_class else None,
        strategy_type=group.strategy_type.value,
        book=group.book,
        # A spread has no single side; forcing one would put a short put
        # vertical in the same bucket as a naked long call.
        side=first.side.value if len(legs) == 1 and first else None,
        status=TradeStatus.CLOSED.value if group.closed_at else TradeStatus.OPEN.value,
        origin=first.origin.value if first else Origin.TRADED.value,
        opened_at=group.opened_at,
        closed_at=group.closed_at,
        trading_day=(
            derive_trading_day(group.closed_at, asset_class)
            if group.closed_at and asset_class
            else None
        ),
        duration_seconds=(
            (group.closed_at - group.opened_at).total_seconds()
            if group.closed_at and group.opened_at
            else None
        ),
        quantity=sum(float(leg.open_qty or 0) for leg in legs),
        multiplier=float(first.instrument.multiplier or 1) if first is not None else 1.0,
        open_price=float(first.avg_open_price)
        if first is not None and first.avg_open_price is not None
        else None,
        stop_loss=None,
        net_pnl=net,
        gross_pnl=float(group.gross_pnl or 0),
        commissions=float(group.commissions or 0),
        cost_basis=cost_basis,
        return_pct=(net / cost_basis) if cost_basis else None,
        max_risk=float(group.max_risk) if group.max_risk is not None else None,
        return_on_risk=(
            float(group.return_on_risk) if group.return_on_risk is not None else None
        ),
        r_value=None,
        dte_at_open=group.dte_at_open,
        excluded_from_win_rate=all(leg.excluded_from_win_rate for leg in legs) if legs else True,
        excluded_from_duration=any(leg.excluded_from_duration for leg in legs),
        straddles_epoch=any(bool(leg.straddles_epoch) for leg in legs),
    )


def _dte_at_open(opened_at: dt.datetime | None, expiry: dt.date | None) -> int | None:
    if opened_at is None or expiry is None:
        return None
    return (expiry - opened_at.date()).days


def _r_value(unit: ChartUnit, stop_loss: float) -> float | None:
    """P&L expressed in units of the risk that was planned (§11.3).

    `R = net P&L / |entry − stop| × quantity × multiplier`. The denominator
    is the loss the position was *supposed* to take if the stop had been
    hit — not the loss it did take, and not the cost basis. A stop at the
    entry price is not a zero-risk trade, it is a missing stop, so it
    yields no R rather than an infinite one.
    """
    if unit.open_price is None or not unit.quantity:
        return None
    risk_per_unit = abs(unit.open_price - stop_loss)
    risk = risk_per_unit * abs(unit.quantity) * (unit.multiplier or 1)
    return unit.net_pnl / risk if risk > 1e-9 else None


def _stops_by_trade(db: Session, account_id: str) -> dict[int, float]:
    """Stops from `trade_annotations`, resolved through the durable anchor.

    Annotations outlive the trades they describe (§11.3), so they are keyed
    on the opening executions and matched back to whatever trade now owns
    them.
    """
    from app.journal.anchors import build_anchor_index
    from app.models.journal import TradeAnnotation

    index = build_anchor_index(db, account_id)
    if not index:
        return {}

    out: dict[int, float] = {}
    for row in (
        db.query(TradeAnnotation)
        .filter(
            TradeAnnotation.account_id == account_id,
            TradeAnnotation.stop_loss.isnot(None),
        )
        .all()
    ):
        trade_id = index.get(row.anchor_key)
        if trade_id is not None:
            out[trade_id] = float(row.stop_loss)
    return out


def _tags_by_trade(db: Session, account_id: str) -> dict[int, list[tuple[str, str | None]]]:
    from app.models.journal import Tag, TradeTag

    rows = (
        db.query(TradeTag.trade_id, Tag.name, Tag.group_name)
        .join(Tag, TradeTag.tag_id == Tag.id)
        .filter(TradeTag.account_id == account_id, TradeTag.trade_id.isnot(None))
        .all()
    )
    out: dict[int, list[tuple[str, str | None]]] = {}
    for trade_id, name, group_name in rows:
        out.setdefault(trade_id, []).append((name, group_name))
    return out


def load_units(
    db: Session, account_id: str, mode: AnalysisMode, include_open: bool = False
) -> list[ChartUnit]:
    """Every closed unit for the account, in the requested analysis mode.

    Pre-epoch units are dropped: §0.3 excludes them from every statistic in
    §8, and a chart is a statistic with axes.
    """
    tags = _tags_by_trade(db, account_id)
    stops = _stops_by_trade(db, account_id)

    def attach(unit: ChartUnit, trade_ids: list[int]) -> ChartUnit:
        names: list[str] = []
        groups: list[str] = []
        for trade_id in trade_ids:
            for name, group_name in tags.get(trade_id, []):
                if name not in names:
                    names.append(name)
                if group_name and group_name not in groups:
                    groups.append(group_name)
        unit.tags = names
        unit.tag_groups = groups

        # §11.3 — R-value, once a stop exists to measure against. Taken
        # from the first leg that has one: a group's R is the risk that was
        # actually defined, and averaging stops across legs would invent a
        # number nobody set.
        for trade_id in trade_ids:
            stop = stops.get(trade_id)
            if stop is not None:
                unit.stop_loss = stop
                unit.r_value = _r_value(unit, stop)
                break
        return unit

    if mode == AnalysisMode.LEG:
        query = db.query(Trade).filter(
            Trade.account_id == account_id, Trade.origin != Origin.PRE_EPOCH
        )
        if not include_open:
            query = query.filter(
                Trade.closed_at.isnot(None),
                Trade.status.in_([TradeStatus.CLOSED, TradeStatus.ORPHAN_CLOSE]),
            )
        return [attach(_trade_unit(t), [t.id]) for t in query.all()]

    query = db.query(TradeGroup).filter(TradeGroup.account_id == account_id)
    if not include_open:
        query = query.filter(TradeGroup.closed_at.isnot(None))
    return [
        # A group inherits the union of its legs' tags: tagging one leg of a
        # vertical is tagging the vertical, since the leg is not a decision
        # anyone made separately (§6).
        attach(_group_unit(g), [leg.id for leg in g.legs])
        for g in query.all()
        # §5.4 — chains overlap the spreads they span, so charting them as
        # units would double-count every leg. Excluded explicitly rather
        # than relying on them happening to own no legs.
        if g.strategy_type != StrategyType.ASSIGNMENT_CHAIN
        and g.legs
        and not all(leg.origin == Origin.PRE_EPOCH for leg in g.legs)
    ]
