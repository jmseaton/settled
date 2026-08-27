"""§8.1-8.2 — statistics catalog (subset scoped to Phase 2).

Pure functions over a filtered trade set (§13: "no database access inside
them"). Ratios that conventionally read *account equity* returns (Sharpe,
Sortino, Calmar, Omega, Recovery Factor, Ulcer Index, CAGR) are computed
here from the *closed-trade P&L* daily series instead, because account-level
cash tracking (§7, deposits/withdrawals/TWR) is Phase 3 and not yet built.
Each such field carries `is_proxy=True` in the response so it is never
mistaken for a true account-return figure — this mirrors the spec's own
insistence (§7.5, §8.2) that a number computed from a short or incomplete
series say so rather than look authoritative.
"""

import datetime as dt
import statistics
from dataclasses import dataclass

from app.stats.significance import describe_sharpe

#: §8.2's display gate. Below this the ratios are not drawn at all.
#:
#: §14.6.3 called this "a reasonable default, not a researched one", and it
#: no longer has to carry that weight: every Sharpe is now reported with the
#: standard error and confidence interval that say precisely how much the
#: sample supports (see `significance.py`). The threshold decides only
#: whether to draw a number, not whether to believe it — and the interval
#: usually says "not yet" long after this cutoff has been cleared.
def _min_sample_days() -> int:
    from app.config import get_settings

    return get_settings().min_sample_trading_days


#: Module-level default, kept so existing imports and tests keep working.
MIN_SAMPLE_TRADING_DAYS = 30


@dataclass
class TradeStat:
    net_pnl: float
    gross_pnl: float
    commissions: float
    opened_at: dt.datetime | None
    closed_at: dt.datetime | None
    trading_day: dt.date | None
    excluded_from_win_rate: bool
    return_pct: float | None = None


@dataclass
class DayStat:
    trading_day: dt.date
    realized_net: float
    realized_gross: float
    cumulative_net_pnl: float


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def _stdev(xs: list[float]) -> float | None:
    return statistics.stdev(xs) if len(xs) >= 2 else None


def compute_pnl_summary(trades: list[TradeStat], days: list[DayStat]) -> dict:
    """§8.1"""
    closed = [t for t in trades if t.closed_at is not None]
    net_pnls = [t.net_pnl for t in closed]
    winners = [t.net_pnl for t in closed if t.net_pnl > 0]
    losers = [t.net_pnl for t in closed if t.net_pnl < 0]

    by_month: dict[str, float] = {}
    by_year: dict[str, float] = {}
    for d in days:
        by_month[d.trading_day.strftime("%Y-%m")] = by_month.get(d.trading_day.strftime("%Y-%m"), 0.0) + d.realized_net
        by_year[str(d.trading_day.year)] = by_year.get(str(d.trading_day.year), 0.0) + d.realized_net

    streak_win, streak_loss, cur_sign, cur_sum = 0.0, 0.0, 0, 0.0
    ordered = sorted(closed, key=lambda t: t.closed_at)
    for t in ordered:
        sign = 1 if t.net_pnl > 0 else (-1 if t.net_pnl < 0 else 0)
        if sign == cur_sign and sign != 0:
            cur_sum += t.net_pnl
        else:
            cur_sign, cur_sum = sign, t.net_pnl
        if cur_sign > 0:
            streak_win = max(streak_win, cur_sum)
        elif cur_sign < 0:
            streak_loss = min(streak_loss, cur_sum)

    returns = [t.return_pct for t in closed if t.return_pct is not None]

    return {
        "trade_count": len(closed),
        "total_net_pnl": sum(net_pnls) if net_pnls else 0.0,
        "total_gross_pnl": sum(t.gross_pnl for t in closed) if closed else 0.0,
        "sum_winners": sum(winners) if winners else 0.0,
        "sum_losers": sum(losers) if losers else 0.0,
        "avg_pnl_per_trade": _mean(net_pnls),
        "avg_pct_return_per_trade": _mean(returns),
        "avg_win": _mean(winners),
        "avg_loss": _mean(losers),
        "avg_pnl_per_day": _mean([d.realized_net for d in days]),
        "avg_pnl_per_month": _mean(list(by_month.values())),
        "avg_pnl_per_year": _mean(list(by_year.values())),
        "best_trade": max(net_pnls) if net_pnls else None,
        "worst_trade": min(net_pnls) if net_pnls else None,
        "best_day": max((d.realized_net for d in days), default=None),
        "worst_day": min((d.realized_net for d in days), default=None),
        "largest_winning_streak_pnl": streak_win,
        "largest_losing_streak_pnl": streak_loss,
        "monthly_pnl": by_month,
        "yearly_pnl": by_year,
    }


def _max_drawdown(days: list[DayStat]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for d in days:
        peak = max(peak, d.cumulative_net_pnl)
        max_dd = max(max_dd, peak - d.cumulative_net_pnl)
    return max_dd


def compute_ratios(trades: list[TradeStat], days: list[DayStat], rf_daily: float = 0.0) -> dict:
    """§8.2. Gated behind the >=30 trading-day sample-size rule where the
    spec calls for it; returns None with `insufficient_sample=True` rather
    than a noisy number."""
    eligible = [t for t in trades if t.closed_at is not None and not t.excluded_from_win_rate]
    wins = [t.net_pnl for t in eligible if t.net_pnl > 0]
    losses = [t.net_pnl for t in eligible if t.net_pnl < 0]
    n_decided = len(wins) + len(losses)

    win_rate = len(wins) / n_decided if n_decided else None
    loss_rate = len(losses) / n_decided if n_decided else None
    avg_win = _mean(wins)
    avg_loss = _mean(losses)

    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    win_loss_ratio = (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss else None
    adjusted_win_loss = (
        (avg_win * win_rate) / (abs(avg_loss) * loss_rate)
        if avg_win is not None and avg_loss and win_rate is not None and loss_rate
        else None
    )
    expectancy = (
        win_rate * avg_win - loss_rate * abs(avg_loss)
        if win_rate is not None and avg_win is not None and loss_rate is not None and avg_loss is not None
        else None
    )
    kelly = (
        win_rate - (loss_rate / (avg_win / abs(avg_loss)))
        if win_rate is not None and loss_rate is not None and avg_win and avg_loss
        else None
    )

    all_pnls = [t.net_pnl for t in eligible]
    n = len(all_pnls)
    sqn = None
    if n >= 2:
        stdev = _stdev(all_pnls)
        if stdev:
            sqn = (_mean(all_pnls) / stdev) * (min(n, 100) ** 0.5)

    daily_returns = [d.realized_net for d in days]
    n_days = len(daily_returns)
    threshold = _min_sample_days()
    insufficient_sample = n_days < threshold

    sharpe = sortino = calmar = omega = recovery_factor = ulcer_index = upi = tail_ratio = None
    if not insufficient_sample:
        mean_r = _mean(daily_returns)
        stdev_r = _stdev(daily_returns)
        if stdev_r:
            sharpe = ((mean_r - rf_daily) / stdev_r) * (252**0.5)
        neg_returns = [r for r in daily_returns if r < 0]
        stdev_neg = _stdev(neg_returns)
        if stdev_neg:
            sortino = ((mean_r - rf_daily) / stdev_neg) * (252**0.5)

        max_dd = _max_drawdown(days)
        total_return = days[-1].cumulative_net_pnl if days else 0.0
        years = max(n_days / 252.0, 1e-9)
        annualized = total_return / years
        if max_dd:
            calmar = annualized / max_dd
            recovery_factor = total_return / max_dd

        pos = sum(r for r in daily_returns if r > 0)
        neg = abs(sum(r for r in daily_returns if r < 0))
        omega = pos / neg if neg else None

        peak = float("-inf")
        dd_pct_sq = []
        for d in days:
            peak = max(peak, d.cumulative_net_pnl)
            if peak > 0:
                dd_pct_sq.append(((peak - d.cumulative_net_pnl) / peak) ** 2)
        if dd_pct_sq:
            ulcer_index = statistics.fmean(dd_pct_sq) ** 0.5
            if ulcer_index:
                upi = (annualized - rf_daily * 252) / ulcer_index

        sorted_r = sorted(daily_returns)
        if len(sorted_r) >= 5:
            p95 = sorted_r[int(0.95 * (len(sorted_r) - 1))]
            p5 = sorted_r[int(0.05 * (len(sorted_r) - 1))]
            if p5 != 0:
                tail_ratio = p95 / abs(p5)

    return {
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "win_loss_ratio": win_loss_ratio,
        "adjusted_win_loss": adjusted_win_loss,
        "expectancy": expectancy,
        "kelly": kelly,
        "sqn": sqn,
        "insufficient_sample": insufficient_sample,
        "sample_trading_days": n_days,
        "min_sample_trading_days": threshold,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "omega": omega,
        "recovery_factor": recovery_factor,
        "ulcer_index": ulcer_index,
        "ulcer_performance_index": upi,
        "tail_ratio": tail_ratio,
        "max_drawdown": _max_drawdown(days),
        "proxy_metrics": [
            "sharpe", "sortino", "calmar", "omega", "recovery_factor",
            "ulcer_index", "ulcer_performance_index",
        ],
        "proxy_note": (
            "Computed from closed-trade daily P&L, not account equity returns — the "
            "Performance page reports the same ratios over the account-return series, "
            "which is the conventional basis (§8.2)."
        ),
        # §14.6.3 — the cutoff says whether to draw the number; this says
        # how much of it to believe.
        "sharpe_significance": describe_sharpe(sharpe, max(n_days - 1, 0)),
    }


def compute_return_ratios(
    daily_returns: list[float], rf_daily: float = 0.0
) -> dict:
    """§8.2 computed from **account returns** rather than trade P&L.

    The versions in `compute_ratios` above read a daily closed-trade P&L
    series, because until §7 landed there was no account-return series to
    read. There is now, and these are what Sharpe, Sortino, Calmar, Omega
    and the Ulcer index conventionally mean.

    Both are reported rather than one replacing the other. They answer
    different questions — "how volatile was my trading result" versus "how
    volatile was my account" — and on an account holding undeployed cash
    they differ substantially. Silently switching the existing numbers to
    this basis would change every figure on the Statistics page without
    anything on screen saying why.

    Returns are fractional (0.01 = 1%), so unlike the P&L-based versions
    these are scale-free and comparable against published figures.
    """
    n_days = len(daily_returns)
    threshold = _min_sample_days()
    insufficient = n_days < threshold
    out = {
        "basis": "account_returns",
        "insufficient_sample": insufficient,
        "sample_trading_days": n_days,
        "min_sample_trading_days": threshold,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "omega": None,
        "ulcer_index": None,
        "max_drawdown_pct": None,
        "annualized_return": None,
        "annualized_volatility": None,
        "note": (
            "Computed from daily account returns — the conventional basis. The Statistics "
            "page reports the same ratios over the closed-trade P&L series, which answers a "
            "different question; neither is a correction of the other (§8.2)."
        ),
        "sharpe_significance": describe_sharpe(None, n_days),
    }
    if insufficient or n_days < 2:
        return out

    mean_r = _mean(daily_returns)
    stdev_r = _stdev(daily_returns)
    if stdev_r:
        out["sharpe"] = ((mean_r - rf_daily) / stdev_r) * (252**0.5)
        out["annualized_volatility"] = stdev_r * (252**0.5)

    downside = _stdev([r for r in daily_returns if r < 0])
    if downside:
        out["sortino"] = ((mean_r - rf_daily) / downside) * (252**0.5)

    # Growth of one unit, so drawdown is a true percentage rather than a
    # dollar amount divided by a moving base.
    curve, value = [], 1.0
    for r in daily_returns:
        value *= 1.0 + r
        curve.append(value)

    peak = float("-inf")
    max_dd_pct = 0.0
    squared = []
    for point in curve:
        peak = max(peak, point)
        if peak > 0:
            drawdown = (peak - point) / peak
            max_dd_pct = max(max_dd_pct, drawdown)
            squared.append(drawdown**2)

    total_growth = curve[-1] if curve else 1.0
    years = max(n_days / 252.0, 1e-9)
    annualized = total_growth ** (1.0 / years) - 1.0 if total_growth > 0 else None

    out["max_drawdown_pct"] = max_dd_pct
    out["annualized_return"] = annualized
    if max_dd_pct and annualized is not None:
        out["calmar"] = annualized / max_dd_pct

    gains = sum(r for r in daily_returns if r > 0)
    losses = abs(sum(r for r in daily_returns if r < 0))
    out["omega"] = gains / losses if losses else None

    if squared:
        out["ulcer_index"] = statistics.fmean(squared) ** 0.5

    # §14.6.3 — the interval, not the cutoff, is what says how much of this
    # Sharpe the sample actually supports.
    out["sharpe_significance"] = describe_sharpe(out["sharpe"], n_days)
    return out
