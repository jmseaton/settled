"""§4.8, §4.9, §10.3, §11 — tags, notes, bulk operations, manual adjustments.

The thread running through all of it: **trades are deleted and re-derived
on every import**, so anything a user writes has to be keyed to something
that survives. Most of these tests are really testing that one property
from different angles.
"""

import datetime as dt
import io
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "fixtures"


def _reimport(client):
    content = (FIXTURES / "fixture-flex.xml").read_bytes()
    return client.post(
        "/api/import", files={"file": ("fixture-flex.xml", io.BytesIO(content), "text/xml")}
    ).json()


def _a_trade(client) -> dict:
    """A closed trade with a real opening execution, so it can be anchored."""
    trades = client.get("/api/trades").json()["items"]
    return next(t for t in trades if not t["has_synthetic_leg"] and t["closed_at"])


# --- Tags (§11.1) -----------------------------------------------------------


def test_tagging_a_trade_and_reading_it_back(client_with_flex):
    trade = _a_trade(client_with_flex)
    client_with_flex.post(
        f"/api/trades/{trade['id']}/tags", json={"name": "earnings", "group_name": "Setup"}
    )

    tags = client_with_flex.get("/api/tags").json()
    earnings = next(t for t in tags["items"] if t["name"] == "earnings")
    assert earnings["group_name"] == "Setup"
    assert earnings["usage_count"] == 1
    assert "Setup" in tags["groups"]


def test_tags_survive_a_reimport(client_with_flex):
    """The whole reason journal data is anchored on execution ids.

    A tag lost to a routine nightly sync is worse than no tagging at all,
    because you stop trusting the ones that remain.
    """
    trade = _a_trade(client_with_flex)
    client_with_flex.post(f"/api/trades/{trade['id']}/tags", json={"name": "keeper"})

    _reimport(client_with_flex)

    tags = client_with_flex.get("/api/tags").json()["items"]
    keeper = next(t for t in tags if t["name"] == "keeper")
    assert keeper["usage_count"] == 1, "the tag reattached to the rebuilt trade"

    # ...and it is on the *same* trade, not merely still in the table.
    chart = client_with_flex.get(
        "/api/charts", params={"dimension": "tag", "metric": "net_pnl", "mode": "LEG"}
    ).json()
    bucket = next(b for b in chart["buckets"] if b["key"] == "keeper")
    assert bucket["net_pnl"] == pytest.approx(trade["net_pnl"], abs=0.01)


def test_a_synthetic_trade_refuses_a_tag_and_says_why(client_with_flex):
    """§5.5 — a synthesized opening leg carries no broker identifier, so a
    tag on it would silently vanish. Refusing beats accepting-then-losing."""
    trades = client_with_flex.get("/api/trades").json()["items"]
    synthetic = next(t for t in trades if t["has_synthetic_leg"])

    response = client_with_flex.post(f"/api/trades/{synthetic['id']}/tags", json={"name": "x"})
    assert response.status_code == 400
    assert "synthetic" in response.json()["detail"].lower()


def test_tag_names_are_matched_case_insensitively(client_with_flex):
    trade = _a_trade(client_with_flex)
    client_with_flex.post(f"/api/trades/{trade['id']}/tags", json={"name": "Breakout"})
    client_with_flex.post(f"/api/trades/{trade['id']}/tags", json={"name": "breakout"})

    names = [t["name"] for t in client_with_flex.get("/api/tags").json()["items"]]
    assert names.count("Breakout") == 1
    assert "breakout" not in names


def test_removing_a_tag(client_with_flex):
    trade = _a_trade(client_with_flex)
    body = client_with_flex.post(f"/api/trades/{trade['id']}/tags", json={"name": "temp"}).json()
    tag_id = body["tag"]["id"]

    client_with_flex.delete(f"/api/trades/{trade['id']}/tags/{tag_id}")
    tags = {t["name"]: t for t in client_with_flex.get("/api/tags").json()["items"]}
    assert tags["temp"]["usage_count"] == 0


def test_day_tags_need_no_anchor(client_with_flex):
    """A trading day is not derived from anything, so nothing can detach."""
    client_with_flex.post("/api/days/2031-07-16/tags", json={"name": "choppy", "group_name": "Market Regime"})
    _reimport(client_with_flex)

    items = client_with_flex.get("/api/days/2031-07-16/tags").json()["items"]
    assert [t["name"] for t in items] == ["choppy"]


# --- Notes (§11.2) ----------------------------------------------------------


def test_notes_are_searchable(client_with_flex):
    client_with_flex.post(
        "/api/notes", json={"scope": "GENERAL", "body_md": "Switched MES to naked shorts."}
    )
    client_with_flex.post("/api/notes", json={"scope": "GENERAL", "body_md": "Unrelated."})

    hits = client_with_flex.get("/api/notes", params={"q": "naked"}).json()
    assert hits["total"] == 1
    assert "naked" in hits["items"][0]["body_md"]


def test_a_trade_note_survives_a_reimport(client_with_flex):
    trade = _a_trade(client_with_flex)
    client_with_flex.post(
        "/api/notes",
        json={"scope": "TRADE", "scope_ref": str(trade["id"]), "body_md": "Held through earnings."},
    )

    _reimport(client_with_flex)

    notes = client_with_flex.get("/api/notes", params={"scope": "TRADE"}).json()["items"]
    kept = next(n for n in notes if "earnings" in n["body_md"])
    assert kept["scope_ref"] is not None, "reattached to the rebuilt trade"


def test_editing_an_auto_draft_makes_it_yours(client_with_flex):
    """§5.4 auto-drafts a note on an assignment. Once edited it is no longer
    a draft, so an untouched auto-note is never mistaken for something the
    user actually wrote."""
    created = client_with_flex.post(
        "/api/notes", json={"scope": "GENERAL", "body_md": "original"}
    ).json()
    assert created["draft"] is False

    updated = client_with_flex.put(
        f"/api/notes/{created['id']}", json={"body_md": "revised"}
    ).json()
    assert updated["body_md"] == "revised"
    assert updated["draft"] is False


def test_a_note_on_a_synthetic_trade_is_refused(client_with_flex):
    trades = client_with_flex.get("/api/trades").json()["items"]
    synthetic = next(t for t in trades if t["has_synthetic_leg"])
    response = client_with_flex.post(
        "/api/notes", json={"scope": "TRADE", "scope_ref": str(synthetic["id"]), "body_md": "x"}
    )
    assert response.status_code == 400


# --- Bulk operations (§10.3) ------------------------------------------------


def test_bulk_tagging(client_with_flex):
    trades = [
        t for t in client_with_flex.get("/api/trades").json()["items"]
        if not t["has_synthetic_leg"] and t["closed_at"]
    ][:3]

    body = client_with_flex.post(
        "/api/trades/bulk",
        json={"trade_ids": [t["id"] for t in trades], "action": "add_tag", "name": "reviewed"},
    ).json()

    assert body["applied"] == 3
    assert body["skipped"] == []
    tag = next(t for t in client_with_flex.get("/api/tags").json()["items"] if t["name"] == "reviewed")
    assert tag["usage_count"] == 3


def test_bulk_skips_unanchorable_trades_by_name(client_with_flex):
    """A bulk edit that half-applies silently is worse than one that says
    which rows it could not touch."""
    trades = client_with_flex.get("/api/trades").json()["items"]
    synthetic = next(t for t in trades if t["has_synthetic_leg"])
    normal = next(t for t in trades if not t["has_synthetic_leg"] and t["closed_at"])

    body = client_with_flex.post(
        "/api/trades/bulk",
        json={"trade_ids": [normal["id"], synthetic["id"]], "action": "add_tag", "name": "bulk"},
    ).json()

    assert body["applied"] == 1
    assert [s["trade_id"] for s in body["skipped"]] == [synthetic["id"]]
    assert "synthetic" in body["note"].lower()


def test_bulk_rejects_an_unknown_action(client_with_flex):
    trade = _a_trade(client_with_flex)
    response = client_with_flex.post(
        "/api/trades/bulk", json={"trade_ids": [trade["id"]], "action": "explode"}
    )
    assert response.status_code == 400
    assert "add_tag" in response.json()["detail"]


def test_bulk_needs_a_selection(client_with_flex):
    assert client_with_flex.post("/api/trades/bulk", json={"action": "add_tag"}).status_code == 400


# --- Stop / target and R-value (§11.3) --------------------------------------


def test_setting_a_stop_unlocks_r_value(client_with_flex):
    """§11.3 — R-value is the one metric that needs a manual input, which is
    why it returned null through all of Phase 3."""
    before = client_with_flex.get(
        "/api/charts", params={"dimension": "symbol", "metric": "r_value", "mode": "LEG"}
    ).json()
    assert all(b["value"] is None for b in before["buckets"])

    trade = _a_trade(client_with_flex)
    entry = trade["avg_open_price"]
    # A stop 10% away from entry.
    stop = entry * 0.9 if trade["side"] == "LONG" else entry * 1.1
    client_with_flex.put(f"/api/trades/{trade['id']}/levels", json={"stop_loss": stop})

    after = client_with_flex.get(
        "/api/charts", params={"dimension": "symbol", "metric": "r_value", "mode": "LEG"}
    ).json()
    assert any(b["value"] is not None for b in after["buckets"])


def test_r_value_matches_the_hand_calculation(client_with_flex):
    trade = _a_trade(client_with_flex)
    entry = trade["avg_open_price"]
    stop = entry * 0.9
    client_with_flex.put(f"/api/trades/{trade['id']}/levels", json={"stop_loss": stop})

    from app.charts.units import load_units
    from app.db import SessionLocal
    from app.models.enums import AnalysisMode

    db = SessionLocal()
    try:
        unit = next(
            u for u in load_units(db, "U0000000", AnalysisMode.LEG) if u.unit_id == trade["id"]
        )
        expected = unit.net_pnl / (abs(entry - stop) * abs(unit.quantity) * unit.multiplier)
        assert unit.r_value == pytest.approx(expected)
    finally:
        db.close()


def test_a_stop_at_the_entry_price_yields_no_r(client_with_flex):
    """That is a missing stop, not a zero-risk trade, and dividing by it
    would print an infinite R."""
    trade = _a_trade(client_with_flex)
    client_with_flex.put(
        f"/api/trades/{trade['id']}/levels", json={"stop_loss": trade["avg_open_price"]}
    )

    from app.charts.units import load_units
    from app.db import SessionLocal
    from app.models.enums import AnalysisMode

    db = SessionLocal()
    try:
        unit = next(
            u for u in load_units(db, "U0000000", AnalysisMode.LEG) if u.unit_id == trade["id"]
        )
        assert unit.r_value is None
    finally:
        db.close()


def test_stops_survive_a_reimport(client_with_flex):
    trade = _a_trade(client_with_flex)
    client_with_flex.put(
        f"/api/trades/{trade['id']}/levels", json={"stop_loss": 1.23, "profit_target": 4.56}
    )
    _reimport(client_with_flex)

    after = client_with_flex.get(
        "/api/charts", params={"dimension": "symbol", "metric": "r_value", "mode": "LEG"}
    ).json()
    assert any(b["value"] is not None for b in after["buckets"])


# --- Manual trade entry (§11.3) ---------------------------------------------


def _an_instrument_id(client) -> int:
    positions = client.get("/api/positions").json()["items"]
    return positions[0]["instrument_id"]


def test_a_manual_fill_is_marked_and_flows_into_trades(client_with_flex):
    instrument_id = _an_instrument_id(client_with_flex)
    before = client_with_flex.get("/api/executions").json()["total"]

    client_with_flex.post(
        "/api/executions/manual",
        json={
            "instrument_id": instrument_id,
            "action": "SELLTOCLOSE",
            "quantity": -12,
            "price": 300.0,
            "executed_at": "2031-08-07T15:00:00",
            "reference": "pre-flex-1",
        },
    )

    executions = client_with_flex.get("/api/executions").json()
    assert executions["total"] == before + 1
    manual = next(e for e in executions["items"] if e["source"] == "MANUAL")
    assert manual["broker_trade_id"].startswith("manual:")


def test_a_manual_fill_survives_a_resync(client_with_flex):
    """§11.3 — the point of `source = MANUAL`."""
    instrument_id = _an_instrument_id(client_with_flex)
    client_with_flex.post(
        "/api/executions/manual",
        json={
            "instrument_id": instrument_id,
            "action": "SELLTOCLOSE",
            "quantity": -12,
            "price": 300.0,
            "executed_at": "2031-08-07T15:00:00",
            "reference": "survivor",
        },
    )
    _reimport(client_with_flex)

    manual = [e for e in client_with_flex.get("/api/executions").json()["items"] if e["source"] == "MANUAL"]
    assert len(manual) == 1


def test_manual_entry_refuses_an_invented_instrument(client_with_flex):
    """Typing a contract would recreate the §2.4 symbol-parsing problem
    §3.9 eliminated, and a typo would split a position across two."""
    response = client_with_flex.post(
        "/api/executions/manual",
        json={"action": "BUYTOOPEN", "quantity": 1, "price": 1.0},
    )
    assert response.status_code == 400
    assert "instrument" in response.json()["detail"].lower()


def test_manual_entry_validates_its_inputs(client_with_flex):
    instrument_id = _an_instrument_id(client_with_flex)
    bad_action = client_with_flex.post(
        "/api/executions/manual",
        json={"instrument_id": instrument_id, "action": "NOPE", "quantity": 1, "price": 1.0},
    )
    assert bad_action.status_code == 400
    assert "BUYTOOPEN" in bad_action.json()["detail"]

    zero_qty = client_with_flex.post(
        "/api/executions/manual",
        json={"instrument_id": instrument_id, "action": "BUYTOOPEN", "quantity": 0, "price": 1.0},
    )
    assert zero_qty.status_code == 400


def test_a_broker_execution_cannot_be_deleted(client_with_flex):
    """§3.2 — executions are immutable once imported; the audit trail is the
    point."""
    execution = client_with_flex.get("/api/executions").json()["items"][0]
    response = client_with_flex.delete(f"/api/executions/manual/{execution['id']}")
    assert response.status_code == 409


# --- Corporate actions (§11.3) ----------------------------------------------


def test_a_split_adjusts_historical_quantities_not_the_raw_rows(client_with_flex):
    """§11.3 / §3.2 — the raw broker figures stay exactly as sent; the split
    is a derivation on top, so it is reversible and auditable."""
    instrument_id = _an_instrument_id(client_with_flex)
    symbol = next(
        p["symbol"] for p in client_with_flex.get("/api/positions").json()["items"]
        if p["instrument_id"] == instrument_id
    )
    before = [
        e for e in client_with_flex.get("/api/executions").json()["items"] if e["symbol"] == symbol
    ]
    assert before, "need at least one execution in the split symbol"
    raw_quantity = before[0]["quantity"]
    raw_price = before[0]["price"]

    client_with_flex.post(
        "/api/corporate-actions",
        json={
            "instrument_id": instrument_id,
            "split_ratio": 2,
            "occurred_on": "2031-08-08",
            "description": "2 for 1",
        },
    )

    after = next(
        e for e in client_with_flex.get("/api/executions").json()["items"]
        if e["id"] == before[0]["id"]
    )
    # Raw values untouched...
    assert after["quantity"] == pytest.approx(raw_quantity)
    assert after["price"] == pytest.approx(raw_price)
    # ...with the adjustment carried alongside.
    assert after["split_factor"] == pytest.approx(2.0)


def test_a_split_leaves_proceeds_unchanged(client_with_flex):
    """Quantity × price is invariant under a split, which is what makes the
    factor safe to apply to the two of them and nothing else."""
    from app.charts.units import load_units
    from app.db import SessionLocal
    from app.models.enums import AnalysisMode

    db = SessionLocal()
    try:
        before = {u.unit_id: u.net_pnl for u in load_units(db, "U0000000", AnalysisMode.LEG)}
    finally:
        db.close()

    instrument_id = _an_instrument_id(client_with_flex)
    client_with_flex.post(
        "/api/corporate-actions",
        json={"instrument_id": instrument_id, "split_ratio": 2, "occurred_on": "2031-08-08"},
    )

    db = SessionLocal()
    try:
        after = {u.unit_id: u.net_pnl for u in load_units(db, "U0000000", AnalysisMode.LEG)}
    finally:
        db.close()

    assert sum(before.values()) == pytest.approx(sum(after.values()), abs=0.01)


def test_deleting_a_manual_split_reverses_it(client_with_flex):
    instrument_id = _an_instrument_id(client_with_flex)
    created = client_with_flex.post(
        "/api/corporate-actions",
        json={"instrument_id": instrument_id, "split_ratio": 4, "occurred_on": "2031-08-08"},
    ).json()

    client_with_flex.delete(f"/api/corporate-actions/{created['id']}")

    factors = {e["split_factor"] for e in client_with_flex.get("/api/executions").json()["items"]}
    assert factors == {1.0}


def test_a_split_ratio_must_be_positive(client_with_flex):
    instrument_id = _an_instrument_id(client_with_flex)
    response = client_with_flex.post(
        "/api/corporate-actions",
        json={"instrument_id": instrument_id, "split_ratio": 0, "occurred_on": "2031-08-08"},
    )
    assert response.status_code == 400
    assert "positive" in response.json()["detail"]


def test_split_ratio_parsing():
    from app.importer.corporate_actions import parse_split_ratio

    assert parse_split_ratio("NVDA(US67066G1040) SPLIT 10 FOR 1 (NVDA)") == pytest.approx(10.0)
    assert parse_split_ratio("SPLIT 1 FOR 8") == pytest.approx(0.125)
    # Unparseable is None, never a guess: a wrong ratio corrupts silently.
    assert parse_split_ratio("SPIN-OFF OF SOMETHING") is None
    assert parse_split_ratio("") is None


# --- The account-return ratios (§8.2, both bases) ---------------------------


def test_ratios_are_reported_on_both_bases(client_with_flex):
    """The trade-P&L versions stay where they were; the account-return
    versions appear beside them, each naming its basis. Neither is a
    correction of the other."""
    client_with_flex.put(
        "/api/settings/epoch",
        json={"tracking_start_date": "2031-05-12", "tracking_start_value": 389.15029091478},
    )

    account = client_with_flex.get("/api/performance").json()["ratios"]
    assert account["basis"] == "account_returns"
    assert account["sharpe"] is not None
    assert account["max_drawdown_pct"] is not None

    trades = client_with_flex.get("/api/stats").json()["ratios"]
    assert "sharpe" in trades
    assert trades["proxy_metrics"], "the trade-P&L versions still declare themselves proxies"
    assert account["sharpe"] != pytest.approx(trades["sharpe"])


def test_account_ratios_are_gated_like_everything_else(client_with_flex):
    """§8.2's ≥30-day rule, applied to the new basis too."""
    client_with_flex.put(
        "/api/settings/epoch",
        json={"tracking_start_date": "2031-08-02", "tracking_start_value": 9000.0},
    )
    ratios = client_with_flex.get("/api/performance").json()["ratios"]
    assert ratios["insufficient_sample"] is True
    assert ratios["sharpe"] is None


def test_account_ratios_are_withheld_without_an_epoch(client_with_flex):
    ratios = client_with_flex.get("/api/performance").json()["ratios"]
    assert ratios["insufficient_sample"] is True
    assert ratios["sharpe"] is None
