"""§7.5 — the `PriceSource` interface.

One method, deliberately. The requirement is trivially small — one symbol,
daily closes, incremental fetch, cached forever — and the interface is
narrow so that swapping providers costs one adapter and changes no
calculation code (test 53). Everything downstream reads `price_bars`; an
adapter's only job is to fill it.

`adj_close` must be **dividend-adjusted**, not merely split-adjusted. SPY
yields roughly 1.2%, and benchmarking against a price-only series overstates
relative performance by about that much every year. Several free sources are
ambiguous about which they return, so each adapter documents what it was
verified to provide and `verify_dividend_adjustment` gives the empirical
check the spec asks for.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class PriceFetchError(Exception):
    """A provider could not be reached or returned something unusable.

    Never fatal to a sync run (§7.5): the benchmark degrades to the last
    cached bar and is marked stale. A failed price fetch must not take the
    trade data down with it.
    """


@dataclass(frozen=True)
class DailyBar:
    date: dt.date
    close: float
    adj_close: float


@runtime_checkable
class PriceSource(Protocol):
    #: Recorded on every row written, so a provider swap is auditable
    #: rather than silently mixed mid-series (§4.10).
    name: str

    def daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> list[DailyBar]:
        """Inclusive on both ends. Missing days are simply absent — the
        caller must not assume one bar per calendar day."""
        ...


def verify_dividend_adjustment(bars: list[DailyBar]) -> dict:
    """Test 51 — is this series dividend-adjusted or only split-adjusted?

    Compares the total return implied by `adj_close` against the price-only
    return over the same span. For a dividend-paying symbol the former must
    exceed the latter by roughly the yield; if the two agree to the last
    decimal, the provider is handing back a price series under an
    ``adjusted`` label and every benchmark comparison built on it flatters
    this account by the dividend yield per year.

    Returns the measurement rather than a verdict — the threshold depends on
    the symbol and the window, and a caller comparing SPY over ten weeks
    should not be told "not dividend adjusted" on the strength of 0.2%.
    """
    usable = [b for b in bars if b.close and b.adj_close]
    if len(usable) < 2:
        return {"comparable": False, "reason": "need at least two bars with both prices"}

    first, last = usable[0], usable[-1]
    price_return = last.close / first.close - 1.0
    total_return = last.adj_close / first.adj_close - 1.0
    span_days = (last.date - first.date).days or 1
    excess = total_return - price_return

    return {
        "comparable": True,
        "from": first.date.isoformat(),
        "to": last.date.isoformat(),
        "span_days": span_days,
        "price_return": price_return,
        "total_return": total_return,
        "excess_return": excess,
        "annualized_excess": excess * 365.0 / span_days,
        # Identical series to floating-point noise is the failure this check
        # exists to catch.
        "series_identical": abs(excess) < 1e-9,
    }
