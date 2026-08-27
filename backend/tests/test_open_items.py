"""§14.6 and §6.6 — the items the spec deliberately left open.

Three of the four are resolvable by building something; the fourth
(§14.6.2, assignment conventions in practice) is not, and the test for it
asserts only that the re-read is put in front of the user at the moment it
becomes possible.
"""

import math

import pytest

from app.charts.dashboard import (
    DEFAULT_PANELS,
    InvalidPanel,
    parse_layout,
    validate_panel,
)
from app.charts.trend import build_trend, UnknownMetric
from app.stats.significance import (
    describe_sharpe,
    observations_for_significance,
    sharpe_standard_error,
)
from tests.test_charts import _unit


# --- §14.6.3 — the sample-size threshold ------------------------------------


def test_lo_2002_standard_error():
    """SE(SR) = sqrt((1 + SR²/2) / n), per-period."""
    assert sharpe_standard_error(0.0, 100) == pytest.approx(0.1)
    assert sharpe_standard_error(1.0, 100) == pytest.approx(math.sqrt(1.5 / 100))
    assert sharpe_standard_error(0.25, 62) == pytest.approx(
        math.sqrt((1 + 0.03125) / 62), rel=1e-9
    )
    # Fewer than two observations has no estimable error.
    assert sharpe_standard_error(1.0, 1) is None


def test_the_error_shrinks_with_the_square_root_of_n():
    assert sharpe_standard_error(1.0, 400) == pytest.approx(
        sharpe_standard_error(1.0, 100) / 2
    )


def test_a_bigger_sharpe_needs_fewer_observations():
    """The reason "30 days" could never be right in general: the answer
    depends on the Sharpe being measured."""
    weak = observations_for_significance(0.5 / math.sqrt(252))
    strong = observations_for_significance(2.0 / math.sqrt(252))
    assert weak > strong
    # A realistic annualized Sharpe of 1.0 needs years, not weeks.
    assert observations_for_significance(1.0 / math.sqrt(252)) > 900


def test_a_zero_sharpe_is_never_distinguishable_from_zero():
    assert observations_for_significance(0.0) is None


def test_the_fixture_sized_sharpe_does_not_clear_zero():
    """The finding that makes this worth doing.

    An annualized Sharpe of 3.91 over 62 observations sounds spectacular
    and has a 95% interval that includes zero. The old ≥30-day gate would
    have printed it bare.
    """
    described = describe_sharpe(3.9089, 62)

    assert described["standard_error"] == pytest.approx(2.046, abs=0.01)
    low, high = described["confidence_95"]
    assert low < 0 < high
    assert described["significant_at_95"] is False
    assert described["observations_needed_95"] == 66
    assert "not yet distinguishable" in described["note"]


def test_a_well_supported_sharpe_says_so():
    described = describe_sharpe(1.5, 2000)
    assert described["significant_at_95"] is True
    assert described["confidence_95"][0] > 0
    assert "excludes zero" in described["note"]


def test_the_method_names_its_own_assumption():
    """Lo's formula assumes IID returns; trading returns are not. The
    interval is a floor on the uncertainty, and the payload says so rather
    than presenting it as a bound."""
    described = describe_sharpe(1.0, 100)
    assert "IID" in described["method"]
    assert "floor" in described["method"]


def test_ratios_carry_their_significance(client_with_flex):
    client_with_flex.put(
        "/api/settings/epoch",
        json={"tracking_start_date": "2031-05-12", "tracking_start_value": 389.15029091478},
    )
    ratios = client_with_flex.get("/api/performance").json()["ratios"]

    significance = ratios["sharpe_significance"]
    assert significance["sharpe"] == pytest.approx(ratios["sharpe"])
    assert significance["standard_error"] is not None
    assert len(significance["confidence_95"]) == 2


def test_the_threshold_is_configurable(client_with_flex, monkeypatch):
    """It is a display preference now, not a statistical claim — the
    interval carries that."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "min_sample_trading_days", 500, raising=False)
    client_with_flex.put(
        "/api/settings/epoch",
        json={"tracking_start_date": "2031-05-12", "tracking_start_value": 389.15029091478},
    )
    ratios = client_with_flex.get("/api/performance").json()["ratios"]
    assert ratios["insufficient_sample"] is True
    assert ratios["min_sample_trading_days"] == 500


# --- §9.4 — the trend chart -------------------------------------------------


def _sequence(values: list[float]):
    import datetime as dt

    return [
        _unit(
            i + 1,
            v,
            closed=dt.datetime(2031, 6, 2, 15, 0, tzinfo=dt.timezone.utc)
            + dt.timedelta(days=i),
        )
        for i, v in enumerate(values)
    ]


def test_the_trend_axis_is_per_trade_not_per_date():
    """§9.4 — spacing by decisions made, not by time elapsed. That is what
    makes a behaviour change visible."""
    body = build_trend(_sequence([10.0, 20.0, 30.0]), "net_pnl", window=2)

    assert body["axis"] == "per_trade"
    assert [p["sequence"] for p in body["points"]] == [1, 2, 3]
    assert body["unit_count"] == 3


def test_the_moving_average_waits_for_a_full_window():
    """A shorter window drawn early would look smoother than the data is."""
    body = build_trend(_sequence([10.0, 20.0, 30.0, 40.0]), "net_pnl", window=3)
    sma = [p["sma"] for p in body["points"]]

    assert sma[0] is None and sma[1] is None
    assert sma[2] == pytest.approx(20.0)
    assert sma[3] == pytest.approx(30.0)
    assert "deliberately absent" in body["window_note"]


def test_the_ema_reacts_sooner_than_the_sma():
    """Why both are offered: the EMA is the better tool for spotting that
    something has already changed."""
    body = build_trend(_sequence([10.0] * 10 + [100.0] * 3), "net_pnl", window=5)
    last = body["points"][-1]
    assert last["ema"] > last["sma"]


def test_units_undefined_on_the_metric_are_skipped_not_zeroed():
    """A trade with no return-on-risk has none; averaging a zero in would
    drag the line toward a number nobody earned."""
    units = _sequence([10.0, 20.0])
    body = build_trend(units, "return_on_risk", window=2)
    assert body["undefined_units"] == 2
    assert all(p["value"] is None for p in body["points"])


def test_the_trend_runs_in_close_order():
    import datetime as dt

    later = _unit(1, 50.0, closed=dt.datetime(2031, 7, 2, tzinfo=dt.timezone.utc))
    earlier = _unit(2, 10.0, closed=dt.datetime(2031, 6, 2, tzinfo=dt.timezone.utc))
    body = build_trend([later, earlier], "net_pnl", window=2)
    assert [p["unit_id"] for p in body["points"]] == [2, 1]


def test_an_unknown_metric_is_refused_with_the_options():
    with pytest.raises(UnknownMetric) as exc:
        build_trend([], "vibes", window=5)
    assert "net_pnl" in str(exc.value)


def test_trend_endpoint_carries_the_regime_markers(client_with_flex):
    """§6.6 — the markers ride along, so the chart draws them without a
    second round trip."""
    client_with_flex.post(
        "/api/regime-markers",
        json={"occurred_on": "2031-07-13", "label": "MES to naked shorts"},
    )
    body = client_with_flex.get(
        "/api/charts/trend", params={"metric": "net_pnl", "window": 5, "mode": "LEG"}
    ).json()

    assert body["points"]
    assert [m["label"] for m in body["regime_markers"]] == ["MES to naked shorts"]


# --- §6.6 — regime markers --------------------------------------------------


def test_a_regime_marker_records_the_switch(client_with_flex):
    """The fixture's own case: MES verticals through 07-05, naked shorts
    from 07-12."""
    created = client_with_flex.post(
        "/api/regime-markers",
        json={
            "occurred_on": "2031-07-13",
            "label": "MES: verticals → naked short puts",
            "note": "Capital allowed the switch.",
        },
    ).json()

    assert created["occurred_on"] == "2031-07-13"
    listing = client_with_flex.get("/api/regime-markers").json()
    assert len(listing["items"]) == 1
    assert "does not detect" in listing["note"]


def test_a_marker_needs_a_label(client_with_flex):
    response = client_with_flex.post("/api/regime-markers", json={"occurred_on": "2031-07-13"})
    assert response.status_code == 400
    assert "label" in response.json()["detail"]


def test_a_marker_needs_a_readable_date(client_with_flex):
    response = client_with_flex.post(
        "/api/regime-markers", json={"occurred_on": "last Tuesday", "label": "x"}
    )
    assert response.status_code == 400


def test_markers_come_back_in_date_order(client_with_flex):
    for day in ("2031-08-02", "2031-06-02", "2031-07-13"):
        client_with_flex.post("/api/regime-markers", json={"occurred_on": day, "label": day})
    days = [m["occurred_on"] for m in client_with_flex.get("/api/regime-markers").json()["items"]]
    assert days == sorted(days)


def test_a_marker_can_be_removed(client_with_flex):
    created = client_with_flex.post(
        "/api/regime-markers", json={"occurred_on": "2031-07-13", "label": "oops"}
    ).json()
    client_with_flex.delete(f"/api/regime-markers/{created['id']}")
    assert client_with_flex.get("/api/regime-markers").json()["items"] == []


# --- §14.6.1 — the curatable dashboard --------------------------------------


def test_the_default_layout_is_the_spec_set(client_with_flex):
    body = client_with_flex.get("/api/dashboard").json()
    assert len(body["panels"]) == len(DEFAULT_PANELS)
    assert body["customized"] is False
    assert "guess" in body["note"]


def test_a_curated_layout_persists(client_with_flex):
    """The point of §14.6.1: curation becomes something you do continuously
    rather than a decision the spec has to get right in advance."""
    client_with_flex.put(
        "/api/dashboard",
        json={
            "panels": [
                {"key": "pnl_dow", "title": "P&L by weekday", "dimension": "day_of_week", "metric": "net_pnl"},
                {"key": "calendar", "title": "Calendar", "kind": "special"},
            ]
        },
    )
    body = client_with_flex.get("/api/dashboard").json()
    assert [p["key"] for p in body["panels"]] == ["pnl_dow", "calendar"]
    assert body["customized"] is True


def test_resetting_returns_to_the_default(client_with_flex):
    client_with_flex.put(
        "/api/dashboard",
        json={"panels": [{"key": "calendar", "title": "Calendar", "kind": "special"}]},
    )
    body = client_with_flex.delete("/api/dashboard").json()
    assert len(body["panels"]) == len(DEFAULT_PANELS)
    assert body["customized"] is False


def test_a_bad_panel_is_refused_with_the_reason(client_with_flex):
    response = client_with_flex.put(
        "/api/dashboard",
        json={"panels": [{"key": "x", "dimension": "phase_of_moon", "metric": "net_pnl"}]},
    )
    assert response.status_code == 400
    assert "phase_of_moon" in response.json()["detail"]


def test_an_empty_layout_is_refused(client_with_flex):
    assert client_with_flex.put("/api/dashboard", json={"panels": []}).status_code == 400


def test_every_default_panel_validates():
    """The shipped set has to survive its own validator, or the first save
    would reject the thing the app just rendered."""
    for panel in DEFAULT_PANELS:
        assert validate_panel(panel.as_dict()).key == panel.key


def test_a_panel_that_stopped_validating_is_dropped_not_fatal():
    """Losing one panel is recoverable; a dashboard that refuses to render
    is not."""
    layout = parse_layout(
        [
            {"key": "good", "dimension": "symbol", "metric": "net_pnl"},
            {"key": "gone", "dimension": "removed_dimension", "metric": "net_pnl"},
        ]
    )
    assert [p.key for p in layout.panels] == ["good"]


def test_an_unknown_special_panel_is_refused():
    with pytest.raises(InvalidPanel):
        validate_panel({"key": "teleporter", "kind": "special"})


# --- §14.6.2 — the item code cannot close -----------------------------------


def test_the_assignment_draft_carries_the_conventions_to_re_read(client_with_flex):
    """§14.6.2 asks for a re-read "the first time an assignment actually
    happens". Code cannot do the re-reading, but it can make sure the
    conventions are in front of the reader at that moment rather than in a
    spec appendix they would have to remember.
    """
    from tests.test_assignment import _short_put_assigned, _upload

    _upload(client_with_flex, _short_put_assigned())

    notes = client_with_flex.get("/api/notes", params={"scope": "GROUP"}).json()["items"]
    draft = next(n for n in notes if n["draft"])
    body = draft["body_md"]

    assert "§14.6.2" in body
    # The four choices worth confirming against a real event.
    assert "P&L is split, not rolled" in body
    assert "Risk at open is frozen" in body
    assert "Durations are split" in body
    assert "Nothing here was inferred" in body
    assert "§5.4 is the section to revisit" in body
