"""§9.2 — the y-axis library.

Each metric reduces a bucket's units to one number. Pure functions over a
list of `ChartUnit`, no database access (§13), so every one of them is
directly unit-testable — which is the difference between a dashboard you
trust and one you second-guess.

Metrics return `None` rather than zero when the inputs are absent. A bucket
with no losing trades has no profit factor; printing 0 there would read as
"terrible" when it means "undefined", and a chart is exactly the place that
distinction gets lost.
"""

import statistics
from dataclasses import dataclass
from typing import Callable

from app.charts.units import ChartUnit

MetricFn = Callable[[list[ChartUnit]], float | None]


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    fn: MetricFn
    #: How the UI should format the axis: money, percent, count or ratio.
    unit: str
    #: True when a negative value is bad news; drives the red/green split.
    signed: bool = True


def _decided(units: list[ChartUnit]) -> list[ChartUnit]:
    """Units that count toward a win rate.

    Orphan closes and pre-epoch rebasing produce trades with no meaningful
    outcome; §8.2 excludes them, and so must anything derived from wins and
    losses.
    """
    return [u for u in units if not u.excluded_from_win_rate]


def _net_pnl(units: list[ChartUnit]) -> float | None:
    return sum(u.net_pnl for u in units) if units else None


def _gross_pnl(units: list[ChartUnit]) -> float | None:
    return sum(u.gross_pnl for u in units) if units else None


def _commissions(units: list[ChartUnit]) -> float | None:
    return sum(u.commissions for u in units) if units else None


def _trade_count(units: list[ChartUnit]) -> float | None:
    return float(len(units))


def _volume(units: list[ChartUnit]) -> float | None:
    return sum(abs(u.quantity) for u in units) if units else None


def _avg_pnl(units: list[ChartUnit]) -> float | None:
    return statistics.fmean([u.net_pnl for u in units]) if units else None


def _win_rate(units: list[ChartUnit]) -> float | None:
    decided = [u for u in _decided(units) if u.net_pnl != 0]
    if not decided:
        return None
    return sum(1 for u in decided if u.net_pnl > 0) / len(decided)


def _expectancy(units: list[ChartUnit]) -> float | None:
    """Expected P&L per unit: `win_rate × avg_win − loss_rate × avg_loss`.

    Deliberately not the same as average P&L, even though on a complete
    sample they coincide: expectancy is computed from the decided units
    only, so an orphan close cannot drag it.
    """
    decided = _decided(units)
    wins = [u.net_pnl for u in decided if u.net_pnl > 0]
    losses = [u.net_pnl for u in decided if u.net_pnl < 0]
    n = len(wins) + len(losses)
    if not n:
        return None
    win_rate = len(wins) / n
    loss_rate = len(losses) / n
    avg_win = statistics.fmean(wins) if wins else 0.0
    avg_loss = abs(statistics.fmean(losses)) if losses else 0.0
    return win_rate * avg_win - loss_rate * avg_loss


def _profit_factor(units: list[ChartUnit]) -> float | None:
    decided = _decided(units)
    wins = sum(u.net_pnl for u in decided if u.net_pnl > 0)
    losses = abs(sum(u.net_pnl for u in decided if u.net_pnl < 0))
    return wins / losses if losses else None


def _avg_duration(units: list[ChartUnit]) -> float | None:
    durations = [
        u.duration_seconds
        for u in units
        if u.duration_seconds is not None and not u.excluded_from_duration
    ]
    return statistics.fmean(durations) if durations else None


def _avg_return_pct(units: list[ChartUnit]) -> float | None:
    returns = [u.return_pct for u in units if u.return_pct is not None]
    return statistics.fmean(returns) if returns else None


def _return_on_risk(units: list[ChartUnit]) -> float | None:
    """§6.3 — P&L over defined risk, and only where risk is defined.

    Units with unbounded risk (a naked short) are excluded rather than
    treated as zero-risk. Pooling a naked short into a return-on-risk figure
    is the specific error §6.3 refuses to make: it would report an infinite
    return on no capital.
    """
    priced = [u for u in units if u.max_risk not in (None, 0)]
    if not priced:
        return None
    total_risk = sum(abs(u.max_risk) for u in priced)
    return sum(u.net_pnl for u in priced) / total_risk if total_risk else None


def _r_value(units: list[ChartUnit]) -> float | None:
    values = [u.r_value for u in units if u.r_value is not None]
    return statistics.fmean(values) if values else None


def _cost_ratio(units: list[ChartUnit]) -> float | None:
    """Commissions as a share of gross P&L (§8.4).

    Worth its own axis: $21.62 of commissions against $1,549.56 of gross
    flow is a meaningful slice, and per-leg spread trading multiplies it.
    """
    gross = sum(abs(u.gross_pnl) for u in units)
    return abs(sum(u.commissions for u in units)) / gross if gross else None


METRICS: dict[str, MetricSpec] = {
    m.key: m
    for m in [
        MetricSpec("net_pnl", "Net P&L", _net_pnl, "money"),
        MetricSpec("gross_pnl", "Gross P&L", _gross_pnl, "money"),
        MetricSpec("commissions", "Commissions", _commissions, "money"),
        MetricSpec("trade_count", "Trade count", _trade_count, "count", signed=False),
        MetricSpec("volume", "Volume", _volume, "count", signed=False),
        MetricSpec("avg_pnl", "Avg P&L per unit", _avg_pnl, "money"),
        MetricSpec("win_rate", "Win rate", _win_rate, "percent", signed=False),
        MetricSpec("expectancy", "Expectancy", _expectancy, "money"),
        MetricSpec("profit_factor", "Profit factor", _profit_factor, "ratio", signed=False),
        MetricSpec("avg_duration", "Avg duration", _avg_duration, "seconds", signed=False),
        MetricSpec("avg_return_pct", "Avg % return", _avg_return_pct, "percent"),
        MetricSpec("return_on_risk", "Return on risk", _return_on_risk, "percent"),
        MetricSpec("r_value", "R-value", _r_value, "ratio"),
        MetricSpec("cost_ratio", "Commissions / gross", _cost_ratio, "percent", signed=False),
    ]
}
