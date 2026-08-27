"""§7 — account-level performance, assembled.

The pure math lives in `returns.py` and `benchmark.py`; the daily series is
materialized in `daily_snapshots`. This module is the seam between them: it
reads the snapshots, picks the curve variant, and hands the numbers to
functions that never touch a database (§13).

The one rule it enforces everywhere: **a percentage return without an epoch
anchor is withheld, not approximated**. Absolute P&L is always available
because it needs no anchor; TWR, XIRR, CAGR and the benchmark comparison are
all `None` with a stated reason until `tracking_start_value` exists. §7.1 is
blunt about why — "Without that anchor, percentage returns are meaningless"
— and a plausible-looking wrong percentage is the specific failure this app
was built to avoid.
"""

import datetime as dt
import enum

from sqlalchemy.orm import Session

from app.models.settings_row import get_settings_row
from app.models.snapshot import DailySnapshot
from app.performance.benchmark import BenchmarkComparison, build_shadow_portfolio, compare
from app.performance.epoch import Epoch, resolve_epoch
from app.performance.returns import (
    ValuePoint,
    cagr_from_twr,
    simple_return,
    twr,
    twr_factors,
    twr_series,
    xirr,
)
from app.stats.engine import compute_return_ratios
from app.prices.store import load_bars


class CurveVariant(str, enum.Enum):
    """§7.4 — selectable equity-curve overlays."""

    #: Cumulative realized P&L, no cash flows. Starts at zero and answers
    #: "what did trading produce", which is a different question from
    #: "what is the account worth".
    PNL_ONLY = "PNL_ONLY"
    #: §7.2's account value: epoch value + external flows + realized P&L.
    #: The default, and the series TWR is computed from.
    WITH_FLOWS = "WITH_FLOWS"
    #: The above plus dividends, interest and fees. Closer to the real
    #: balance; diverges from §7.2's stated formula, which is why it is a
    #: separate variant rather than a correction to the default.
    WITH_INCOME = "WITH_INCOME"


def load_snapshots(db: Session, account_id: str) -> list[DailySnapshot]:
    return (
        db.query(DailySnapshot)
        .filter(DailySnapshot.account_id == account_id)
        .order_by(DailySnapshot.trading_day)
        .all()
    )


def curve_values(
    snapshots: list[DailySnapshot], variant: CurveVariant, epoch: Epoch
) -> list[float]:
    if variant == CurveVariant.PNL_ONLY:
        return [float(s.cumulative_net_pnl) for s in snapshots]
    del epoch  # the anchor is already baked into `account_value`

    values = [float(s.account_value) for s in snapshots]
    if variant == CurveVariant.WITH_INCOME:
        running = 0.0
        out = []
        for snapshot, value in zip(snapshots, values):
            running += float(snapshot.income_cash)
            out.append(value + running)
        return out
    return values


def rebuild_benchmark_shadow(db: Session, account_id: str, symbol: str | None = None) -> dict:
    """Run the shadow portfolio over the materialized days and store it.

    Persisted onto `daily_snapshots.benchmark_shadow_value` (§4.11) rather
    than recomputed per request, and re-run after every price refresh so a
    fetch that fills yesterday's gap updates the curve without a reimport.

    A day with no bar keeps the previous close and is marked
    `benchmark_stale`. That is test 54's requirement in table form: the
    cached bar stays, the line renders stale, nothing fails.
    """
    settings_row = get_settings_row(db)
    symbol = symbol or settings_row.benchmark_symbol
    epoch = resolve_epoch(db, account_id)
    snapshots = load_snapshots(db, account_id)
    if not snapshots:
        return {"symbol": symbol, "days": 0, "stale_days": 0, "priced": False}

    days = [s.trading_day for s in snapshots]
    flows = {s.trading_day: float(s.cash_flow) for s in snapshots}
    bars = load_bars(db, symbol, days[0] - dt.timedelta(days=10), days[-1])
    prices = {bar.date: float(bar.adj_close) for bar in bars}

    portfolio = build_shadow_portfolio(
        days,
        flows,
        prices,
        # §0.3 — the shadow portfolio starts at the epoch too, seeded with
        # `tracking_start_value`. Otherwise the two curves cover different
        # periods and the comparison is meaningless.
        seed_value=epoch.start_value if epoch.configured else 0.0,
        seed_day=epoch.start_date,
    )

    by_day = portfolio.as_map()
    for snapshot in snapshots:
        point = by_day.get(snapshot.trading_day)
        snapshot.benchmark_shadow_value = point.shadow_value if point else None
        snapshot.benchmark_stale = bool(point.stale) if point else False
    db.flush()

    return {
        "symbol": symbol,
        "days": len(portfolio.points),
        "stale_days": portfolio.stale_days,
        "priced": bool(prices),
        "final_shares": portfolio.final_shares,
        "unpriced_flow_days": [d.isoformat() for d in portfolio.unpriced_flows],
    }


def _daily_returns(
    snapshots: list[DailySnapshot], values: list[float], epoch: Epoch
) -> list[float]:
    """The flow-adjusted daily return series (§7.3's `r_t`).

    The same series TWR chains and the benchmark regresses, so the risk
    ratios cannot disagree with either about what a day's return was.
    """
    if not epoch.configured:
        return []
    # `twr_factors` yields growth factors (1 + r); the ratios want r.
    return [f - 1.0 for f in twr_factors(_value_points(snapshots, values), epoch.start_value)]


def _value_points(
    snapshots: list[DailySnapshot], values: list[float]
) -> list[ValuePoint]:
    return [
        ValuePoint(day=s.trading_day, value=v, flow=float(s.cash_flow))
        for s, v in zip(snapshots, values)
    ]


def compute_returns(
    snapshots: list[DailySnapshot], values: list[float], epoch: Epoch
) -> dict:
    """§7.3 — TWR as the headline, the others clearly labelled."""
    if not snapshots or not epoch.configured:
        return {
            "available": False,
            "reason": epoch.note or "No daily snapshots for this account yet.",
            "twr": None,
            "xirr": None,
            "cagr": None,
            "simple_return": None,
        }

    points = _value_points(snapshots, values)
    total_twr = twr(points, epoch.start_value)

    net_deposits = sum(float(s.cash_flow) for s in snapshots)
    ending_value = values[-1]
    span_days = (snapshots[-1].trading_day - snapshots[0].trading_day).days + 1

    # Investor sign convention: money into the account is negative (it left
    # your pocket), the terminal value is the positive payoff.
    flows: list[tuple[dt.date, float]] = [(epoch.start_date, -epoch.start_value)]
    for snapshot in snapshots:
        amount = float(snapshot.cash_flow)
        if amount:
            flows.append((snapshot.trading_day, -amount))
    flows.append((snapshots[-1].trading_day, ending_value))

    realized_partials = sum(float(s.realized_partial_net) for s in snapshots)
    marked_days = sum(1 for s in snapshots if s.unrealized_priced)
    closing_unrealized = float(snapshots[-1].unrealized_pnl) if snapshots else 0.0

    return {
        "available": True,
        "reason": None,
        "starting_value": epoch.start_value,
        "ending_value": ending_value,
        "net_deposits": net_deposits,
        "realized_from_partials": realized_partials,
        "span_days": span_days,
        # §7.2's "[+ unrealized_t if available]" — available since §12, from
        # the marks Flex already publishes on Open Positions. Coverage is
        # only as dense as the import history, so it is reported rather
        # than assumed: a period with two marked days out of fifty is a
        # realized-only curve with two marked points, not a marked curve.
        "excludes_unrealized": marked_days == 0,
        "marked_days": marked_days,
        "closing_unrealized": closing_unrealized,
        "unrealized_note": (
            "Open positions are valued at zero — no position snapshot covers this period, so "
            "these returns are realized-only and will read below IBKR's reported TWR by the "
            "change in unrealized P&L (§12)."
            if marked_days == 0
            else (
                f"Open positions are marked to the broker's own close on {marked_days} of "
                f"{len(snapshots)} day(s), from Flex Open Positions (§12). Days without a "
                "snapshot are realized-only rather than carrying a stale mark forward."
            )
        ),
        "twr": total_twr,
        "twr_note": (
            "Time-weighted: strips out the timing and size of your own contributions. "
            "The measure of trading skill, and the headline figure (§7.3)."
        ),
        "xirr": xirr(flows),
        "xirr_note": (
            "Money-weighted: the return on your actual dollars, contribution timing "
            "included. Answers a different question from TWR; the gap between them is "
            "itself informative (§7.3)."
        ),
        "cagr": cagr_from_twr(total_twr, span_days),
        "cagr_note": (
            "Annualized from the TWR series, not from raw values, so deposits cannot "
            "inflate it. Withheld under a week of data — annualizing four days produces "
            "an artifact, not a rate."
        ),
        "simple_return": simple_return(epoch.start_value, ending_value, net_deposits),
        "simple_return_note": (
            "The naive measure. Easy, and wrong when deposits are large relative to the "
            "account — which on this account they are (§7.3)."
        ),
    }


def compute_benchmark(
    db: Session,
    snapshots: list[DailySnapshot],
    values: list[float],
    epoch: Epoch,
    symbol: str,
) -> dict:
    """§7.5 — the shadow portfolio series plus the regression statistics."""
    shadow = [
        float(s.benchmark_shadow_value)
        for s in snapshots
        if s.benchmark_shadow_value is not None
    ]
    priced_days = len(shadow)
    stale_days = sum(1 for s in snapshots if s.benchmark_stale)
    latest_priced = next(
        (
            s.trading_day
            for s in reversed(snapshots)
            if s.benchmark_shadow_value is not None and not s.benchmark_stale
        ),
        None,
    )

    if not epoch.configured:
        comparison = BenchmarkComparison(
            insufficient_sample=True, sample_trading_days=priced_days
        )
        reason = epoch.note
    elif priced_days < len(values):
        # Compare only the overlapping stretch, and say how much of the
        # period that is — a benchmark drawn over half the days is not a
        # comparison over the period.
        aligned = [
            (v, float(s.benchmark_shadow_value), float(s.cash_flow))
            for s, v in zip(snapshots, values)
            if s.benchmark_shadow_value is not None
        ]
        comparison = compare(
            [a for a, _, _ in aligned],
            [b for _, b, _ in aligned],
            [f for _, _, f in aligned],
        )
        reason = (
            f"Benchmark bars cover {priced_days} of {len(values)} days in the period."
            if priced_days
            else "No benchmark price bars are cached for this period yet (§7.5)."
        )
    else:
        comparison = compare(values, shadow, [float(s.cash_flow) for s in snapshots])
        reason = None

    return {
        "symbol": symbol,
        "priced_days": priced_days,
        "stale_days": stale_days,
        "latest_priced_day": latest_priced.isoformat() if latest_priced else None,
        "is_stale": bool(stale_days) or priced_days == 0,
        "coverage_note": reason,
        "method": (
            "Cash-flow matched buy-and-hold: the same money, on the same dates, passively "
            "invested at the adjusted close. Comparing TWR against the index's raw period "
            "return would be apples-to-oranges — the index has no deposits in it (§7.5)."
        ),
        **comparison.as_dict(),
    }


def build_performance(
    db: Session,
    account_id: str,
    variant: CurveVariant = CurveVariant.WITH_FLOWS,
    benchmark_symbol: str | None = None,
) -> dict:
    settings_row = get_settings_row(db)
    symbol = benchmark_symbol or settings_row.benchmark_symbol
    epoch = resolve_epoch(db, account_id)
    snapshots = load_snapshots(db, account_id)
    values = curve_values(snapshots, variant, epoch)

    # Returns are always computed from an *account-value* series. A
    # cumulative-P&L curve starts at zero and has no denominator, so
    # chaining sub-period returns over it yields −100% and nothing else;
    # PNL_ONLY therefore plots what was asked for and reports returns from
    # the §7.2 curve, saying so rather than printing the artifact.
    return_variant = (
        CurveVariant.WITH_FLOWS if variant == CurveVariant.PNL_ONLY else variant
    )
    return_values = (
        values if return_variant == variant else curve_values(snapshots, return_variant, epoch)
    )
    growth = (
        twr_series(_value_points(snapshots, return_values), epoch.start_value)
        if epoch.configured
        else []
    )
    returns = compute_returns(snapshots, return_values, epoch)
    returns["basis_variant"] = return_variant.value
    if return_variant != variant:
        returns["basis_note"] = (
            f"Computed from the {return_variant.value} account-value series. A "
            f"{variant.value} curve has no starting capital to measure against."
        )

    return {
        "account_id": account_id,
        "variant": variant.value,
        "epoch": epoch.as_dict(),
        "series": [
            {
                "trading_day": s.trading_day.isoformat(),
                "value": v,
                "realized_net": float(s.realized_net),
                "realized_partial_net": float(s.realized_partial_net),
                "cumulative_net_pnl": float(s.cumulative_net_pnl),
                "cash_flow": float(s.cash_flow),
                "income_cash": float(s.income_cash),
                "unrealized_pnl": float(s.unrealized_pnl),
                "unrealized_priced": s.unrealized_priced,
                "account_value": float(s.account_value),
                "drawdown": float(s.drawdown),
                "drawdown_pct": float(s.drawdown_pct),
                "twr_cumulative": growth[i] if i < len(growth) else None,
                "benchmark_shadow_value": (
                    float(s.benchmark_shadow_value)
                    if s.benchmark_shadow_value is not None
                    else None
                ),
                "benchmark_stale": s.benchmark_stale,
                "trade_count": s.trade_count,
            }
            for i, (s, v) in enumerate(zip(snapshots, values))
        ],
        "returns": returns,
        # §8.2 on the conventional basis. The Statistics page keeps its
        # trade-P&L versions; neither is a correction of the other, and
        # both say which series they came from.
        "ratios": compute_return_ratios(
            _daily_returns(snapshots, return_values, epoch)
        ),
        # The benchmark compares like with like: an account-value series
        # against a portfolio value, never a P&L curve against a portfolio.
        "benchmark": compute_benchmark(db, snapshots, return_values, epoch, symbol),
    }
