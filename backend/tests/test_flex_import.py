"""§16.1 tests 26-28 and §16.3 transfer handling, against `fixture-flex.xml`."""

import datetime as dt

import pytest

from app.importer.closed_lots import check_closed_lots
from app.importer.pipeline import import_file, import_tlg_file
from app.models import (
    ClosedLot,
    Execution,
    Instrument,
    PositionTransfer,
    Trade,
    TradeExecution,
    TransferBasisPolicy,
    get_settings_row,
)
from app.models.enums import AssetClass, ExerciseStyle, LegRole, Origin, TradeStatus
from app.parsers.flex import parse_flex
from app.trades.basis import BasisResolver
from app.trades.constructor import rebuild_trades
from tests.conftest import ACCOUNT_ID, FIXTURES

TOL = 0.01


def _flex_bytes():
    return (FIXTURES / "fixture-flex.xml").read_bytes()


def _orphan_bytes():
    return (FIXTURES / "fixture-orphan.tlg").read_bytes()


@pytest.fixture
def flex_report(db):
    return import_file(db, "fixture-flex.xml", _flex_bytes())


def _instrument(db, symbol):
    return db.query(Instrument).filter(Instrument.broker_symbol == symbol).one()


# ----------------------------------------------------- parser-level checks
def test_flex_parses_all_sections():
    result = parse_flex((FIXTURES / "fixture-flex.xml").read_text())
    assert {"Trades", "Trades:CLOSED_LOT", "OpenPositions", "Transfers", "CashTransactions", "ChangeInNAV"} <= result.sections_seen
    assert len(result.executions) == 78
    assert len(result.closed_lots) == 40
    assert len(result.transfers) == 1


def test_flex_sign_conventions_hold_across_every_execution():
    """§3.9 — verified exactly, 0 violations across 78 executions.
    `tradeMoney == quantity x multiplier x price`, and Flex `proceeds` is its
    negation. Any violation surfaces as a parser warning."""
    result = parse_flex((FIXTURES / "fixture-flex.xml").read_text())
    for e in result.executions:
        assert abs(e.quantity * e.multiplier * e.price - e.proceeds) <= TOL
    assert not [w for w in result.warnings if "tradeMoney" in w or "expected Flex proceeds" in w]


def test_flex_nav_matches_the_fixture_readme():
    result = parse_flex((FIXTURES / "fixture-flex.xml").read_text())
    assert result.nav.twr == pytest.approx(10.24784403)
    assert result.nav.starting_value == pytest.approx(389.15029091478)
    assert result.nav.ending_value == pytest.approx(4350.85747501788)
    assert result.nav.realized == pytest.approx(437.6769436691, abs=TOL)
    # §5.5: IBKR backs the pre-transfer gain out of its own figures.
    # positionAmount - cost = 2598.275 - 2490.388
    assert result.nav.transferred_pnl_adjustments == pytest.approx(-107.887)


def test_security_info_removes_the_need_for_symbol_parsing():
    """§2.4 — the MES local symbols are not reliably parseable; SecurityInfo
    supplies every field directly."""
    result = parse_flex((FIXTURES / "fixture-flex.xml").read_text())
    mes = next(e for e in result.executions if e.symbol.broker_symbol == "EX1Q1 P3061.6")
    assert mes.symbol.asset_class == AssetClass.FOP
    assert mes.symbol.strike == pytest.approx(3061.6)
    assert mes.symbol.expiry == dt.date(2031, 8, 8)
    assert mes.symbol.right.value == "P"
    assert mes.symbol.multiplier if hasattr(mes.symbol, "multiplier") else True
    # underlyingSymbol is the specific contract; root is derived from it.
    assert mes.symbol.underlying_symbol == "MESU1"
    assert mes.symbol.underlying_root == "MES"
    assert mes.symbol.conid == "700005080"


# --------------------------------------------------------------- test 26
def test_26_cross_format_equivalence(db):
    """The 78 shared executions from `fixture-orphan.tlg` and the Flex XML
    describe the same activity and must agree on totals."""
    tlg = import_tlg_file(db, "fixture-orphan.tlg", _orphan_bytes())
    tlg_execs = db.query(Execution).filter(Execution.account_id == ACCOUNT_ID).all()
    tlg_proceeds = sum(-float(e.proceeds) for e in tlg_execs)
    tlg_commission = sum(float(e.commission) for e in tlg_execs)
    tlg_trades = {
        (t.instrument.broker_symbol, t.side.value): round(float(t.net_pnl), 2)
        for t in db.query(Trade).filter(Trade.account_id == ACCOUNT_ID).all()
        if t.status == TradeStatus.CLOSED
    }
    assert tlg.rows_parsed["executions"] == 78

    # Fresh database state for the Flex side. expunge_all() detaches the
    # now-deleted rows from the session's identity map; without it the next
    # flush re-associates stale objects with reused primary keys.
    from app.db import Base, engine

    db.rollback()
    db.expunge_all()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    flex = import_file(db, "fixture-flex.xml", _flex_bytes())
    flex_execs = db.query(Execution).filter(Execution.account_id == ACCOUNT_ID).all()
    flex_proceeds = sum(-float(e.proceeds) for e in flex_execs)
    flex_commission = sum(float(e.commission) for e in flex_execs)
    flex_trades = {
        (t.instrument.broker_symbol, t.side.value): round(float(t.net_pnl), 2)
        for t in db.query(Trade).filter(Trade.account_id == ACCOUNT_ID).all()
        if t.status == TradeStatus.CLOSED and t.origin != Origin.TRANSFERRED
    }

    assert flex.rows_parsed["executions"] == 78
    assert flex_proceeds == pytest.approx(tlg_proceeds, abs=TOL)
    assert flex_commission == pytest.approx(tlg_commission, abs=TOL)
    assert flex_proceeds == pytest.approx(1549.56262, abs=TOL)
    assert flex_commission == pytest.approx(-21.616737, abs=TOL)

    # Every traded round trip agrees leg for leg. BCGX is excluded above: it
    # is an orphan on the .tlg side and a properly based transfer on the Flex
    # side, which is the whole point of having the Transfers section.
    assert flex_trades == tlg_trades


def test_26b_tlg_order_id_matches_flex_ib_order_id(db, flex_report):
    """§3.2 — a `.tlg` row and a Flex row for the same fill match only via
    ibOrderID, and it is 78-for-78 distinct in this window."""
    order_ids = [e.ib_order_id for e in db.query(Execution).all() if e.ib_order_id]
    tlg_ids = set()
    for line in (FIXTURES / "fixture-orphan.tlg").read_text().splitlines():
        if line.startswith(("STK_TRD|", "OPT_TRD|", "FOP_TRD|")):
            tlg_ids.add(line.split("|")[1])
    assert len(set(order_ids)) == len(order_ids), "no duplicate ibOrderIDs in this window"
    assert set(order_ids) == tlg_ids


def test_26b2_a_partially_filled_order_keeps_every_fill(db):
    """A regression: fills 2..n of one order were dropped as duplicates.

    `ibOrderID` is per *order*, so a partially filled order emits several
    Flex rows carrying it. The .tlg-supersede path keyed off it and treated
    the later fills as repeats, so a 2-lot vertical was imported as 1 lot —
    and its 2-lot expiration then closed the one lot on file and re-opened
    the remainder as a phantom position. Dedupe is per fill (transactionID);
    only a .tlg-sourced prior may collide on ibOrderID.
    """
    from tests.synthetic_flex import ACME_PUT_100, ACCOUNT, Fill, Statement

    statement = Statement(
        from_date=dt.date(2031, 8, 21),
        to_date=dt.date(2031, 8, 21),
        fills=[
            Fill(
                ACME_PUT_100,
                quantity=-1,
                price=3.0,
                when=dt.datetime(2031, 8, 21, 10, 0, 1),
                transaction_id="fill-one",
                order_id="order-42",
            ),
            Fill(
                ACME_PUT_100,
                quantity=-1,
                price=3.0,
                when=dt.datetime(2031, 8, 21, 10, 0, 3),
                transaction_id="fill-two",
                order_id="order-42",
            ),
        ],
    )
    report = import_file(db, "partial-fill.xml", statement.as_bytes())

    assert report.rows_inserted == 2, "both fills of the order must survive"
    assert report.rows_duplicate == 0
    rows = db.query(Execution).filter(Execution.account_id == ACCOUNT).all()
    assert {e.transaction_id for e in rows} == {"fill-one", "fill-two"}
    assert {e.ib_order_id for e in rows} == {"order-42"}, "one order, two fills"
    assert sum(float(e.quantity) for e in rows) == -2.0


def test_26b3_a_partially_filled_order_is_still_idempotent(db):
    """The per-fill key still has to reject a genuine re-import."""
    from tests.synthetic_flex import ACME_PUT_100, ACCOUNT, Fill, Statement

    statement = Statement(
        from_date=dt.date(2031, 8, 21),
        to_date=dt.date(2031, 8, 21),
        fills=[
            Fill(
                ACME_PUT_100,
                quantity=-1,
                price=3.0,
                when=dt.datetime(2031, 8, 21, 10, 0, 1),
                transaction_id="fill-one",
                order_id="order-42",
            ),
            Fill(
                ACME_PUT_100,
                quantity=-1,
                price=3.0,
                when=dt.datetime(2031, 8, 21, 10, 0, 3),
                transaction_id="fill-two",
                order_id="order-42",
            ),
        ],
    )
    import_file(db, "partial-fill.xml", statement.as_bytes())
    again = import_file(db, "partial-fill.xml", statement.as_bytes())

    assert again.rows_inserted == 0
    assert again.rows_duplicate == 2
    assert db.query(Execution).filter(Execution.account_id == ACCOUNT).count() == 2


def test_26b4_a_failed_reconciliation_records_why(db):
    """A PARTIAL import has to say what was wrong, not just that it was.

    `warnings` is a plain JSON column, so the list handed to ImportFile(...)
    is not mutation-tracked. Everything appended after the record was built —
    every reconciliation and closed-lot issue — was silently dropped, and the
    import went PARTIAL with only its parse-time warnings on file.
    """
    from tests.synthetic_flex import ACME_PUT_100, ACCOUNT, Fill, OpenPositionRow, Statement
    from app.models import ImportFile
    from app.models.enums import ImportStatus

    # One fill on file, but the broker says the position is twice that: the
    # reconciliation must fail, and must leave its reason behind.
    statement = Statement(
        from_date=dt.date(2031, 8, 21),
        to_date=dt.date(2031, 8, 21),
        fills=[
            Fill(
                ACME_PUT_100,
                quantity=-1,
                price=3.0,
                when=dt.datetime(2031, 8, 21, 10, 0, 1),
                transaction_id="fill-one",
                order_id="order-42",
            )
        ],
        positions=[OpenPositionRow(ACME_PUT_100, quantity=-2, cost_basis_price=3.0, mark_price=3.0)],
    )
    report = import_file(db, "mismatch.xml", statement.as_bytes())

    assert report.status == ImportStatus.PARTIAL.value
    record = db.get(ImportFile, report.import_file_id)
    assert any("reconciliation" in w for w in record.warnings), (
        f"the reason a PARTIAL import failed must be persisted, got {record.warnings}"
    )


def test_26b5_a_position_reported_as_several_broker_lots_reconciles(db):
    """The broker reports a position lot by lot, and two fills make two lots.

    Keying the snapshot rows by instrument kept only the last one, so a
    correct 2-lot position was compared against a single -1 lot and reported
    as a quantity mismatch — an error raised on data that was right.
    """
    from tests.synthetic_flex import ACME_PUT_100, Fill, OpenPositionRow, Statement

    statement = Statement(
        from_date=dt.date(2031, 8, 21),
        to_date=dt.date(2031, 8, 21),
        fills=[
            Fill(
                ACME_PUT_100,
                quantity=-1,
                price=3.0,
                when=dt.datetime(2031, 8, 21, 10, 0, 1),
                transaction_id="fill-one",
                order_id="order-42",
            ),
            Fill(
                ACME_PUT_100,
                quantity=-1,
                price=3.0,
                when=dt.datetime(2031, 8, 21, 10, 0, 3),
                transaction_id="fill-two",
                order_id="order-42",
            ),
        ],
        # Two lots of -1, exactly as IB reports a two-fill position.
        positions=[
            OpenPositionRow(ACME_PUT_100, quantity=-1, cost_basis_price=-3.0, mark_price=-3.0),
            OpenPositionRow(ACME_PUT_100, quantity=-1, cost_basis_price=-3.0, mark_price=-3.0),
        ],
    )
    report = import_file(db, "two-lots.xml", statement.as_bytes())

    assert report.reconciliation_passed, report.reconciliation_issues
    assert not any("Quantity mismatch" in i for i in report.reconciliation_issues)


def test_26c_reimporting_flex_is_idempotent(db, flex_report):
    """§16.1 test 25 — a 30-day trailing resync produces 0 duplicates."""
    before = db.query(Execution).count()
    second = import_file(db, "fixture-flex.xml", _flex_bytes())
    assert second.rows_inserted == 0
    assert second.rows_duplicate == 78
    assert db.query(Execution).count() == before


# --------------------------------------------------------------- test 27
def test_27_closed_lot_reconciliation(db, flex_report):
    """Computed per-trade realized P&L matches IB's own `fifoPnlRealized`."""
    check = flex_report.closed_lot_check
    assert check is not None
    assert check["passed"] is True
    assert check["lots_total"] == 40
    assert check["trades_checked"] == check["trades_matched"] == 35
    assert check["aggregate_difference"] == pytest.approx(0.0, abs=TOL)
    # The full lot sum ties out to ChangeInNAV.realized.
    assert check["ib_total_realized"] == pytest.approx(437.676946, abs=TOL)
    # BCGX is carved out under the default policy, not silently passed.
    assert check["skipped_transfers"] == 1


def test_27b_closed_lot_check_matches_under_original_cost(db, flex_report):
    """Under ORIGINAL_COST there is no divergence to carve out: every trade,
    including the transferred one, agrees with IB."""
    settings = get_settings_row(db)
    settings.transfer_basis_policy = TransferBasisPolicy.ORIGINAL_COST
    db.flush()
    rebuild_trades(db, ACCOUNT_ID, BasisResolver(db, ACCOUNT_ID, TransferBasisPolicy.ORIGINAL_COST))
    db.flush()

    check = check_closed_lots(db, ACCOUNT_ID, TransferBasisPolicy.ORIGINAL_COST)
    assert check.passed
    assert check.skipped_transfers == 0
    assert check.trades_checked == check.trades_matched == 36
    assert check.computed_total == pytest.approx(check.ib_total_checked, abs=TOL)


def test_27c_a_corrupted_trade_is_caught(db, flex_report):
    """The check must actually fail when computed P&L is wrong — otherwise it
    proves nothing."""
    trade = (
        db.query(Trade)
        .filter(
            Trade.account_id == ACCOUNT_ID,
            Trade.status == TradeStatus.CLOSED,
            Trade.origin == Origin.TRADED,
        )
        .first()
    )
    trade.net_pnl = float(trade.net_pnl) + 5.0
    db.flush()

    check = check_closed_lots(db, ACCOUNT_ID)
    assert not check.passed
    assert any("difference" in i.message for i in check.issues if i.severity == "error")


def test_27e_a_lot_opened_before_our_data_reads_as_truncation_not_a_bug(db, flex_report):
    """§5.5 case (a). IB matched the close against a lot it holds and we do
    not, so the two figures are not describing the same pair of fills.

    Reported as a warning naming the data gap, rather than an error that
    accuses the §5.2 matching algorithm of arithmetic it did not get wrong.
    """
    trade = (
        db.query(Trade)
        .filter(
            Trade.account_id == ACCOUNT_ID,
            Trade.status == TradeStatus.CLOSED,
            Trade.origin == Origin.TRADED,
        )
        .first()
    )
    trade.net_pnl = float(trade.net_pnl) + 5.0
    earliest = min(
        e.executed_at.date()
        for e in db.query(Execution).filter(Execution.instrument_id == trade.instrument_id)
    )
    for lot in db.query(ClosedLot).filter(ClosedLot.instrument_id == trade.instrument_id):
        lot.open_date = earliest - dt.timedelta(days=45)
    db.flush()

    check = check_closed_lots(db, ACCOUNT_ID)
    assert check.passed, "a data gap is not a failed reconciliation"
    assert check.truncated_trades == 1
    message = next(i.message for i in check.issues if i.severity == "warning")
    assert "Window truncation" in message and "re-sync" in message
    # And it is kept out of the aggregate, which would otherwise report the
    # same discrepancy a second time as an unexplained total.
    assert not any("Aggregate" in i.message for i in check.issues)


def test_27f_truncation_does_not_excuse_a_trade_that_still_reconciles(db, flex_report):
    """The carve-out is conditional on the numbers actually disagreeing —
    otherwise an old lot would silently exempt a trade from checking."""
    trade = (
        db.query(Trade)
        .filter(Trade.account_id == ACCOUNT_ID, Trade.origin == Origin.TRADED)
        .first()
    )
    earliest = min(
        e.executed_at.date()
        for e in db.query(Execution).filter(Execution.instrument_id == trade.instrument_id)
    )
    for lot in db.query(ClosedLot).filter(ClosedLot.instrument_id == trade.instrument_id):
        lot.open_date = earliest - dt.timedelta(days=45)
    db.flush()

    check = check_closed_lots(db, ACCOUNT_ID)
    assert check.truncated_trades == 0
    assert check.passed


def test_27d_the_transfer_carve_out_leaves_a_real_blind_spot(db, flex_report):
    """Documents a genuine limitation rather than implying full coverage.

    Under TRANSFER_MARK a transferred trade is deliberately not compared
    against IB, so an error in *its* P&L is invisible to this check. Under
    ORIGINAL_COST it is compared and the error surfaces. Anyone relying on
    the check should know which of the two they are running.
    """
    trade = db.query(Trade).filter(Trade.origin == Origin.TRANSFERRED).one()
    trade.net_pnl = float(trade.net_pnl) + 5.0
    db.flush()
    # The blind spot is specific to a *transfer* under TRANSFER_MARK. This
    # fixture has a Transfers row, so the trade is a genuine transfer rather
    # than the window-truncated case that used to share the label.
    assert trade.origin == Origin.TRANSFERRED

    assert check_closed_lots(db, ACCOUNT_ID, TransferBasisPolicy.TRANSFER_MARK).passed
    assert not check_closed_lots(db, ACCOUNT_ID, TransferBasisPolicy.ORIGINAL_COST).passed


# --------------------------------------------------------------- test 28
def test_28_brokerage_order_id_grouping(db, flex_report):
    """§6.1 — `brokerageOrderID` groups combos; `ibOrderID` does not."""
    executions = db.query(Execution).filter(Execution.account_id == ACCOUNT_ID).all()
    groups: dict[str, list[Execution]] = {}
    for e in executions:
        if e.brokerage_order_id:
            groups.setdefault(e.brokerage_order_id, []).append(e)

    two_leg = [g for g in groups.values() if len(g) == 2]
    one_leg = [g for g in groups.values() if len(g) == 1]
    no_order_id = [e for e in executions if not e.brokerage_order_id]

    assert len(two_leg) == 22
    assert len(one_leg) == 20
    assert len(no_order_id) == 14

    # Each two-leg group is a genuine vertical: same underlying, opposing sides.
    for group in two_leg:
        underlyings = {e.instrument.underlying_root for e in group}
        assert len(underlyings) == 1, "a combo must not span underlyings"
        assert len({float(e.quantity) > 0 for e in group}) == 2, "legs must oppose"

    # The 14 without one split into 10 expirations and 4 real fills (§6.1).
    expirations = [e for e in no_order_id if "Ep" in (e.flags or [])]
    assert len(expirations) == 10
    assert len(no_order_id) - len(expirations) == 4


def test_28b_ib_order_id_does_not_group_combos(db, flex_report):
    """The finding that overturned the original design: ibOrderID is per leg."""
    executions = db.query(Execution).filter(Execution.account_id == ACCOUNT_ID).all()
    by_order: dict[str, list[Execution]] = {}
    for e in executions:
        if e.ib_order_id:
            by_order.setdefault(e.ib_order_id, []).append(e)
    assert all(len(g) == 1 for g in by_order.values()), "ibOrderID is per leg, never a combo key"


# ------------------------------------------------- §16.3 transfer handling
def test_34_transfers_convert_orphan_close_to_a_based_trade(db, flex_report):
    """A Transfers row matching a close converts ORPHAN_CLOSE -> TRANSFERRED
    and synthesizes the opening leg."""
    bcgx = _instrument(db, "BCGX")
    trades = db.query(Trade).filter(Trade.instrument_id == bcgx.id).all()
    assert len(trades) == 1
    trade = trades[0]
    assert trade.status == TradeStatus.CLOSED
    assert trade.origin == Origin.TRANSFERRED
    # TRANSFER_MARK: positionAmount / quantity = 2598.275 / 100
    assert float(trade.avg_open_price) == pytest.approx(25.98275, abs=1e-6)
    # §5.5's worked example: -95.50 before the closing commission of -1.1423.
    assert float(trade.net_pnl) == pytest.approx(-41.556193, abs=TOL)


def test_35_synthetic_legs_are_marked_and_carry_no_execution(db, flex_report):
    bcgx = _instrument(db, "BCGX")
    trade = db.query(Trade).filter(Trade.instrument_id == bcgx.id).one()
    legs = db.query(TradeExecution).filter(TradeExecution.trade_id == trade.id).all()

    synthetic = [leg for leg in legs if leg.synthetic]
    assert len(synthetic) == 1
    assert synthetic[0].execution_id is None
    assert synthetic[0].leg_role == LegRole.OPEN
    assert synthetic[0].synthetic_source == "TRANSFER"

    real = [leg for leg in legs if not leg.synthetic]
    assert len(real) == 1
    assert real[0].execution_id is not None


def test_37_basis_policy_changes_pnl_but_not_cash_flow(db, flex_report):
    """The same position under either policy produces different trade P&L but
    identical cash flow — the equity curve must not move."""
    bcgx = _instrument(db, "BCGX")
    executions = db.query(Execution).filter(Execution.instrument_id == bcgx.id).all()
    cash_before = sum(float(e.cash_flow) for e in executions)
    mark_pnl = float(db.query(Trade).filter(Trade.instrument_id == bcgx.id).one().net_pnl)

    rebuild_trades(db, ACCOUNT_ID, BasisResolver(db, ACCOUNT_ID, TransferBasisPolicy.ORIGINAL_COST))
    db.flush()
    cost_pnl = float(db.query(Trade).filter(Trade.instrument_id == bcgx.id).one().net_pnl)

    assert mark_pnl == pytest.approx(-41.556193, abs=TOL)
    assert cost_pnl == pytest.approx(66.33080756, abs=TOL)
    assert mark_pnl != cost_pnl

    executions = db.query(Execution).filter(Execution.instrument_id == bcgx.id).all()
    assert sum(float(e.cash_flow) for e in executions) == pytest.approx(cash_before, abs=1e-9)


def test_38_transferred_trades_excluded_from_duration(db, flex_report):
    """An open date inherited from another broker makes hold time
    meaningless, under either policy."""
    bcgx = _instrument(db, "BCGX")
    trade = db.query(Trade).filter(Trade.instrument_id == bcgx.id).one()
    assert trade.excluded_from_duration is True


def test_transfer_price_zero_is_not_used_as_basis(db, flex_report):
    """§5.5 caveat, confirmed live: transferPrice is 0 and must not be read.
    Reading it would value the position at nothing and report the entire
    proceeds as profit."""
    transfer = db.query(PositionTransfer).one()
    assert float(transfer.transfer_price) == 0.0
    assert transfer.mark_price() == pytest.approx(25.98275)
    assert transfer.original_cost_price() == pytest.approx(24.90388)

    trade = db.query(Trade).filter(Trade.origin == Origin.TRANSFERRED).one()
    assert float(trade.avg_open_price) != 0.0
    assert any(w for w in flex_report.warnings if "transferPrice=0" in w)


def test_closed_lot_shortcut_supplies_basis_without_a_transfers_row(db):
    """§5.5 — a lot carries the opening basis even when the opening execution
    is outside the window, so the orphan problem does not arise for anything
    closed in-window. Verified by removing the Transfers section entirely."""
    xml = (FIXTURES / "fixture-flex.xml").read_text()
    start, end = xml.index("<Transfers>"), xml.index("</Transfers>") + len("</Transfers>")
    without_transfers = xml[:start] + xml[end:]

    report = import_file(db, "no-transfers.xml", without_transfers.encode())
    assert report.rows_parsed["transfers"] == 0

    bcgx = _instrument(db, "BCGX")
    trade = db.query(Trade).filter(Trade.instrument_id == bcgx.id).one()
    # Not TRANSFERRED: this opening execution exists and is merely outside
    # the window, so more history fixes it — which is the opposite of a
    # transfer, where no opening execution will ever exist (§5.5 a vs b).
    assert trade.origin == Origin.WINDOW_TRUNCATED
    # Falls back to the lot's own basis: cost / quantity = 2490.388 / 100.
    assert float(trade.avg_open_price) == pytest.approx(24.90388, abs=1e-6)
    leg = (
        db.query(TradeExecution)
        .filter(TradeExecution.trade_id == trade.id, TradeExecution.synthetic.is_(True))
        .one()
    )
    assert leg.synthetic_source == "CLOSED_LOT"


def test_flex_import_reconciles_against_open_positions(db, flex_report):
    """§3.4 — Flex `OpenPositions` replaces STK_LOT/OPT_LOT for reconciliation."""
    assert flex_report.reconciliation_passed is True
    assert [i for i in flex_report.reconciliation_issues if i.startswith("[error]")] == []
    assert flex_report.rows_parsed["positions"] == 4


def test_flex_populates_conid_and_exercise_style(db, flex_report):
    mes = _instrument(db, "EX1Q1 P3061.6")
    assert mes.conid == "700005080"
    assert mes.underlying_symbol == "MESU1"
    assert mes.underlying_root == "MES"
    assert mes.exercise_style == ExerciseStyle.EUROPEAN

    omni = _instrument(db, "OMNI  330121C00172000")
    assert omni.exercise_style == ExerciseStyle.AMERICAN
