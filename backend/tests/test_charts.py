"""§9 — the chart grid, global filters and the calendar heatmap."""

import datetime as dt

import pytest

from app.charts.dimensions import DIMENSIONS
from app.charts.grid import THIN_BUCKET_UNITS, UnknownAxis, build_chart
from app.charts.metrics import METRICS
from app.charts.units import ChartUnit


def _unit(
    unit_id: int = 1,
    net_pnl: float = 100.0,
    *,
    closed: dt.datetime | None = None,
    symbol: str = "ACME",
    excluded: bool = False,
    **overrides,
) -> ChartUnit:
    closed = closed or dt.datetime(2031, 6, 16, 15, 0, tzinfo=dt.timezone.utc)
    base = dict(
        unit_id=unit_id,
        unit_kind="trade",
        account_id="U0000000",
        symbol=symbol,
        underlying=symbol,
        asset_class="STK",
        strategy_type="LONG_STOCK",
        book="Holdings",
        side="LONG",
        status="CLOSED",
        origin="TRADED",
        opened_at=closed - dt.timedelta(days=2),
        closed_at=closed,
        trading_day=closed.date(),
        duration_seconds=172800.0,
        quantity=10.0,
        multiplier=1.0,
        open_price=100.0,
        stop_loss=None,
        net_pnl=net_pnl,
        gross_pnl=net_pnl + 1.0,
        commissions=-1.0,
        cost_basis=1000.0,
        return_pct=net_pnl / 1000.0,
        max_risk=None,
        return_on_risk=None,
        r_value=None,
        dte_at_open=None,
        excluded_from_win_rate=excluded,
        excluded_from_duration=False,
        straddles_epoch=False,
    )
    base.update(overrides)
    return ChartUnit(**base)


# --- The grid itself --------------------------------------------------------


def test_every_dimension_crossed_with_every_metric_produces_a_chart():
    """§9.2 — "Any metric × any dimension = the chart grid."

    The claim is that ~200 charts come from one piece of code, so the test
    is the full cross product rather than a sample: a dimension that only
    works with some metrics would break the claim quietly.
    """
    units = [
        _unit(1, 120.0, symbol="ACME"),
        _unit(2, -40.0, symbol="BIDM", closed=dt.datetime(2031, 7, 3, 14, tzinfo=dt.timezone.utc)),
        _unit(3, 60.0, symbol="ACME", closed=dt.datetime(2031, 7, 4, 20, tzinfo=dt.timezone.utc)),
    ]

    for dimension in DIMENSIONS:
        for metric in METRICS:
            body = build_chart(units, dimension, metric)
            assert body["dimension"] == dimension
            assert body["metric"] == metric
            assert isinstance(body["buckets"], list)


def test_buckets_carry_the_ids_behind_them():
    """§9 opens with "click a data point to drill into the underlying
    trades". Building that generically once is the point."""
    units = [_unit(1, 100.0), _unit(2, 50.0)]
    body = build_chart(units, "symbol", "net_pnl")
    assert body["buckets"][0]["unit_ids"] == [1, 2]


def test_day_of_week_sorts_by_weekday_not_alphabetically():
    monday = dt.datetime(2031, 6, 16, 15, tzinfo=dt.timezone.utc)
    friday = dt.datetime(2031, 6, 20, 15, tzinfo=dt.timezone.utc)
    body = build_chart([_unit(1, closed=friday), _unit(2, closed=monday)], "day_of_week", "net_pnl")
    assert [b["label"] for b in body["buckets"]] == ["Monday", "Friday"]


def test_thin_buckets_are_flagged_rather_than_hidden():
    """A 100% win rate off one trade is a tall green bar unless the chart
    says how thin it is (§8.2's argument, applied to charts)."""
    body = build_chart([_unit(1, 100.0)], "symbol", "win_rate")
    assert body["buckets"][0]["thin"] is True
    assert body["thin_bucket_threshold"] == THIN_BUCKET_UNITS


def test_units_with_no_value_on_an_axis_are_reported_not_bucketed():
    """An "Unknown" column gets read as a finding. Equities have no DTE."""
    body = build_chart([_unit(1), _unit(2)], "dte_at_open", "net_pnl")
    assert body["buckets"] == []
    assert body["unplaced_units"] == 2
    assert "excluded rather than bucketed" in body["unplaced_note"]


def test_win_rate_ignores_units_excluded_from_it():
    """An orphan close has no outcome; §8.2 excludes it and so must a chart
    derived from wins and losses."""
    units = [_unit(1, 100.0), _unit(2, -500.0, excluded=True)]
    body = build_chart(units, "symbol", "win_rate")
    assert body["buckets"][0]["value"] == pytest.approx(1.0)


def test_profit_factor_is_none_not_zero_when_there_are_no_losses():
    """Zero would read as "terrible"; the truth is "undefined"."""
    body = build_chart([_unit(1, 100.0), _unit(2, 50.0)], "symbol", "profit_factor")
    assert body["buckets"][0]["value"] is None


def test_return_on_risk_skips_unbounded_risk():
    """§6.3 — pooling a naked short into return-on-risk reports an infinite
    return on no capital."""
    units = [_unit(1, 100.0, max_risk=None), _unit(2, 50.0, max_risk=500.0)]
    body = build_chart(units, "symbol", "return_on_risk")
    assert body["buckets"][0]["value"] == pytest.approx(50.0 / 500.0)


def test_limit_keeps_the_top_and_bottom_buckets():
    """§9.3 #8 — "P&L by underlying (top/bottom 10)"."""
    units = [_unit(i, float(i * 10), symbol=f"S{i:02d}") for i in range(1, 11)]
    body = build_chart(units, "symbol", "net_pnl", limit=2)
    labels = {b["label"] for b in body["buckets"]}
    assert {"S01", "S02", "S09", "S10"} == labels
    assert body["truncated_buckets"] == 6
    # Totals still count the excluded buckets: they were hidden, not dropped.
    assert body["bucket_count"] == 10


def test_unknown_axes_are_refused_with_the_available_ones():
    with pytest.raises(UnknownAxis) as exc:
        build_chart([], "phase_of_moon", "net_pnl")
    assert "symbol" in str(exc.value)

    with pytest.raises(UnknownAxis):
        build_chart([], "symbol", "sharpe")


def test_tag_buckets_overlap_and_say_so():
    """§9.1's tag axis is the one place buckets legitimately overlap.

    A trade carrying three tags lands in three buckets, so the columns do
    not sum to the period total. Each column is correct; the total across
    them is not a total, and the response says so rather than letting the
    double counting read as a discrepancy.
    """
    unit = _unit(1, 100.0)
    unit.tags = ["assigned", "earnings", "mistake"]

    body = build_chart([unit], "tag", "net_pnl")

    assert {b["key"] for b in body["buckets"]} == {"assigned", "earnings", "mistake"}
    assert all(b["value"] == pytest.approx(100.0) for b in body["buckets"])
    assert body["overlapping"] is True
    assert "do not sum" in body["overlapping_note"]


def test_an_untagged_unit_is_unplaced_rather_than_bucketed():
    body = build_chart([_unit(1, 100.0)], "tag", "net_pnl")
    assert body["buckets"] == []
    assert body["unplaced_units"] == 1


def test_non_overlapping_dimensions_say_so_too():
    body = build_chart([_unit(1, 100.0)], "symbol", "net_pnl")
    assert body["overlapping"] is False
    assert body["overlapping_note"] is None


# --- Through the API --------------------------------------------------------


def test_chart_endpoint_serves_the_grid(client_with_flex):
    body = client_with_flex.get(
        "/api/charts", params={"dimension": "underlying", "metric": "net_pnl"}
    ).json()
    assert body["buckets"]
    assert body["metric_unit"] == "money"
    assert body["analysis_mode"] in ("STRATEGY", "LEG")


def test_axis_catalog_lists_both_libraries(client_with_flex):
    body = client_with_flex.get("/api/charts/axes").json()
    assert len(body["dimensions"]) >= 15
    assert len(body["metrics"]) >= 10
    # Every dimension §9.1 names is now buildable — tags landed with §11.1.
    assert {d["key"] for d in body["dimensions"]} >= {"tag", "tag_group"}
    assert body["unavailable"] == []


def test_totals_are_invariant_across_the_analysis_mode(client_with_flex):
    """§6.4 — "the sum of legs equals the sum of groups". Charts must obey
    the same invariant, or the toggle silently changes the P&L."""
    leg = client_with_flex.get(
        "/api/charts", params={"dimension": "account", "metric": "net_pnl", "mode": "LEG"}
    ).json()
    strategy = client_with_flex.get(
        "/api/charts", params={"dimension": "account", "metric": "net_pnl", "mode": "STRATEGY"}
    ).json()

    assert leg["buckets"][0]["value"] == pytest.approx(strategy["buckets"][0]["value"], abs=0.01)
    # ...while the unit counts differ, which is the entire point.
    assert leg["unit_count"] != strategy["unit_count"]


def test_a_bad_axis_is_a_400_not_a_500(client_with_flex):
    assert client_with_flex.get("/api/charts", params={"dimension": "nope"}).status_code == 400
    assert client_with_flex.get("/api/charts", params={"metric": "nope"}).status_code == 400


# --- Global filters ---------------------------------------------------------


def test_filters_narrow_the_chart_and_are_echoed_back(client_with_flex):
    """A chart whose filters are invisible is a chart that gets misread."""
    unfiltered = client_with_flex.get(
        "/api/charts", params={"dimension": "underlying", "metric": "net_pnl"}
    ).json()
    filtered = client_with_flex.get(
        "/api/charts",
        params={"dimension": "underlying", "metric": "net_pnl", "books": "Holdings"},
    ).json()

    assert filtered["unit_count"] < unfiltered["unit_count"]
    assert filtered["filters"]["books"] == ["Holdings"]
    assert filtered["filters"]["active"] is True


def test_a_date_range_filter_applies(client_with_flex):
    body = client_with_flex.get(
        "/api/charts",
        params={
            "dimension": "month",
            "metric": "net_pnl",
            "date_from": "2031-07-01",
            "date_to": "2031-07-31",
        },
    ).json()
    assert all(b["key"] == "2031-07" for b in body["buckets"])


def test_right_click_drill_down_filters_by_bucket(client_with_flex):
    """§9 — "right-click to apply that bucket as a global filter"."""
    full = client_with_flex.get(
        "/api/charts", params={"dimension": "day_of_week", "metric": "net_pnl"}
    ).json()
    target = full["buckets"][0]["key"]

    drilled = client_with_flex.get(
        "/api/charts",
        params={
            "dimension": "day_of_week",
            "metric": "net_pnl",
            "filter_dimension": "day_of_week",
            "filter_keys": target,
        },
    ).json()

    assert [b["key"] for b in drilled["buckets"]] == [target]
    assert drilled["buckets"][0]["value"] == pytest.approx(full["buckets"][0]["value"])


def test_filter_options_come_from_the_data(client_with_flex):
    body = client_with_flex.get("/api/charts/filter-options").json()
    assert "Options Income" in body["books"]
    assert body["underlyings"]
    assert body["date_from"] <= body["date_to"]


# --- The calendar (§9.3 #3) -------------------------------------------------


def test_calendar_returns_days_and_monthly_rollups(client_with_flex):
    body = client_with_flex.get("/api/calendar").json()
    assert body["days"]
    assert body["months"]
    assert body["months"][0]["month"] < body["months"][-1]["month"]


def test_calendar_keeps_flat_days(client_with_flex):
    """An empty cell and a flat cell mean different things, and the heatmap
    is where that difference is most visible."""
    body = client_with_flex.get("/api/calendar").json()
    assert body["scale"]["flat_days"] > 0
    assert body["scale"]["active_days"] > 0
    assert any(d["trade_count"] == 0 for d in body["days"])


def test_calendar_scale_ignores_flat_days(client_with_flex):
    """Including the zeroes would compress every colour toward the middle
    and make a flat month look like a good one."""
    body = client_with_flex.get("/api/calendar").json()
    assert body["scale"]["max_gain"] > 0
    assert body["scale"]["max_loss"] < 0


def test_calendar_filters_by_month(client_with_flex):
    body = client_with_flex.get("/api/calendar", params={"year": 2031, "month": 7}).json()
    assert {d["trading_day"][:7] for d in body["days"]} == {"2031-07"}


def test_calendar_rejects_a_month_without_a_year(client_with_flex):
    assert client_with_flex.get("/api/calendar", params={"month": 7}).status_code == 400
    assert (
        client_with_flex.get("/api/calendar", params={"year": 2026, "month": 13}).status_code
        == 400
    )
