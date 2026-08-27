"""§16 acceptance tests against `fixture-complete.tlg` / `fixture-orphan.tlg`.

Test numbers match the spec's tables. Tests covering Flex sync (§16.1),
assignment (§16.2), transfers (§16.3), and spread grouping (§16.4) are out
of scope for v1 (Phases 1b/4) and are not stubbed here — an empty passing
stub is worse than an absent test.
"""

import datetime as dt

import pytest
from sqlalchemy import func

from app.importer.pipeline import import_tlg_file
from app.importer.reconciliation import compute_open_positions
from app.models import Execution, Instrument, Trade
from app.models.enums import AssetClass, ExerciseStyle, TradeStatus
from app.parsers.tlg import parse_tlg
from tests.conftest import ACCOUNT_ID, FIXTURES

TOL = 0.01


def _text(name):
    return (FIXTURES / name).read_text()


def _instrument(db, broker_symbol):
    return db.query(Instrument).filter(Instrument.broker_symbol == broker_symbol).one()


def _trades_for(db, broker_symbol):
    inst = _instrument(db, broker_symbol)
    return db.query(Trade).filter(Trade.instrument_id == inst.id).all()


def _net(db, broker_symbol):
    trades = _trades_for(db, broker_symbol)
    return sum(float(t.net_pnl) for t in trades)


# ---------------------------------------------------------------- test 1
def test_01_sections_parsed():
    result = parse_tlg(_text("fixture-complete.tlg"))
    assert result.sections_seen == {
        "ACCOUNT_INFORMATION",
        "STOCK_TRANSACTIONS",
        "OPTION_TRANSACTIONS",
        "FUTURE_OPTION_TRANSACTIONS",
        "STOCK_POSITIONS",
        "OPTION_POSITIONS",
    }


# ---------------------------------------------------------------- test 2
def test_02_execution_counts():
    result = parse_tlg(_text("fixture-complete.tlg"))
    counts: dict[str, int] = {}
    for e in result.executions:
        counts[e.record_type] = counts.get(e.record_type, 0) + 1
    assert counts["STK_TRD"] == 8
    assert counts["OPT_TRD"] == 49
    assert counts["FOP_TRD"] == 22
    assert len(result.executions) == 79


# ---------------------------------------------------------------- test 3
def test_03_proceeds_identity():
    result = parse_tlg(_text("fixture-complete.tlg"))
    for e in result.executions:
        assert abs(e.quantity * e.multiplier * e.price - e.proceeds) <= TOL, e.broker_trade_id


# ---------------------------------------------------------------- test 4
def test_04_open_close_code_distribution():
    result = parse_tlg(_text("fixture-complete.tlg"))
    dist: dict[str, int] = {}
    for e in result.executions:
        dist[";".join(e.flags)] = dist.get(";".join(e.flags), 0) + 1
    assert dist["O"] == 39
    assert dist["C"] == 30
    assert dist["C;Ep"] == 10


# ---------------------------------------------------------------- test 5
def test_05_commission_signs():
    result = parse_tlg(_text("fixture-complete.tlg"))
    negative = sum(1 for e in result.executions if e.commission < 0)
    zero = sum(1 for e in result.executions if e.commission == 0)
    assert (negative, zero) == (69, 10)


# ---------------------------------------------------------------- test 6
def test_06_cash_flow_totals(db, complete_report):
    execs = db.query(Execution).all()
    assert abs(sum(-float(e.proceeds) for e in execs) - (-749.64738)) <= TOL
    assert abs(sum(float(e.commission) for e in execs) - (-22.10792712)) <= TOL


# ---------------------------------------------------------------- test 7
def test_07_reimport_is_idempotent(db, complete_report, complete_bytes):
    before = db.query(func.count(Execution.id)).scalar()
    second = import_tlg_file(db, "fixture-complete.tlg", complete_bytes)
    assert second.rows_inserted == 0
    assert second.rows_duplicate == 79
    assert db.query(func.count(Execution.id)).scalar() == before


# ---------------------------------------------------------------- test 8
def test_08_fbcg_round_trip(db, complete_report):
    trades = _trades_for(db, "BCGX")
    assert len(trades) == 1
    t = trades[0]
    assert t.status == TradeStatus.CLOSED
    assert abs(float(t.gross_pnl) - 258.00) <= TOL
    assert abs(float(t.commissions) - (-0.9804)) <= TOL
    assert abs(float(t.net_pnl) - 257.017616) <= TOL
    assert t.opened_at.date() == dt.date(2031, 4, 9)
    assert t.closed_at.date() == dt.date(2031, 6, 27)
    assert abs(float(t.avg_open_price) - 22.9921) <= TOL
    assert abs(float(t.avg_close_price) - 25.5721) <= TOL
    assert round(float(t.duration_seconds) / 86400) == 79


# --------------------------------------------------------------- test 8b
def test_08b_fbcg_orphan_close(db, orphan_report):
    trades = _trades_for(db, "BCGX")
    assert len(trades) == 1
    t = trades[0]
    assert t.status == TradeStatus.ORPHAN_CLOSE
    assert t.excluded_from_win_rate is True
    assert t.excluded_from_duration is True
    # No P&L is attributed without an opening basis — proceeds are explicitly
    # not treated as pure profit (§5.5).
    assert float(t.gross_pnl) == pytest.approx(0.0)
    assert float(t.net_pnl) == pytest.approx(0.0)

    # Cash flow is still recorded — the close is never silently dropped (§5.5).
    inst = _instrument(db, "BCGX")
    execs = db.query(Execution).filter(Execution.instrument_id == inst.id).all()
    assert len(execs) == 1
    assert abs(float(execs[0].cash_flow) - (2557.21 - 0.49119244)) <= TOL


def test_08b_orphan_close_does_not_fabricate_a_short_position(db, orphan_report):
    """A close with no opening must not invent a position in the opposite
    direction — the account never held short BCGX, and the broker's LOT
    records agree by omitting it."""
    positions = compute_open_positions(db, ACCOUNT_ID)
    bcgx = _instrument(db, "BCGX")
    assert positions[bcgx.id].quantity == pytest.approx(0.0)
    assert positions[bcgx.id].orphan_close_qty == pytest.approx(-100.0)


def test_08c_orphan_close_reconciles_and_is_surfaced(db, orphan_report):
    """§3.3/§3.4 — the orphan is reported as a warning, not swallowed and not
    escalated into a spurious quantity error."""
    assert orphan_report.reconciliation_passed is True
    orphan_issues = [i for i in orphan_report.reconciliation_issues if "Orphan close" in i]
    assert len(orphan_issues) == 1
    assert orphan_issues[0].startswith("[warning]")
    assert "§5.5" in orphan_issues[0]


def test_08d_orphan_fixture_totals(db, orphan_report):
    """README: fixture-orphan.tlg — 78 executions, sum(-proceeds) 1549.56262,
    commissions -21.61673468."""
    assert orphan_report.rows_parsed["executions"] == 78
    execs = db.query(Execution).all()
    assert abs(sum(-float(e.proceeds) for e in execs) - 1549.56262) <= TOL
    assert abs(sum(float(e.commission) for e in execs) - (-21.61673468)) <= TOL


# ---------------------------------------------------------------- test 9
def test_09_open_positions_after_import(db, complete_report):
    positions = compute_open_positions(db, ACCOUNT_ID)
    by_symbol = {}
    for instrument_id, pos in positions.items():
        inst = db.get(Instrument, instrument_id)
        by_symbol[inst.broker_symbol] = pos.quantity

    assert by_symbol["BIDM"] == pytest.approx(12.0)
    assert by_symbol["MEMC  320617P00012470"] == pytest.approx(-1.0)
    assert by_symbol["OMNI  330121C00172000"] == pytest.approx(1.0)
    assert by_symbol["OMNI  330121C00174150"] == pytest.approx(-1.0)
    assert by_symbol.get("BCGX", 0.0) == pytest.approx(0.0)


# --------------------------------------------------------------- test 10
def test_10_reconciliation_against_lot_records(db, complete_report):
    assert complete_report.reconciliation_passed is True
    errors = [i for i in complete_report.reconciliation_issues if i.startswith("[error]")]
    assert errors == []
    # Cost basis within $0.01 on every LOT-reported instrument, incl. BIDM's
    # commission-inclusive 124.94617629/share (§2.5 worked example).
    warnings = [i for i in complete_report.reconciliation_issues if i.startswith("[warning]")]
    assert warnings == []

    positions = compute_open_positions(db, ACCOUNT_ID)
    bidm = _instrument(db, "BIDM")
    assert positions[bidm.id].cost_basis_price == pytest.approx(124.94617629, abs=TOL)
    # BCGX is flat and correctly absent from STK_LOT.
    bcgx = _instrument(db, "BCGX")
    assert positions[bcgx.id].quantity == pytest.approx(0.0)


# --------------------------------------------------------------- test 11
def test_11_leg_level_round_trip(db, complete_report):
    assert _net(db, "ACME  310813P00172000") == pytest.approx(-61.372436, abs=TOL)
    assert _net(db, "ACME  310813P00180600") == pytest.approx(103.745084, abs=TOL)


# --------------------------------------- tests 12-14 (leg sums; §6 grouping is Phase 4)
def test_12_vertical_leg_sum_matches_group_pnl(db, complete_report):
    total = _net(db, "ACME  310813P00172000") + _net(db, "ACME  310813P00180600")
    assert total == pytest.approx(42.372648, abs=TOL)


def test_13_expiration_handling(db, complete_report):
    inst = _instrument(db, "ACME  310725P00182750")
    expiry_execs = [
        e for e in db.query(Execution).filter(Execution.instrument_id == inst.id).all() if "Ep" in e.flags
    ]
    assert len(expiry_execs) == 1
    assert float(expiry_execs[0].price) == 0.0
    assert float(expiry_execs[0].commission) == 0.0

    total = _net(db, "ACME  310725P00182750") + _net(db, "ACME  310725P00189200")
    assert total == pytest.approx(20.904516, abs=TOL)


def test_14_mes_spread_leg_sum(db, complete_report):
    total = _net(db, "EX3N1 P3014.3") + _net(db, "EX3N1 P3057.3")
    assert total == pytest.approx(1.9049, abs=TOL)


# --------------------------------------------------------------- test 15
def test_15_trading_day_assignment(db, complete_report):
    fop = db.query(Execution).filter(Execution.broker_trade_id == "921666627").one()
    assert fop.executed_at.date() == dt.date(2031, 7, 6)
    assert fop.trading_day == dt.date(2031, 7, 7)  # 18:12:09 ET -> next session

    stk = db.query(Execution).filter(Execution.broker_trade_id == "5358356122").one()
    assert stk.trading_day == dt.date(2031, 6, 27)


# --------------------------------------------------------------- test 16
def test_16_futures_option_multiplier_and_points(db, complete_report):
    inst = _instrument(db, "EX3N1 P3014.3")
    assert float(inst.multiplier) == 5.0
    trade = _trades_for(db, "EX3N1 P3014.3")[0]
    assert float(trade.pnl_points) == pytest.approx(float(trade.net_pnl) / 5.0, abs=1e-6)


# -------------------------------------------------------------- test 16b
def test_16b_out_of_window_date_warns_but_imports(db, complete_report):
    assert complete_report.status == "SUCCESS"
    assert any("2031-04-09" in w for w in complete_report.warnings)
    fbcg_open = db.query(Execution).filter(Execution.broker_trade_id == "5358356121").one()
    assert fbcg_open.out_of_window_warning is True


# ------------------------------------------------- test 62 (books, §6.5)
def test_62_book_assignment(db, complete_report):
    for symbol in ("BIDM", "BCGX"):
        for t in _trades_for(db, symbol):
            assert t.book == "Holdings"
    for symbol in ("ACME  310813P00172000", "EX3N1 P3014.3"):
        for t in _trades_for(db, symbol):
            assert t.book == "Options Income"


def test_63_book_contamination_guard(db, complete_report):
    """BCGX's +257.02 must not reach options statistics — in either
    analysis mode, since the book partition is orthogonal to the toggle."""
    from app.api.routes import _load_stat_inputs
    from app.models.enums import AnalysisMode

    for mode in (AnalysisMode.LEG, AnalysisMode.STRATEGY):
        trade_stats, _ = _load_stat_inputs(db, ACCOUNT_ID, "Options Income", mode)
        assert trade_stats, mode
        assert all(abs(t.net_pnl - 257.017616) > TOL for t in trade_stats), mode


# ----------------------------------------- tests 65-74 (Expiry Watch, §9.5)
def test_65_open_options_listed(db, complete_report):
    from app.expiry_watch.service import build_expiry_watch

    rows = build_expiry_watch(db, ACCOUNT_ID, dt.date(2031, 8, 8))
    assert len(rows) == 3
    assert {r.symbol for r in rows} == {
        "MEMC  320617P00012470",
        "OMNI  330121C00172000",
        "OMNI  330121C00174150",
    }


def test_66_exercise_style_populated(db, complete_report):
    assert _instrument(db, "MEMC  320617P00012470").exercise_style == ExerciseStyle.AMERICAN
    assert _instrument(db, "OMNI  330121C00172000").exercise_style == ExerciseStyle.AMERICAN
    for sym in ("EX3N1 P3014.3", "EXN1 P3083.1", "X1BN1 P3083.1", "EXQ1 P3031.5"):
        assert _instrument(db, sym).exercise_style == ExerciseStyle.EUROPEAN, sym


def test_68_assignment_projection_stock(db, complete_report):
    from app.expiry_watch.service import build_expiry_watch

    rows = {r.symbol: r for r in build_expiry_watch(db, ACCOUNT_ID, dt.date(2031, 8, 8))}
    memc = rows["MEMC  320617P00012470"]
    assert memc.quantity == pytest.approx(-1.0)
    assert "Long 100 shares" in memc.projection
    assert memc.projection_cash_required == pytest.approx(1247.0)


def test_69_assignment_projection_futures(db, complete_report):
    """A naked MES 7150 P projects long 1 MES future at ~$35,750 notional.
    The fixture's MES options are all closed by 2031-08-08, so this asserts
    the projection function directly on that instrument."""
    from app.expiry_watch.service import _project

    inst = _instrument(db, "EX2Q1 P3074.5")
    projection, notional, cash = _project(inst, -1.0)
    assert "Long 1 MES future(s) at 3074.5" in projection
    assert notional == pytest.approx(15372.5)


def test_71_renders_without_prices(db, complete_report):
    from app.expiry_watch.service import build_expiry_watch

    rows = build_expiry_watch(db, ACCOUNT_ID, dt.date(2031, 8, 8))
    for r in rows:
        assert r.expiry is not None
        assert r.strike is not None
        assert r.quantity is not None
        assert r.projection
        assert r.reference_price is None  # no PriceSource in v1


def test_73_trading_day_dte(db, complete_report):
    from app.expiry_watch.service import build_expiry_watch

    rows = {r.symbol: r for r in build_expiry_watch(db, ACCOUNT_ID, dt.date(2031, 8, 8))}
    memc = rows["MEMC  320617P00012470"]
    assert memc.dte_calendar == (dt.date(2032, 6, 17) - dt.date(2031, 8, 8)).days
    assert memc.dte_trading < memc.dte_calendar


def test_74_no_probability_output(db, complete_report):
    from dataclasses import asdict

    from app.expiry_watch.service import build_expiry_watch

    rows = build_expiry_watch(db, ACCOUNT_ID, dt.date(2031, 8, 8))
    banned = ("probability", "prob_", "likelihood", "signal", "recommend", "score")
    for r in rows:
        for key in asdict(r):
            assert not any(b in key.lower() for b in banned)
