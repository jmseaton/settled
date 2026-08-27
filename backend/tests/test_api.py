import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from tests.conftest import FIXTURES


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _upload(client, name="fixture-complete.tlg"):
    content = (FIXTURES / name).read_bytes()
    return client.post("/api/import/tlg", files={"file": (name, io.BytesIO(content), "text/plain")})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_import_returns_report(client):
    resp = _upload(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows_inserted"] == 79
    assert body["rows_duplicate"] == 0
    assert body["reconciliation"]["passed"] is True
    assert body["account_id"] == "U0000000"


def test_import_rejects_malformed_file(client):
    bad = io.BytesIO(b"STOCK_TRANSACTIONS\nSTK_TRD|only|three|fields\nEOF\n")
    resp = client.post("/api/import/tlg", files={"file": ("bad.tlg", bad, "text/plain")})
    assert resp.status_code == 400


def test_executions_endpoint(client):
    _upload(client)
    body = client.get("/api/executions").json()
    assert body["total"] == 79
    first = body["items"][0]
    assert {"broker_trade_id", "symbol", "trading_day", "cash_flow"} <= set(first)


def test_timestamps_are_served_in_eastern_not_utc(client):
    """§14.1 — display in America/New_York. The fixture's ACME fill is
    10:06:11 ET; serving the raw UTC instant would show 14:06:11 and
    disagree with the broker statement."""
    _upload(client)
    execs = client.get("/api/executions").json()["items"]
    acme = next(e for e in execs if e["broker_trade_id"] == "5488585286")
    assert acme["executed_at"].startswith("2031-08-05T10:06:11")
    assert acme["executed_at"].endswith("-04:00")

    # An evening futures fill: 18:12:09 ET on 07-05, trading day 07-06.
    fop = next(e for e in execs if e["broker_trade_id"] == "921666627")
    assert fop["executed_at"].startswith("2031-07-06T18:12:09")
    assert fop["trading_day"] == "2031-07-07"


def test_trades_endpoint(client):
    _upload(client)
    body = client.get("/api/trades").json()
    assert body["total"] > 0
    bcgx = [t for t in body["items"] if t["symbol"] == "BCGX"]
    assert len(bcgx) == 1
    assert bcgx[0]["net_pnl"] == pytest.approx(257.017616, abs=0.01)


def test_stats_defaults_to_options_income_book(client):
    _upload(client)
    body = client.get("/api/stats").json()
    assert body["book"] == "Options Income"

    # §4.11 (Phase 3) — snapshots now cover every trading day in the
    # measured period, not only the days something closed. The sample the
    # ≥30-day gate counts is therefore the length of the period, which is
    # the right denominator for a daily-return statistic: the old
    # activity-only series handed Sharpe a return stream with all its
    # zeroes deleted. The fixture spans ten weeks, so the gate opens — and
    # `proxy_note` is what carries the "ten weeks is still noise" caveat.
    ratios = body["ratios"]
    assert ratios["sample_trading_days"] > ratios["min_sample_trading_days"]
    assert ratios["insufficient_sample"] is False

    pooled = client.get("/api/stats", params={"book": ""}).json()
    assert pooled["book"] == "ALL"
    assert pooled["pnl"]["total_net_pnl"] != body["pnl"]["total_net_pnl"]


def test_equity_curve_is_monotonic_in_days(client):
    _upload(client)
    items = client.get("/api/equity-curve").json()["items"]
    days = [i["trading_day"] for i in items]
    assert days == sorted(days)


def test_expiry_watch_endpoint(client):
    _upload(client)
    body = client.get("/api/expiry-watch", params={"as_of": "2031-08-08"}).json()
    assert len(body["items"]) == 3
    assert "no probability" in body["disclaimer"].lower() or "probability" in body["disclaimer"].lower()
    assert body["items"][0]["expiry"] <= body["items"][-1]["expiry"]


def test_positions_endpoint(client):
    _upload(client)
    items = client.get("/api/positions").json()["items"]
    by_symbol = {i["symbol"]: i["quantity"] for i in items}
    assert by_symbol["BIDM"] == pytest.approx(12.0)
    assert "BCGX" not in by_symbol


def test_endpoints_404_before_any_import(client):
    assert client.get("/api/stats").status_code == 404


def _upload_flex(client):
    content = (FIXTURES / "fixture-flex.xml").read_bytes()
    return client.post(
        "/api/import", files={"file": ("fixture-flex.xml", io.BytesIO(content), "text/xml")}
    )


def test_import_autodetects_flex_xml(client):
    body = _upload_flex(client).json()
    assert body["source_format"] == "FLEX_XML"
    assert body["rows_inserted"] == 78
    assert body["rows_parsed"]["closed_lots"] == 40
    assert body["reconciliation"]["passed"] is True


def test_import_autodetects_tlg(client):
    content = (FIXTURES / "fixture-complete.tlg").read_bytes()
    body = client.post(
        "/api/import", files={"file": ("fixture-complete.tlg", io.BytesIO(content), "text/plain")}
    ).json()
    assert body["source_format"] == "TLG"
    assert body["rows_inserted"] == 79
    assert body["closed_lot_check"] is None, "the .tlg format carries no closed lots"


def test_closed_lot_check_is_reported_to_the_client(client):
    check = _upload_flex(client).json()["closed_lot_check"]
    assert check["passed"] is True
    assert check["trades_checked"] == check["trades_matched"] == 35
    assert check["skipped_transfers"] == 1


def test_settings_endpoint_exposes_the_active_policy(client):
    body = client.get("/api/settings").json()
    assert body["transfer_basis_policy"] == "TRANSFER_MARK"
    assert "fifoPnlRealized" in body["transfer_basis_note"]


def test_changing_basis_policy_rebuilds_trades(client):
    _upload_flex(client)
    before = next(t for t in client.get("/api/trades").json()["items"] if t["symbol"] == "BCGX")
    assert before["net_pnl"] == pytest.approx(-41.556193, abs=0.01)

    resp = client.put("/api/settings/transfer-basis-policy", params={"policy": "ORIGINAL_COST"})
    assert resp.status_code == 200
    assert resp.json()["rebuilt"] is True

    after_body = client.get("/api/trades").json()
    after = next(t for t in after_body["items"] if t["symbol"] == "BCGX")
    assert after["net_pnl"] == pytest.approx(66.33080756, abs=0.01)
    assert after_body["transfer_basis_policy"] == "ORIGINAL_COST"


def test_unknown_basis_policy_is_rejected(client):
    assert client.put("/api/settings/transfer-basis-policy", params={"policy": "NOPE"}).status_code == 400


def test_groups_endpoint_returns_strategies_with_legs(client):
    _upload_flex(client)
    body = client.get("/api/groups").json()
    assert body["total"] == 24

    vertical = next(
        g for g in body["items"]
        if {"ACME  310813P00172000", "ACME  310813P00180600"} == {leg["symbol"] for leg in g["legs"]}
    )
    assert vertical["strategy_type"] == "VERTICAL_PUT"
    assert vertical["net_pnl"] == pytest.approx(42.372648, abs=0.01)
    assert vertical["max_risk"] == pytest.approx(800.23, abs=0.01)
    assert len(vertical["legs"]) == 2


def test_risk_stats_endpoint_refuses_to_pool(client):
    _upload_flex(client)
    body = client.get("/api/stats/risk").json()
    assert body["pooled_return_on_risk"] is None
    assert "not pooled" in body["refusal_reason"]
    assert len({s["strategy_type"] for s in body["segments"]}) > 1


def test_changing_basis_policy_also_rebuilds_groups(client):
    """Groups are derived from trades, so a rebuild that skips them leaves
    every group orphaned and every strategy view silently empty."""
    _upload_flex(client)
    before = client.get("/api/groups").json()
    assert before["total"] == 24

    client.put("/api/settings/transfer-basis-policy", params={"policy": "ORIGINAL_COST"})

    after = client.get("/api/groups").json()
    assert after["total"] == 24
    assert all(g["leg_count"] > 0 for g in after["items"])
    trades = client.get("/api/trades").json()["items"]
    assert all(t["trade_group_id"] is not None for t in trades)


def test_sync_health_before_any_sync(client):
    body = client.get("/api/sync/health").json()
    assert body["last_success_at"] is None
    assert body["is_stale"] is False
    assert body["needs_attention"] is False
    assert body["staleness_threshold_business_days"] == 2


def test_sync_runs_endpoint_is_empty_before_any_sync(client):
    assert client.get("/api/sync/runs").json()["items"] == []


def test_manual_sync_without_credentials_explains_itself(client):
    """§3.10 — credentials live in the environment, so an unconfigured
    install must say which variables to set rather than 500."""
    resp = client.post("/api/sync/run")
    assert resp.status_code == 400
    assert "SETTLED_FLEX_TOKEN" in resp.json()["detail"]


def test_transferred_trade_is_flagged_for_the_ui(client):
    _upload_flex(client)
    body = client.get("/api/trades").json()
    bcgx = next(t for t in body["items"] if t["symbol"] == "BCGX")
    assert bcgx["origin"] == "TRANSFERRED"
    assert bcgx["has_synthetic_leg"] is True
    assert bcgx["excluded_from_duration"] is True
    assert body["transfer_basis_policy"] == "TRANSFER_MARK"
