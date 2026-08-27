"""§7.5 / §16.6 — benchmark tests 50-55, plus the price layer around them."""

import datetime as dt

import httpx
import pytest

from app.models.price_bar import PriceBar
from app.performance.benchmark import (
    MIN_SAMPLE_TRADING_DAYS,
    build_shadow_portfolio,
    compare,
)
from app.prices.base import DailyBar, PriceFetchError, verify_dividend_adjustment
from app.prices.stooq import StooqSource
from app.prices.tiingo import TiingoSource
from app.prices.store import latest_bar_date, load_bars, refresh_bars, upsert_bars

ACCOUNT = "U0000000"


def _weekdays(start: dt.date, count: int) -> list[dt.date]:
    out, cursor = [], start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += dt.timedelta(days=1)
    return out


class _FakeSource:
    """A `PriceSource` that reads from a dict. Test 53's instrument: if the
    calculation code notices which adapter produced a bar, this fails."""

    name = "fake"

    def __init__(self, bars: list[DailyBar], fail: bool = False):
        self._bars = bars
        self._fail = fail
        self.calls: list[tuple] = []

    def daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> list[DailyBar]:
        self.calls.append((symbol, start, end))
        if self._fail:
            raise PriceFetchError("provider is down")
        return [b for b in self._bars if start <= b.date <= end]


# --- Test 50: cash-flow matching -------------------------------------------


def test_50_single_deposit_tracks_the_index_exactly():
    """§16.6 test 50 — a single $10,000 deposit with no trades produces a
    shadow portfolio that tracks SPY exactly, and the account's own TWR is
    zero because nothing was traded."""
    days = _weekdays(dt.date(2026, 1, 5), 40)
    prices = {day: 500.0 * (1.001**i) for i, day in enumerate(days)}
    flows = {days[0]: 10_000.0}

    portfolio = build_shadow_portfolio(days, flows, prices)

    shares = 10_000.0 / prices[days[0]]
    assert portfolio.final_shares == pytest.approx(shares)
    for point in portfolio.points:
        assert point.shadow_value == pytest.approx(shares * prices[point.day])

    # The shadow portfolio's return is exactly the index's return, since
    # the money went in on day one and never moved.
    first, last = portfolio.points[0], portfolio.points[-1]
    index_return = prices[days[-1]] / prices[days[0]] - 1.0
    assert last.shadow_value / first.shadow_value - 1.0 == pytest.approx(index_return)

    # Account value never moves — no trades — so excess return is the
    # negative of the index's return.
    account = [10_000.0] * len(days)
    result = compare(account, [p.shadow_value for p in portfolio.points])
    assert result.account_return == pytest.approx(0.0)
    assert result.excess_return == pytest.approx(-index_return)


# --- Test 51: total return basis -------------------------------------------


def test_51_adjusted_close_must_beat_price_return_by_the_yield():
    """§16.6 test 51 — asserts the source is dividend-adjusted, not merely
    split-adjusted. A price-only series overstates relative performance by
    roughly the yield every year."""
    days = _weekdays(dt.date(2026, 1, 2), 252)
    bars = []
    for i, day in enumerate(days):
        close = 500.0 * (1.0002**i)
        # Adjusted series carries an extra ~1.2%/yr of reinvested dividends.
        adj = close * (1.012 ** (i / 251))
        bars.append(DailyBar(date=day, close=close, adj_close=adj))

    check = verify_dividend_adjustment(bars)
    assert check["comparable"] is True
    assert check["series_identical"] is False
    assert check["annualized_excess"] == pytest.approx(0.012, abs=0.002)


def test_51_a_price_only_series_is_caught():
    """Stooq hands back close as adj_close and says so; the check must
    notice rather than let a price series pass as total return."""
    days = _weekdays(dt.date(2026, 1, 2), 30)
    bars = [DailyBar(date=d, close=500.0 + i, adj_close=500.0 + i) for i, d in enumerate(days)]
    check = verify_dividend_adjustment(bars)
    assert check["series_identical"] is True
    assert check["excess_return"] == pytest.approx(0.0, abs=1e-12)


# --- Test 52: fractional shares --------------------------------------------


def test_52_shares_are_not_rounded():
    """§16.6 test 52 — "a $1,000 deposit at a $600 close yields 1.6667
    shares". An index has no lot size, and rounding down would hand the
    account a free head start on every single flow."""
    day = dt.date(2026, 1, 5)
    portfolio = build_shadow_portfolio([day], {day: 1000.0}, {day: 600.0})
    assert portfolio.final_shares == pytest.approx(1.6666666, abs=1e-6)
    assert portfolio.points[0].shadow_value == pytest.approx(1000.0)


def test_52_repeated_flows_accumulate_fractionally():
    days = _weekdays(dt.date(2026, 1, 5), 3)
    prices = {days[0]: 600.0, days[1]: 500.0, days[2]: 400.0}
    portfolio = build_shadow_portfolio(days, {d: 1000.0 for d in days}, prices)
    expected = 1000 / 600 + 1000 / 500 + 1000 / 400
    assert portfolio.final_shares == pytest.approx(expected)


# --- Test 53: source independence ------------------------------------------


def test_53_swapping_the_adapter_changes_no_calculation(db):
    """§16.6 test 53 — `price_bars` is the sole read path.

    Two adapters, identical bars, different `name`. Everything downstream
    reads the table, so the computed series must be byte-identical and only
    the provenance column differs.
    """
    days = _weekdays(dt.date(2026, 1, 5), 10)
    bars = [DailyBar(date=d, close=500.0 + i, adj_close=500.0 + i) for i, d in enumerate(days)]

    first = _FakeSource(bars)
    refresh_bars(db, first, "SPY", days[0], days[-1])
    from_first = [(b.date, float(b.adj_close)) for b in load_bars(db, "SPY")]
    assert {b.source for b in load_bars(db, "SPY")} == {"fake"}

    class _OtherSource(_FakeSource):
        name = "other"

    db.query(PriceBar).delete()
    db.flush()
    refresh_bars(db, _OtherSource(bars), "SPY", days[0], days[-1])
    from_second = [(b.date, float(b.adj_close)) for b in load_bars(db, "SPY")]

    assert from_first == from_second
    assert {b.source for b in load_bars(db, "SPY")} == {"other"}


def test_53_provenance_is_recorded_per_row(db):
    days = _weekdays(dt.date(2026, 1, 5), 2)
    upsert_bars(db, "SPY", "tiingo", [DailyBar(date=days[0], close=1, adj_close=1)])
    upsert_bars(db, "SPY", "stooq", [DailyBar(date=days[1], close=2, adj_close=2)])
    assert [b.source for b in load_bars(db, "SPY")] == ["tiingo", "stooq"]


# --- Test 54: stale-data degradation ---------------------------------------


def test_54_a_fetch_failure_leaves_the_cache_intact(db):
    """§16.6 test 54 — the failure must not blank the chart or fail the run."""
    days = _weekdays(dt.date(2026, 1, 5), 5)
    bars = [DailyBar(date=d, close=500.0 + i, adj_close=500.0 + i) for i, d in enumerate(days)]
    refresh_bars(db, _FakeSource(bars), "SPY", days[0], days[-1])
    cached_before = [(b.date, float(b.close)) for b in load_bars(db, "SPY")]

    result = refresh_bars(db, _FakeSource([], fail=True), "SPY", days[0], days[-1] + dt.timedelta(days=7))

    assert result.ok is False
    assert "provider is down" in result.error
    assert result.latest_cached == days[-1]
    assert [(b.date, float(b.close)) for b in load_bars(db, "SPY")] == cached_before


def test_54_an_adapter_raising_anything_is_still_non_fatal(db):
    """An adapter is third-party-shaped code; it must never take a sync down
    no matter what it throws."""

    class _Exploding:
        name = "boom"

        def daily_bars(self, symbol, start, end):
            raise RuntimeError("unexpected")

    result = refresh_bars(db, _Exploding(), "SPY", dt.date(2026, 1, 5), dt.date(2026, 1, 9))
    assert result.ok is False
    assert "RuntimeError" in result.error


def test_54_missing_days_carry_the_last_close_and_are_marked_stale():
    days = _weekdays(dt.date(2026, 1, 5), 5)
    prices = {days[0]: 500.0, days[1]: 510.0}  # the fetch is three days behind

    portfolio = build_shadow_portfolio(days, {days[0]: 1000.0}, prices)

    assert portfolio.stale_days == 3
    assert [p.stale for p in portfolio.points] == [False, False, True, True, True]
    # The line holds its last value rather than dropping to zero.
    assert portfolio.points[-1].shadow_value == pytest.approx(portfolio.points[1].shadow_value)


def test_54_sync_survives_a_dead_price_provider(client_with_flex, monkeypatch):
    """End to end: the endpoint reports the failure and returns 200."""
    import datetime as _dt

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "price_fetch_enabled", True, raising=False)
    # §7.5 fetches from the epoch start up to *today*, so an unpinned clock
    # decides whether this test reaches the provider at all. Pin it past the
    # end of the fixture window; otherwise the range is empty, `refresh_bars`
    # short-circuits before the adapter is called, and a dead provider looks
    # healthy.
    monkeypatch.setattr("app.flex.sync.broker_today", lambda: _dt.date(2031, 8, 11))
    monkeypatch.setattr(
        "app.prices.factory.build_price_source", lambda *a, **k: _FakeSource([], fail=True)
    )

    response = client_with_flex.post("/api/performance/benchmark/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "provider is down" in body["error"]


# --- Test 55: sample-size gate ---------------------------------------------


def test_55_under_thirty_days_returns_insufficient_data():
    """§16.6 test 55 — "the UI should say so rather than print a
    confident-looking beta"."""
    n = MIN_SAMPLE_TRADING_DAYS - 1
    account = [1000.0 * (1.001**i) for i in range(n)]
    shadow = [1000.0 * (1.0008**i) for i in range(n)]

    result = compare(account, shadow)

    assert result.insufficient_sample is True
    assert result.sample_trading_days == n
    assert result.alpha is None
    assert result.beta is None
    assert result.correlation is None
    assert result.information_ratio is None
    assert result.tracking_error is None


def test_55_the_gate_opens_at_exactly_thirty_days():
    n = MIN_SAMPLE_TRADING_DAYS
    account = [1000.0 * (1.001**i) for i in range(n)]
    shadow = [1000.0 * (1.0008**i) for i in range(n)]

    result = compare(account, shadow)

    assert result.insufficient_sample is False
    assert result.beta is not None
    assert result.correlation is not None


def test_flows_are_netted_out_before_anything_is_regressed():
    """The deposit schedule is not a source of correlation.

    Both series receive the same cash on the same days — that is what
    "cash-flow matched" means — so a regression on raw value changes finds
    those two jumps in lockstep and reports beta 1.00 with correlation
    1.00. It is a perfect fit to the funding schedule and says nothing
    about how the account trades.

    Here the account is deliberately *uncorrelated* with the benchmark
    once flows are removed, and a single large deposit sits in the middle.
    """
    import math

    n = 60
    deposit_day = 30
    account, shadow, flows = [1000.0], [1000.0], [0.0]

    for i in range(1, n):
        bench_r = 0.004 * math.sin(i)
        acct_r = 0.004 * math.cos(i * 2.7)  # unrelated to bench_r
        flow = 50_000.0 if i == deposit_day else 0.0
        flows.append(flow)
        shadow.append((shadow[-1] + flow) * (1 + bench_r))
        account.append((account[-1] + flow) * (1 + acct_r))

    naive = compare(account, shadow)  # no flows passed
    aware = compare(account, shadow, flows)

    # The deposit alone drives the naive fit almost perfectly.
    assert naive.correlation == pytest.approx(1.0, abs=0.01)
    # With the flow netted out, the true (absent) relationship shows.
    assert abs(aware.correlation) < 0.4
    assert aware.beta != pytest.approx(naive.beta, abs=0.1)


def test_reported_returns_are_time_weighted_not_growth_multiples():
    """An account that went from $905 to $9,969 on $8,297 of deposits did
    not return 1001%. Reporting the multiple as a "return" is a true
    statement about the funding schedule and a useless one about
    performance."""
    n = 40
    account, shadow, flows = [905.0], [905.0], [0.0]
    for i in range(1, n):
        flow = 8297.0 if i == 10 else 0.0
        flows.append(flow)
        account.append((account[-1] + flow) * 1.001)
        shadow.append((shadow[-1] + flow) * 1.001)

    result = compare(account, shadow, flows)

    assert result.account_final == pytest.approx(account[-1])
    assert result.value_gap == pytest.approx(0.0, abs=1e-6)
    # ~0.1% a day over 39 days, not 1000%.
    assert result.account_return == pytest.approx(1.001**39 - 1, abs=1e-6)
    assert result.account_return < 0.05


def test_beta_of_a_perfect_tracker_is_one():
    n = 60
    shadow = [1000.0 * (1.001**i) for i in range(n)]
    result = compare(list(shadow), shadow)

    assert result.beta == pytest.approx(1.0)
    assert result.correlation == pytest.approx(1.0)
    assert result.alpha == pytest.approx(0.0, abs=1e-12)
    assert result.tracking_error == pytest.approx(0.0, abs=1e-12)
    # Zero tracking error means the information ratio is undefined, not
    # infinite — dividing anyway is how a dashboard prints `inf`.
    assert result.information_ratio is None


def test_a_double_beta_account_is_measured_as_such():
    """Beta needs the benchmark's returns to actually vary.

    A constant-growth series has zero return variance, so a regression on
    it divides by nothing and the answer is floating-point noise. That is
    why the returns here oscillate: beta measures sensitivity to movement,
    and a benchmark that never moves has none to be sensitive to.
    """
    import math

    n = 60
    shadow_returns = [0.004 * math.sin(i) for i in range(n - 1)]

    shadow, account = [1000.0], [1000.0]
    for r in shadow_returns:
        shadow.append(shadow[-1] * (1 + r))
        account.append(account[-1] * (1 + 2 * r))

    result = compare(account, shadow)
    assert result.beta == pytest.approx(2.0, abs=0.02)
    assert result.correlation == pytest.approx(1.0, abs=0.01)


def test_beta_is_withheld_when_the_benchmark_never_moves():
    """A flat benchmark has no variance to regress against. Returning None
    is the honest answer; returning a number would be dividing by noise."""
    n = 60
    flat = [1000.0] * n
    account = [1000.0 * (1.001**i) for i in range(n)]
    result = compare(account, flat)
    assert result.insufficient_sample is False
    assert result.beta is None
    assert result.alpha is None


# --- The store's incremental behaviour --------------------------------------


def test_refresh_is_incremental_but_refetches_recent_bars(db):
    """Providers restate recent bars — a dividend applied a day late
    rewrites the adjusted series behind you — so the overlap is deliberate."""
    days = _weekdays(dt.date(2026, 1, 5), 20)
    bars = [DailyBar(date=d, close=500.0 + i, adj_close=500.0 + i) for i, d in enumerate(days)]

    source = _FakeSource(bars)
    refresh_bars(db, source, "SPY", days[0], days[9])
    assert latest_bar_date(db, "SPY") == days[9]

    refresh_bars(db, source, "SPY", days[0], days[-1])
    _symbol, second_from, _to = source.calls[1]
    assert second_from < days[9], "should re-fetch a restatement overlap"
    assert second_from > days[0], "should not re-fetch the whole history"


def test_upsert_is_idempotent(db):
    day = dt.date(2026, 1, 5)
    upsert_bars(db, "SPY", "fake", [DailyBar(date=day, close=1.0, adj_close=1.0)])
    upsert_bars(db, "SPY", "fake", [DailyBar(date=day, close=2.0, adj_close=2.0)])
    rows = load_bars(db, "SPY")
    assert len(rows) == 1
    assert float(rows[0].close) == 2.0


# --- Adapter parsing (no network) -------------------------------------------


def test_tiingo_adapter_parses_a_response():
    payload = [
        {"date": "2031-08-07T00:00:00.000Z", "close": 500.0, "adjClose": 498.5},
        {"date": "2031-08-08T00:00:00.000Z", "close": 502.0, "adjClose": 500.4},
    ]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    source = TiingoSource("secret-key", client=httpx.Client(transport=transport))

    bars = source.daily_bars("SPY", dt.date(2031, 8, 2), dt.date(2031, 8, 8))

    assert [b.date for b in bars] == [dt.date(2031, 8, 7), dt.date(2031, 8, 8)]
    assert bars[0].adj_close == pytest.approx(498.5)


def test_tiingo_drops_partial_rows_rather_than_defaulting_them():
    """An invented close would flow into the shadow portfolio and be
    indistinguishable from a real one."""
    payload = [
        {"date": "2031-08-07T00:00:00.000Z", "close": 500.0},  # no adjClose
        {"date": "2031-08-08T00:00:00.000Z", "close": 502.0, "adjClose": 500.4},
    ]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    source = TiingoSource("k", client=httpx.Client(transport=transport))
    assert len(source.daily_bars("SPY", dt.date(2026, 8, 1), dt.date(2026, 8, 7))) == 1


def test_tiingo_redacts_its_key_from_errors():
    def _boom(request):
        raise httpx.ConnectError("failed talking to https://api.tiingo.com?token=secret-key")

    source = TiingoSource("secret-key", client=httpx.Client(transport=httpx.MockTransport(_boom)))
    with pytest.raises(PriceFetchError) as exc:
        source.daily_bars("SPY", dt.date(2026, 8, 1), dt.date(2026, 8, 7))
    assert "secret-key" not in str(exc.value)


def test_tiingo_without_a_key_refuses_up_front():
    with pytest.raises(PriceFetchError):
        TiingoSource("")


def test_stooq_adapter_parses_csv_and_flags_its_basis():
    body = "Date,Open,High,Low,Close,Volume\n2031-08-07,499,503,498,500.5,1000\n"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    source = StooqSource(client=httpx.Client(transport=transport))

    bars = source.daily_bars("SPY", dt.date(2031, 8, 2), dt.date(2031, 8, 8))

    assert bars[0].close == pytest.approx(500.5)
    # Price-only, and honest about it rather than labelling close as adjusted.
    assert bars[0].adj_close == bars[0].close
    assert "price-only" in StooqSource.basis_note


def test_stooq_treats_a_200_with_no_data_as_a_failure():
    """Stooq answers an unknown symbol with a 200 and a one-line body."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="No data"))
    source = StooqSource(client=httpx.Client(transport=transport))
    with pytest.raises(PriceFetchError):
        source.daily_bars("NOPE", dt.date(2026, 8, 1), dt.date(2026, 8, 7))
