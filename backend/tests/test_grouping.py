"""§16.4 — strategy classification and group tests, plus §16's group P&L
assertions (tests 12-14) which only become checkable once grouping exists."""

import datetime as dt

import pytest

from app.importer.pipeline import import_file, import_tlg_file
from app.models import Trade, TradeGroup
from app.models.enums import AssetClass, Right, StrategyType
from app.stats.groups import compute_segmented_risk_stats
from app.trades.grouping import (
    DETECTION_BROKERAGE_ORDER_ID,
    DETECTION_SINGLE,
    DETECTION_TIMESTAMP,
)
from tests.conftest import ACCOUNT_ID, FIXTURES

TOL = 0.01


@pytest.fixture
def flex(db):
    return import_file(db, "fixture-flex.xml", (FIXTURES / "fixture-flex.xml").read_bytes())


@pytest.fixture
def tlg(db):
    return import_tlg_file(db, "fixture-complete.tlg", (FIXTURES / "fixture-complete.tlg").read_bytes())


def _group_for(db, *symbol_fragments) -> TradeGroup:
    for group in db.query(TradeGroup).all():
        legs = [t.instrument.broker_symbol.strip() for t in group.legs]
        if len(legs) != len(symbol_fragments):
            continue
        if all(any(f in leg for leg in legs) for f in symbol_fragments):
            return group
    raise AssertionError(f"No group matching {symbol_fragments}")


def _counts(db) -> dict:
    counts: dict = {}
    for group in db.query(TradeGroup).all():
        counts[group.strategy_type] = counts.get(group.strategy_type, 0) + 1
    return counts


# ------------------------------------------------------- tests 12, 13, 14
def test_12_put_credit_vertical_group_pnl(db, flex):
    """ACME 12AUG26 400/420 P: one Put Credit Vertical, net credit 1.39,
    group net P&L +42.37. The legs alone read -61.37 and +103.75, which is
    exactly why §6 exists."""
    group = _group_for(db, "310813P00172000", "310813P00180600")
    assert group.strategy_type == StrategyType.VERTICAL_PUT
    assert float(group.net_debit_credit) == pytest.approx(59.77, abs=TOL)
    assert float(group.net_pnl) == pytest.approx(42.372648, abs=TOL)
    assert group.opened_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S") == "2031-08-05 14:06:11"
    assert group.leg_count == 2

    legs = {t.instrument.broker_symbol.strip(): float(t.net_pnl) for t in group.legs}
    assert legs["ACME  310813P00172000"] == pytest.approx(-61.372436, abs=TOL)
    assert legs["ACME  310813P00180600"] == pytest.approx(103.745084, abs=TOL)


def test_13_expiring_vertical_group_pnl(db, flex):
    """ACME 24JUL26 425/440 P closes at expiry with zero price and zero
    commission on both legs.

    The exact figure is 20.904516. The spec's "+20.90" is that truncated,
    not a different number — asserted against the exact value here, within
    the spec's own $0.01 tolerance.
    """
    group = _group_for(db, "310725P00182750", "310725P00189200")
    assert group.strategy_type == StrategyType.VERTICAL_PUT
    assert float(group.net_pnl) == pytest.approx(20.904516, abs=1e-6)
    assert float(group.net_pnl) == pytest.approx(20.90, abs=TOL)


def test_14_mes_futures_option_vertical_group_pnl(db, flex):
    group = _group_for(db, "EX3N1 P3014.3", "EX3N1 P3057.3")
    assert group.strategy_type == StrategyType.VERTICAL_PUT
    assert float(group.net_pnl) == pytest.approx(1.9049, abs=TOL)
    assert group.legs[0].instrument.asset_class == AssetClass.FOP


# --------------------------------------------------------------- test 56
def test_56_no_fabricated_spreads(db, flex):
    """The two MEMC positions opened 2031-07-14 at 10:21:44 and 12:40:43
    classify as two separate NAKED_SHORT_PUTs, never as one spread.
    Relaxing §6.1's exact-timestamp match to a date match fails this."""
    dram_groups = [
        g
        for g in db.query(TradeGroup).all()
        if any("MEMC" in t.instrument.broker_symbol for t in g.legs)
        and g.opened_at.date() == dt.date(2031, 7, 14)
    ]
    assert len(dram_groups) == 2
    for group in dram_groups:
        assert group.strategy_type == StrategyType.NAKED_SHORT_PUT
        assert group.leg_count == 1


def test_56b_same_day_different_time_never_groups(db, flex):
    """The guard restated as an invariant over the whole fixture: no group
    was formed from legs with differing open timestamps unless the broker's
    own combo id said so."""
    for group in db.query(TradeGroup).all():
        if group.leg_count < 2 or group.detection_source != DETECTION_TIMESTAMP:
            continue
        opens = {t.opened_at for t in group.legs}
        assert len(opens) == 1, f"timestamp-grouped {group.id} spans {opens}"


# ----------------------------------------------------------- tests 57, 58
def test_57_futures_option_classification_split(db, flex):
    """The 8 MES opening events yield 3 verticals and 5 naked short puts."""
    mes = [
        g
        for g in db.query(TradeGroup).all()
        if g.legs and g.legs[0].instrument.asset_class == AssetClass.FOP
    ]
    verticals = [g for g in mes if g.strategy_type == StrategyType.VERTICAL_PUT]
    nakeds = [g for g in mes if g.strategy_type == StrategyType.NAKED_SHORT_PUT]

    assert len(verticals) == 3
    assert len(nakeds) == 5
    assert {g.opened_at.date() for g in verticals} == {
        dt.date(2031, 6, 27),
        dt.date(2031, 6, 30),
        dt.date(2031, 7, 6),
    }


def test_58_equity_classification(db, flex):
    """12 two-leg verticals plus 2 naked short puts; SEMX and OMNI classify
    as call verticals, not puts."""
    equity = [
        g
        for g in db.query(TradeGroup).all()
        if g.legs and g.legs[0].instrument.asset_class == AssetClass.OPT
    ]
    verticals = [
        g for g in equity if g.strategy_type in (StrategyType.VERTICAL_PUT, StrategyType.VERTICAL_CALL)
    ]
    nakeds = [g for g in equity if g.strategy_type == StrategyType.NAKED_SHORT_PUT]

    assert len(verticals) == 12
    assert len(nakeds) == 2

    calls = [g for g in verticals if g.strategy_type == StrategyType.VERTICAL_CALL]
    assert len(calls) == 2
    assert {g.underlying_root for g in calls} == {"SEMX", "OMNI"}
    for group in calls:
        for trade in group.legs:
            assert trade.instrument.right == Right.CALL


def test_58b_overall_group_shape(db, flex):
    counts = _counts(db)
    assert counts[StrategyType.VERTICAL_PUT] == 13
    assert counts[StrategyType.VERTICAL_CALL] == 2
    assert counts[StrategyType.NAKED_SHORT_PUT] == 7
    assert counts[StrategyType.LONG_STOCK] == 2
    assert sum(counts.values()) == 24


# ----------------------------------------------------------- tests 59, 60
def test_59_naked_put_uses_the_cash_secured_risk_formula(db, flex):
    """MES 08AUG31 3061.6 P: max_risk = (3061.6 x 5) - credit, not the spread
    formula. Getting this wrong understates exposure by ~18x."""
    group = _group_for(db, "EX1Q1 P3061.6")
    assert group.strategy_type == StrategyType.NAKED_SHORT_PUT
    credit = float(group.net_debit_credit)
    assert float(group.max_risk) == pytest.approx(3061.6 * 5 - credit, abs=TOL)
    assert float(group.max_risk) == pytest.approx(15281.985, abs=TOL)


def test_59b_vertical_uses_the_width_formula(db, flex):
    group = _group_for(db, "310813P00172000", "310813P00180600")
    # (180.6 - 172) x 100 - 59.77 credit
    assert float(group.max_risk) == pytest.approx(860 - 59.77, abs=TOL)


def test_60_unbounded_risk_propagates_as_null(db, flex):
    """A naked short call has genuinely unbounded risk. It must yield
    max_risk = None — never a finite placeholder — and must be neither
    counted as zero-risk nor silently dropped from a segment."""
    group = _group_for(db, "EX1Q1 P3061.6")
    group.strategy_type = StrategyType.NAKED_SHORT_CALL
    group.max_risk = None
    group.return_on_risk = None
    db.flush()

    stats = compute_segmented_risk_stats(db, ACCOUNT_ID)
    segment = next(s for s in stats.segments if s.strategy_type == "NAKED_SHORT_CALL")
    assert segment.group_count == 1
    assert segment.unbounded_risk_count == 1
    assert segment.total_max_risk is None
    assert segment.return_on_risk is None
    # The position still contributes its P&L; only the ratio is withheld.
    assert segment.net_pnl != 0


# --------------------------------------------------------------- test 61
def test_61_return_on_risk_is_never_pooled(db, flex):
    """Requesting an aggregate across mixed strategy types returns a
    segmented result and refuses to emit a blended number."""
    stats = compute_segmented_risk_stats(db, ACCOUNT_ID)
    payload = stats.as_dict()

    assert payload["pooled_return_on_risk"] is None
    assert "not pooled" in payload["refusal_reason"]
    assert len(payload["segments"]) > 1

    types = {s["strategy_type"] for s in payload["segments"]}
    assert {"VERTICAL_PUT", "NAKED_SHORT_PUT"} <= types

    # The segments must genuinely differ in scale — that is the reason for
    # the refusal, not a stylistic preference.
    naked = next(s for s in payload["segments"] if s["strategy_type"] == "NAKED_SHORT_PUT")
    vertical = next(s for s in payload["segments"] if s["strategy_type"] == "VERTICAL_PUT")
    assert naked["total_max_risk"] > vertical["total_max_risk"] * 5


# ------------------------------------------------------------ §6.1 source
def test_detection_source_records_how_a_group_was_formed(db, flex):
    """A timestamp-inferred combo must never be indistinguishable from one
    the broker confirmed."""
    sources: dict[str, int] = {}
    for group in db.query(TradeGroup).all():
        sources[group.detection_source] = sources.get(group.detection_source, 0) + 1

    assert sources[DETECTION_BROKERAGE_ORDER_ID] == 13
    # §6.1: the VERT 82.5/92.5 and MES 7170/7270 opening pairs are genuine
    # combos with no brokerageOrderID recorded.
    assert sources[DETECTION_TIMESTAMP] == 2
    assert sources[DETECTION_SINGLE] == 9


def test_timestamp_fallback_finds_the_two_pairs_flex_did_not_key(db, flex):
    fallback = [g for g in db.query(TradeGroup).all() if g.detection_source == DETECTION_TIMESTAMP]
    assert {g.underlying_root for g in fallback} == {"VERT", "MES"}


def test_tlg_data_groups_entirely_by_timestamp(db, tlg):
    """`.tlg` files carry no order identifier of either kind, so every combo
    comes from the fallback — and must still produce the same verticals."""
    groups = db.query(TradeGroup).all()
    assert all(g.detection_source != DETECTION_BROKERAGE_ORDER_ID for g in groups)

    group = _group_for(db, "310813P00172000", "310813P00180600")
    assert group.strategy_type == StrategyType.VERTICAL_PUT
    assert float(group.net_pnl) == pytest.approx(42.372648, abs=TOL)
    assert group.detection_source == DETECTION_TIMESTAMP


# --------------------------------------------------------------- test 62-64
def test_62_books_partition_groups(db, flex):
    for group in db.query(TradeGroup).all():
        asset_class = group.legs[0].instrument.asset_class
        if asset_class in (AssetClass.OPT, AssetClass.FOP):
            assert group.book == "Options Income"
        else:
            assert group.book == "Holdings"


def test_63_holdings_excluded_from_options_segments(db, flex):
    """BCGX's stock result must not appear in any options segment."""
    stats = compute_segmented_risk_stats(db, ACCOUNT_ID, book="Options Income")
    assert all(s["strategy_type"] != "LONG_STOCK" for s in stats.as_dict()["segments"])


def test_groups_rebuild_idempotently(db, flex):
    before = {(g.strategy_type, round(float(g.net_pnl), 6)) for g in db.query(TradeGroup).all()}
    import_file(db, "fixture-flex.xml", (FIXTURES / "fixture-flex.xml").read_bytes())
    after = {(g.strategy_type, round(float(g.net_pnl), 6)) for g in db.query(TradeGroup).all()}
    assert before == after
    assert db.query(TradeGroup).count() == 24


def test_every_trade_belongs_to_exactly_one_group(db, flex):
    """No trade may be orphaned by grouping — a leg that belongs to no group
    silently disappears from every strategy-level statistic."""
    trades = db.query(Trade).filter(Trade.account_id == ACCOUNT_ID).all()
    assert trades
    assert all(t.trade_group_id is not None for t in trades)

    total_from_groups = sum(float(g.net_pnl) for g in db.query(TradeGroup).all())
    total_from_trades = sum(float(t.net_pnl) for t in trades)
    assert total_from_groups == pytest.approx(total_from_trades, abs=1e-6)
