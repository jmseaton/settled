"""§7.3 — return calculations.

Pure functions over a value/flow series (§13: "no database access inside
them"). Three measures that answer three different questions, reported side
by side because the gap between them is itself informative:

  * **Simple return** is easy and wrong the moment deposits are large
    relative to the account — which, on an account that grew from $905 to
    $10,118 on $2,255 of deposits plus a $6,042 transfer, is immediately.
  * **TWR** is the measure of *trading skill*: it strips out the timing and
    size of contributions, so a well-timed deposit cannot flatter it. This
    is the headline number.
  * **XIRR** is the return on *your actual dollars*, which is what the
    money did rather than what the decisions did.

Every one of them divides by a denominator that can legitimately be zero on
a real account — a day it was fully withdrawn, a sub-period whose starting
value plus flow nets to nothing. Each guard below is a case that occurs, not
defensive padding.
"""

import datetime as dt
from dataclasses import dataclass

#: A sub-period whose starting capital is smaller than this is treated as
#: having no measurable return rather than producing a division that
#: explodes. At $1e-6 the day contributed nothing worth chaining.
MIN_SUBPERIOD_BASE = 1e-6

DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class ValuePoint:
    """One day of the account: its ending value and the external flow that
    crossed the boundary that day (§7.2)."""

    day: dt.date
    value: float
    flow: float


def simple_return(
    starting_value: float, ending_value: float, net_deposits: float
) -> float | None:
    """`(V_end − V_start − net_deposits) / (V_start + net_deposits)` (§7.3).

    Reported for completeness and labelled as the naive measure. Returns
    None when the denominator vanishes rather than a number that looks like
    a result.
    """
    denominator = starting_value + net_deposits
    if abs(denominator) < MIN_SUBPERIOD_BASE:
        return None
    return (ending_value - starting_value - net_deposits) / denominator


def twr_factors(points: list[ValuePoint], starting_value: float) -> list[float]:
    """Per-period growth factors `1 + r_t`, in order.

    `r_t = (V_t − V_{t−1} − F_t) / (V_{t−1} + F_t)`. The flow is added to the
    denominator rather than ignored, which is what makes the day a deposit
    lands contribute no return: the money was there for the period, so it
    belongs in the base it earned against.
    """
    factors: list[float] = []
    previous = starting_value
    for point in points:
        base = previous + point.flow
        if abs(base) < MIN_SUBPERIOD_BASE:
            # Nothing was at risk; chaining a return here would be division
            # noise, not performance. Skip the period, keep the value.
            previous = point.value
            continue
        factors.append(1.0 + (point.value - previous - point.flow) / base)
        previous = point.value
    return factors


def twr(points: list[ValuePoint], starting_value: float) -> float | None:
    """Time-weighted return: `Π(1 + r_t) − 1` (§7.3)."""
    factors = twr_factors(points, starting_value)
    if not factors:
        return None
    product = 1.0
    for f in factors:
        product *= f
    return product - 1.0


def twr_series(points: list[ValuePoint], starting_value: float) -> list[float]:
    """Cumulative TWR after each point — the growth-of-$1 curve.

    Plotted instead of raw account value when the question is "how did the
    strategy do", because raw value steps up on every deposit and makes a
    funding event look like a good week.
    """
    out: list[float] = []
    product = 1.0
    previous = starting_value
    for point in points:
        base = previous + point.flow
        if abs(base) >= MIN_SUBPERIOD_BASE:
            product *= 1.0 + (point.value - previous - point.flow) / base
        previous = point.value
        out.append(product - 1.0)
    return out


def cagr_from_twr(total_twr: float | None, days: int) -> float | None:
    """`(1 + TWR)^(365/days) − 1` (§7.3).

    Computed from the TWR series rather than from raw beginning and ending
    values, so deposits cannot inflate it. Refuses to annualize a loss below
    −100% (impossible from a real series, but reachable from a corrupt one)
    and refuses sub-week windows outright: annualizing four days of data
    produces a triple-digit number that is pure artifact.
    """
    if total_twr is None or days < 7:
        return None
    growth = 1.0 + total_twr
    if growth <= 0:
        return None
    return growth ** (DAYS_PER_YEAR / days) - 1.0


def _npv(rate: float, flows: list[tuple[dt.date, float]], epoch: dt.date) -> float:
    total = 0.0
    for day, amount in flows:
        years = (day - epoch).days / DAYS_PER_YEAR
        total += amount / ((1.0 + rate) ** years)
    return total


def xirr(
    flows: list[tuple[dt.date, float]],
    *,
    max_iterations: int = 200,
    tolerance: float = 1e-7,
) -> float | None:
    """Money-weighted return: the rate where the NPV of all flows is zero.

    `flows` uses the investor's sign convention — money *into* the account
    is negative (it left your pocket), the terminal value is positive.

    Solved by bisection rather than Newton. Newton is faster and diverges on
    exactly the flow patterns a real account produces (a large late deposit
    with a small terminal value), and a silently diverged IRR is worse than
    no IRR. Bisection over a bracketed range either converges or reports
    that it could not.
    """
    if len(flows) < 2:
        return None
    if not (any(f < 0 for _, f in flows) and any(f > 0 for _, f in flows)):
        # No sign change means no root: the money only ever went one way.
        return None

    ordered = sorted(flows, key=lambda f: f[0])
    epoch = ordered[0][0]
    if ordered[-1][0] == epoch:
        return None

    low, high = -0.9999, 1e6
    npv_low = _npv(low, ordered, epoch)
    npv_high = _npv(high, ordered, epoch)
    if npv_low * npv_high > 0:
        return None

    for _ in range(max_iterations):
        mid = (low + high) / 2.0
        npv_mid = _npv(mid, ordered, epoch)
        if abs(npv_mid) < tolerance:
            return mid
        if npv_low * npv_mid < 0:
            high, npv_high = mid, npv_mid
        else:
            low, npv_low = mid, npv_mid
    return (low + high) / 2.0
