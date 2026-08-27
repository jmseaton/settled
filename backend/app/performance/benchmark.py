"""§7.5 — cash-flow-matched benchmark comparison.

**Why not just compare TWR against SPY's period return.** Because SPY's
return has no deposits in it. An account that was funded in three tranches
across a rising quarter cannot be honestly compared against a single
lump-sum index return; the comparison would credit or penalise the account
for the shape of its own funding.

The shadow portfolio fixes that by construction: the same money, on the
same dates, passively invested. Every dollar that entered the account buys
SPY at that day's adjusted close; every dollar that left sells it. What is
left is the only honest question — did trading beat doing nothing with the
identical cash flows?

Everything downstream reads `price_bars` (§4.10). No function here fetches
a price.
"""

import datetime as dt
import statistics
from dataclasses import dataclass, field

#: §7.5, §8.2 — the same gate as everywhere else. "With ten weeks of data
#: these will be noise, and the UI should say so rather than print a
#: confident-looking beta."
MIN_SAMPLE_TRADING_DAYS = 30

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class BenchmarkPoint:
    day: dt.date
    shadow_value: float
    #: Price carried forward from an older bar because the fetch is behind
    #: or the day was not in the provider's calendar.
    stale: bool


@dataclass
class ShadowPortfolio:
    points: list[BenchmarkPoint] = field(default_factory=list)
    final_shares: float = 0.0
    #: Days the portfolio had to reuse an older close. Reported rather than
    #: hidden: a chart drawn mostly from carried-forward prices is not a
    #: benchmark, it is a horizontal line.
    stale_days: int = 0
    #: Flows that landed before any price bar existed and could not buy
    #: anything. Silently dropping them would understate the shadow
    #: portfolio and flatter this account.
    unpriced_flows: list[dt.date] = field(default_factory=list)

    def as_map(self) -> dict[dt.date, BenchmarkPoint]:
        return {p.day: p for p in self.points}


def build_shadow_portfolio(
    days: list[dt.date],
    flows: dict[dt.date, float],
    prices: dict[dt.date, float],
    *,
    seed_value: float = 0.0,
    seed_day: dt.date | None = None,
) -> ShadowPortfolio:
    """Run the shadow portfolio over `days`.

    `flows` is the external flow per day (deposits positive, withdrawals
    negative) — the same `F_t` that TWR strips out, which is exactly the
    point: both measures see identical cash movements.

    `seed_value` is the epoch's `tracking_start_value`, invested on
    `seed_day`. §0.3: "The benchmark shadow portfolio starts at the epoch
    too, seeded with `tracking_start_value` as its initial flow. Otherwise
    you are comparing different periods."

    **Shares are never rounded** (test 52). A $1,000 deposit at a $600 close
    buys 1.6667 shares, not 1 — an index has no lot size, and rounding down
    would quietly hand the account a head start on every flow.
    """
    portfolio = ShadowPortfolio()
    shares = 0.0
    last_price: float | None = None

    seeded = seed_day is None or seed_value == 0.0

    for day in days:
        price = prices.get(day)
        stale = price is None
        if price is None:
            price = last_price
        else:
            last_price = price

        if not seeded and seed_day is not None and day >= seed_day:
            if price:
                shares += seed_value / price
                seeded = True
            else:
                portfolio.unpriced_flows.append(day)

        flow = flows.get(day, 0.0)
        if flow:
            if price:
                shares += flow / price
            else:
                portfolio.unpriced_flows.append(day)

        if price is None:
            # No bar has ever been seen; there is nothing to value against.
            # Recorded as a hole rather than as zero.
            continue

        if stale:
            portfolio.stale_days += 1
        portfolio.points.append(
            BenchmarkPoint(day=day, shadow_value=shares * price, stale=stale)
        )

    portfolio.final_shares = shares
    return portfolio


def _returns(values: list[float], flows: list[float] | None = None) -> list[float]:
    """Daily returns with external flows stripped out.

    `r_t = (V_t − V_{t−1} − F_t) / (V_{t−1} + F_t)` — §7.3's formula, used
    here and not just in the TWR module, because **a raw value change is
    not a return on a day money moved.**

    That distinction is the whole comparison. On the day a $6,042 transfer
    lands, both the account and its shadow portfolio jump by $6,042: the
    account because the cash arrived, the shadow because it bought the
    index with that same cash. Regressing raw changes would find those two
    jumps in lockstep and report beta 1.00 with correlation 1.00 — a
    perfect fit to the deposit schedule, measuring nothing about how the
    account trades. Netting the flow out leaves only the part that is
    actually performance.
    """
    flows = flows or [0.0] * len(values)
    out: list[float] = []
    for i in range(1, len(values)):
        previous, current, flow = values[i - 1], values[i], flows[i]
        base = previous + flow
        out.append((current - previous - flow) / base if abs(base) > 1e-9 else 0.0)
    return out


def _chain(returns: list[float]) -> float | None:
    if not returns:
        return None
    product = 1.0
    for r in returns:
        product *= 1.0 + r
    return product - 1.0


@dataclass
class BenchmarkComparison:
    insufficient_sample: bool
    sample_trading_days: int
    min_sample_trading_days: int = MIN_SAMPLE_TRADING_DAYS
    alpha: float | None = None
    alpha_annualized: float | None = None
    beta: float | None = None
    correlation: float | None = None
    tracking_error: float | None = None
    tracking_error_annualized: float | None = None
    information_ratio: float | None = None
    #: Time-weighted, so the two are comparable to each other and to the
    #: headline TWR on the same page. A raw start-to-end multiple would
    #: mostly measure the deposit schedule.
    account_return: float | None = None
    benchmark_return: float | None = None
    excess_return: float | None = None
    #: §7.5's actual headline: "Plot account_value_t against shadow_value_t
    #: on one chart. The gap is the honest answer." In dollars, because the
    #: dollars are the answer.
    account_final: float | None = None
    benchmark_final: float | None = None
    value_gap: float | None = None

    def as_dict(self) -> dict:
        return {
            "insufficient_sample": self.insufficient_sample,
            "sample_trading_days": self.sample_trading_days,
            "min_sample_trading_days": self.min_sample_trading_days,
            "alpha": self.alpha,
            "alpha_annualized": self.alpha_annualized,
            "beta": self.beta,
            "correlation": self.correlation,
            "tracking_error": self.tracking_error,
            "tracking_error_annualized": self.tracking_error_annualized,
            "information_ratio": self.information_ratio,
            "account_return": self.account_return,
            "benchmark_return": self.benchmark_return,
            "excess_return": self.excess_return,
            "account_final": self.account_final,
            "benchmark_final": self.benchmark_final,
            "value_gap": self.value_gap,
            "note": (
                "A retrospective measurement of one account against one index over a short "
                "window. Ten weeks of out- or under-performance says essentially nothing "
                "about skill. Not investment advice, and not a signal (§7.5)."
            ),
        }


def compare(
    account_values: list[float],
    shadow_values: list[float],
    flows: list[float] | None = None,
) -> BenchmarkComparison:
    """Alpha, beta, correlation, tracking error and information ratio.

    OLS of daily account returns on daily benchmark returns, all of it
    gated behind the ≥30-trading-day sample-size check. Below that the
    function returns `insufficient_sample` and no numbers at all (test 55) —
    printing a beta from three weeks of data is worse than printing nothing,
    because a number on a dashboard gets believed.

    `flows` is the external cash flow per day. Both series receive the same
    flows by construction — that is what "cash-flow matched" means — so
    both have them netted out before anything is regressed. Skipping that
    step yields a beta fitted to the deposit schedule.
    """
    n_days = min(len(account_values), len(shadow_values))
    if n_days < MIN_SAMPLE_TRADING_DAYS:
        return BenchmarkComparison(insufficient_sample=True, sample_trading_days=n_days)

    account_values = account_values[:n_days]
    shadow_values = shadow_values[:n_days]
    day_flows = (flows or [0.0] * n_days)[:n_days]

    ra = _returns(account_values, day_flows)
    rb = _returns(shadow_values, day_flows)
    if len(ra) < 2:
        return BenchmarkComparison(insufficient_sample=True, sample_trading_days=n_days)

    mean_a = statistics.fmean(ra)
    mean_b = statistics.fmean(rb)
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(ra, rb)) / (len(ra) - 1)
    var_b = sum((b - mean_b) ** 2 for b in rb) / (len(rb) - 1)

    beta = cov / var_b if var_b else None
    alpha = mean_a - beta * mean_b if beta is not None else None

    stdev_a = statistics.stdev(ra)
    stdev_b = statistics.stdev(rb)
    correlation = cov / (stdev_a * stdev_b) if stdev_a and stdev_b else None

    diffs = [a - b for a, b in zip(ra, rb)]
    tracking_error = statistics.stdev(diffs) if len(diffs) >= 2 else None
    mean_excess = statistics.fmean(diffs)
    information_ratio = mean_excess / tracking_error if tracking_error else None

    # Time-weighted on both sides. A start-to-end multiple would read
    # "1001% return" on an account that grew from $905 to $9,969 on $8,297
    # of deposits — a true statement about the funding schedule and a
    # useless one about performance.
    account_return = _chain(ra)
    benchmark_return = _chain(rb)

    return BenchmarkComparison(
        insufficient_sample=False,
        sample_trading_days=n_days,
        alpha=alpha,
        alpha_annualized=alpha * TRADING_DAYS_PER_YEAR if alpha is not None else None,
        beta=beta,
        correlation=correlation,
        tracking_error=tracking_error,
        tracking_error_annualized=(
            tracking_error * (TRADING_DAYS_PER_YEAR**0.5) if tracking_error else None
        ),
        information_ratio=information_ratio,
        account_return=account_return,
        benchmark_return=benchmark_return,
        excess_return=(
            account_return - benchmark_return
            if account_return is not None and benchmark_return is not None
            else None
        ),
        account_final=account_values[-1],
        benchmark_final=shadow_values[-1],
        value_gap=account_values[-1] - shadow_values[-1],
    )
