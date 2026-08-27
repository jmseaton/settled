"""§7.3 — TWR, XIRR, CAGR and simple return, as pure functions."""

import datetime as dt

import pytest

from app.performance.returns import (
    ValuePoint,
    cagr_from_twr,
    simple_return,
    twr,
    twr_series,
    xirr,
)


def _flat(values: list[float], flows: list[float] | None = None) -> list[ValuePoint]:
    flows = flows or [0.0] * len(values)
    start = dt.date(2026, 1, 5)
    return [
        ValuePoint(day=start + dt.timedelta(days=i), value=v, flow=f)
        for i, (v, f) in enumerate(zip(values, flows))
    ]


def test_twr_chains_simple_growth():
    # 100 -> 110 -> 121 is two 10% periods.
    assert twr(_flat([110.0, 121.0]), 100.0) == pytest.approx(0.21)


def test_a_deposit_produces_no_return():
    """The whole point of time-weighting: money arriving is not performance.

    Day one the account goes from 100 to 200, but 100 of that was a
    deposit. A simple return would call this +100%.
    """
    points = _flat([200.0, 220.0], flows=[100.0, 0.0])
    assert twr(points, 100.0) == pytest.approx(0.10)


def test_simple_return_is_the_naive_measure():
    """Same account as above; the naive figure disagrees, and §7.3 says to
    show both precisely because the gap is informative."""
    assert simple_return(100.0, 220.0, 100.0) == pytest.approx(0.10)
    # ...but drop the deposit adjustment and it reads +120%.
    assert simple_return(100.0, 220.0, 0.0) == pytest.approx(1.20)


def test_a_withdrawal_to_zero_does_not_explode():
    """§7.3 — "Guard against divide-by-zero when V_{t-1} + F_t ≈ 0."

    A fully withdrawn account is a real state, not a corrupt one, and the
    period after it must not return infinity.
    """
    points = _flat([0.0, 0.0, 50.0], flows=[-100.0, 0.0, 50.0])
    result = twr(points, 100.0)
    assert result is not None
    assert result == pytest.approx(0.0, abs=1e-9)


def test_twr_series_is_the_growth_of_one_dollar():
    series = twr_series(_flat([110.0, 121.0]), 100.0)
    assert series == pytest.approx([0.10, 0.21])


def test_twr_of_an_empty_series_is_none_not_zero():
    assert twr([], 100.0) is None


def test_cagr_annualizes_from_twr():
    # 10% over half a year annualizes to about 21%.
    assert cagr_from_twr(0.10, 182) == pytest.approx(0.2116, abs=0.001)


def test_cagr_refuses_a_window_too_short_to_annualize():
    """Annualizing four days produces a triple-digit artifact, not a rate."""
    assert cagr_from_twr(0.02, 4) is None


def test_cagr_refuses_a_total_loss():
    assert cagr_from_twr(-1.0, 365) is None


def test_xirr_recovers_a_known_rate():
    """$1,000 in, $1,100 out a year later is 10%."""
    flows = [(dt.date(2026, 1, 1), -1000.0), (dt.date(2027, 1, 1), 1100.0)]
    assert xirr(flows) == pytest.approx(0.10, abs=1e-4)


def test_xirr_weights_a_late_deposit_differently_from_twr():
    """The money-weighted answer depends on *when* the money arrived, which
    is exactly the difference §7.3 asks to be shown."""
    early = xirr([(dt.date(2026, 1, 1), -1000.0), (dt.date(2026, 12, 31), 1100.0)])
    late = xirr(
        [
            (dt.date(2026, 1, 1), -100.0),
            (dt.date(2026, 11, 1), -900.0),
            (dt.date(2026, 12, 31), 1100.0),
        ]
    )
    assert late > early


def test_xirr_without_a_sign_change_is_none():
    """Money that only ever went one way has no internal rate of return."""
    assert xirr([(dt.date(2026, 1, 1), -100.0), (dt.date(2026, 6, 1), -100.0)]) is None


def test_xirr_needs_two_distinct_dates():
    assert xirr([(dt.date(2026, 1, 1), -100.0), (dt.date(2026, 1, 1), 110.0)]) is None
