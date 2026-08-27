"""§6.4 analysis mode and manual overrides, and §3.2 payload storage."""

import datetime as dt

import pytest

from app.importer import storage
from app.importer.pipeline import import_file
from app.models import GroupOverride, ImportFile, Trade, TradeGroup
from app.models.enums import AnalysisMode, LegRole, StrategyType
from app.stats.engine import compute_pnl_summary, compute_ratios
from app.stats.source import trade_stats_for_mode
from app.trades.grouping import DETECTION_MANUAL, rebuild_trade_groups
from tests.conftest import ACCOUNT_ID, FIXTURES

TOL = 0.01


@pytest.fixture
def flex(db, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path), raising=False)
    report = import_file(db, "fixture-flex.xml", (FIXTURES / "fixture-flex.xml").read_bytes())
    # Trades and groups are deleted and recreated during the import, so any
    # instance the session cached beforehand refers to a row that is gone.
    db.expire_all()
    return report


def _trade_ids(db, *symbol_fragments) -> list[int]:
    ids = []
    for fragment in symbol_fragments:
        trade = next(
            t
            for t in db.query(Trade).filter(Trade.account_id == ACCOUNT_ID).all()
            if fragment in t.instrument.broker_symbol
        )
        ids.append(trade.id)
    return ids


# ------------------------------------------------------- §6.4 analysis mode
def test_totals_are_identical_in_both_modes(db, flex):
    """The sum of legs equals the sum of groups. If this ever diverges, one
    of the two views is losing or double-counting a position."""
    legs = trade_stats_for_mode(db, ACCOUNT_ID, AnalysisMode.LEG)
    strategies = trade_stats_for_mode(db, ACCOUNT_ID, AnalysisMode.STRATEGY)

    assert sum(t.net_pnl for t in legs) == pytest.approx(
        sum(t.net_pnl for t in strategies), abs=1e-6
    )
    assert sum(t.commissions for t in legs) == pytest.approx(
        sum(t.commissions for t in strategies), abs=1e-6
    )


def test_per_unit_figures_differ_between_modes(db, flex):
    """And they must — a vertical is one outcome as a strategy and one win
    plus one loss as legs. Equal win rates would mean the toggle does
    nothing."""
    legs = trade_stats_for_mode(db, ACCOUNT_ID, AnalysisMode.LEG)
    strategies = trade_stats_for_mode(db, ACCOUNT_ID, AnalysisMode.STRATEGY)

    assert len(strategies) < len(legs)

    leg_ratios = compute_ratios(legs, [])
    strategy_ratios = compute_ratios(strategies, [])
    assert leg_ratios["win_rate"] != strategy_ratios["win_rate"]


def test_a_winning_vertical_is_one_win_not_a_win_and_a_loss(db, flex):
    """The concrete case §6 opens with: ACME 12AUG26 400/420 P reads
    -61.37 and +103.75 per leg, and made +42.37 as a position."""
    group = next(
        g
        for g in db.query(TradeGroup).all()
        if {"ACME  310813P00172000", "ACME  310813P00180600"}
        == {t.instrument.broker_symbol for t in g.legs}
    )
    leg_pnls = sorted(round(float(t.net_pnl), 2) for t in group.legs)
    assert leg_pnls == [-61.37, 103.75]
    assert float(group.net_pnl) == pytest.approx(42.372648, abs=TOL)

    strategies = trade_stats_for_mode(db, ACCOUNT_ID, AnalysisMode.STRATEGY)
    matching = [s for s in strategies if abs(s.net_pnl - 42.372648) < TOL]
    assert len(matching) == 1, "the spread contributes exactly one outcome"


def test_strategy_mode_inherits_leg_exclusions(db, tmp_path, monkeypatch):
    """An orphan close excluded from win rate as a leg must stay excluded as
    a strategy, or it reappears through the back door.

    Uses the orphan `.tlg` rather than the Flex fixture: with the Transfers
    section present, BCGX is a properly based trade and nothing is excluded
    from win rate at all.
    """
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path), raising=False)
    import_file(db, "fixture-orphan.tlg", (FIXTURES / "fixture-orphan.tlg").read_bytes())
    db.expire_all()

    excluded = db.query(Trade).filter(Trade.excluded_from_win_rate.is_(True)).all()
    assert excluded, "the orphan fixture must contain an excluded trade"
    excluded_pnls = {round(float(t.net_pnl), 4) for t in excluded}

    strategies = trade_stats_for_mode(db, ACCOUNT_ID, AnalysisMode.STRATEGY)
    matched = [s for s in strategies if round(s.net_pnl, 4) in excluded_pnls]
    assert matched, "the excluded leg should still appear as a strategy"
    for stat in matched:
        assert stat.excluded_from_win_rate


def test_stats_endpoint_reports_and_honours_the_mode(client_with_flex):
    client = client_with_flex
    strategy = client.get("/api/stats?mode=STRATEGY").json()
    leg = client.get("/api/stats?mode=LEG").json()

    assert strategy["analysis_mode"] == "STRATEGY"
    assert leg["analysis_mode"] == "LEG"
    assert strategy["unit_count"] < leg["unit_count"]
    # Totals invariant, per-unit figures not.
    assert strategy["pnl"]["total_net_pnl"] == pytest.approx(leg["pnl"]["total_net_pnl"], abs=TOL)
    assert strategy["ratios"]["win_rate"] != leg["ratios"]["win_rate"]
    assert "one outcome" in strategy["analysis_mode_note"]


def test_default_mode_is_strategy(client_with_flex):
    assert client_with_flex.get("/api/settings").json()["analysis_mode"] == "STRATEGY"
    assert client_with_flex.get("/api/stats").json()["analysis_mode"] == "STRATEGY"


def test_mode_setting_persists_and_drives_the_default(client_with_flex):
    client = client_with_flex
    assert client.put("/api/settings/analysis-mode", params={"mode": "LEG"}).status_code == 200
    assert client.get("/api/stats").json()["analysis_mode"] == "LEG"
    assert client.put("/api/settings/analysis-mode", params={"mode": "nope"}).status_code == 400


# --------------------------------------------------- §6.4 manual overrides
def test_manual_grouping_survives_a_rebuild(db, flex):
    """The whole point. Trade ids do not survive a rebuild, so an override
    keyed on them would silently vanish on the next import."""
    ids = _trade_ids(db, "MEMC  310718P00021070", "MEMC  320617P00012470")
    identifiers = []
    for trade_id in ids:
        trade = db.get(Trade, trade_id)
        for leg in trade.legs:
            if leg.leg_role == LegRole.OPEN and leg.execution_id is not None:
                execution = leg.execution
                identifiers.append(execution.transaction_id or execution.broker_trade_id)

    db.add(
        GroupOverride(
            account_id=ACCOUNT_ID,
            member_key=GroupOverride.build_member_key(identifiers),
            strategy_type=StrategyType.CUSTOM,
            created_at=dt.datetime.now(dt.timezone.utc),
        )
    )
    db.flush()

    rebuild_trade_groups(db, ACCOUNT_ID)
    db.flush()
    manual = [g for g in db.query(TradeGroup).all() if g.detection_source == DETECTION_MANUAL]
    assert len(manual) == 1
    assert manual[0].leg_count == 2
    assert manual[0].user_confirmed is True
    assert manual[0].strategy_type == StrategyType.CUSTOM
    # Captured before the rebuild, which deletes the row this object refers to.
    first_group_id = manual[0].id
    first_trade_ids = set(ids)

    # Re-import: every trade id is regenerated, and the override must still bind.
    import_file(db, "fixture-flex.xml", (FIXTURES / "fixture-flex.xml").read_bytes())
    db.expire_all()

    manual_again = [g for g in db.query(TradeGroup).all() if g.detection_source == DETECTION_MANUAL]
    assert len(manual_again) == 1, "override lost across a rebuild"
    assert manual_again[0].leg_count == 2
    assert manual_again[0].id != first_group_id, "groups are re-derived, so this is a new row"
    # And the legs are genuinely new trades, which is why trade ids could
    # never have anchored the override.
    assert not ({t.id for t in manual_again[0].legs} & first_trade_ids)


def test_override_endpoint_groups_two_trades(client_with_flex):
    client = client_with_flex
    trades = client.get("/api/trades").json()["items"]
    ids = [t["id"] for t in trades if "MEMC" in t["symbol"]][:2]

    resp = client.post(
        "/api/groups/override",
        json={"trade_ids": ids, "strategy_type": "CUSTOM", "note": "manual"},
    )
    assert resp.status_code == 200

    groups = client.get("/api/groups").json()["items"]
    manual = [g for g in groups if g["detection_source"] == DETECTION_MANUAL]
    assert len(manual) == 1
    assert manual[0]["leg_count"] == 2

    listed = client.get("/api/groups/overrides").json()["items"]
    assert len(listed) == 1
    assert listed[0]["note"] == "manual"


def test_deleting_an_override_restores_auto_detection(client_with_flex):
    client = client_with_flex
    before = client.get("/api/groups").json()["total"]

    trades = client.get("/api/trades").json()["items"]
    ids = [t["id"] for t in trades if "MEMC" in t["symbol"]][:2]
    override_id = client.post(
        "/api/groups/override", json={"trade_ids": ids, "strategy_type": "CUSTOM"}
    ).json()["id"]

    assert client.delete(f"/api/groups/override/{override_id}").status_code == 200
    assert client.get("/api/groups").json()["total"] == before
    assert client.get("/api/groups/overrides").json()["items"] == []


def test_ungroup_splits_an_auto_detected_spread(client_with_flex):
    client = client_with_flex
    vertical = next(
        g for g in client.get("/api/groups").json()["items"] if g["leg_count"] == 2
    )
    leg_ids = [leg["trade_id"] for leg in vertical["legs"]]

    resp = client.post("/api/groups/override", json={"trade_ids": leg_ids, "ungroup": True})
    assert resp.status_code == 200

    groups = client.get("/api/groups").json()["items"]
    split = [g for g in groups if g["detection_source"] == DETECTION_MANUAL]
    assert len(split) == 2
    assert all(g["leg_count"] == 1 for g in split)


def test_grouping_a_single_trade_is_rejected(client_with_flex):
    client = client_with_flex
    ids = [client.get("/api/trades").json()["items"][0]["id"]]
    assert client.post("/api/groups/override", json={"trade_ids": ids}).status_code == 400


# ------------------------------------------------------- §3.2 file storage
def test_payload_is_archived_by_hash(db, flex, tmp_path):
    record = db.query(ImportFile).one()
    assert record.stored_path is not None

    stored = storage.load_payload(record.sha256)
    assert stored == (FIXTURES / "fixture-flex.xml").read_bytes()
    assert record.sha256 in record.stored_path


def test_storage_is_content_addressed_so_a_reimport_writes_nothing_new(db, flex, tmp_path):
    before = sorted(p.name for p in (tmp_path / "imports").rglob("*") if p.is_file())
    import_file(db, "same-again.xml", (FIXTURES / "fixture-flex.xml").read_bytes())
    after = sorted(p.name for p in (tmp_path / "imports").rglob("*") if p.is_file())
    assert before == after


def test_a_corrupted_store_is_detected_rather_than_trusted(db, flex, tmp_path):
    record = db.query(ImportFile).one()
    storage.path_for(record.sha256).write_bytes(b"not the original payload")

    with pytest.raises(ValueError, match="corrupt"):
        storage.load_payload(record.sha256)


def test_an_unwritable_store_warns_but_still_imports(db, monkeypatch):
    """Losing the archive copy is bad; refusing data the user already has is
    worse."""
    monkeypatch.setattr(storage, "store_payload", lambda *a, **k: None)
    import app.importer.pipeline as pipeline

    monkeypatch.setattr(pipeline, "store_payload", lambda *a, **k: None)

    report = import_file(db, "fixture-flex.xml", (FIXTURES / "fixture-flex.xml").read_bytes())
    assert report.rows_inserted == 78
    assert any("archive" in w for w in report.warnings)
