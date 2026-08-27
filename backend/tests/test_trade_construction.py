"""Trade-construction paths the fixture does not exercise, plus the
partial-close realization the §16 round-trip assertions can't distinguish."""

import datetime as dt

import pytest

from app.importer.reconciliation import compute_open_positions
from app.models import Execution, ImportFile, Instrument, Trade
from app.models.enums import (
    Action,
    AssetClass,
    ImportSource,
    ImportStatus,
    SourceFormat,
    TradeSide,
    TradeStatus,
)
from app.trades.constructor import rebuild_trades
from app.trading_day import EASTERN, derive_trading_day, localize
from tests.conftest import ACCOUNT_ID


@pytest.fixture
def stock_instrument(db):
    inst = Instrument(
        asset_class=AssetClass.STK,
        root_symbol="TEST",
        broker_symbol="TEST",
        description="Test Co",
        multiplier=1,
    )
    db.add(inst)
    import_file = ImportFile(
        source=ImportSource.FILE_UPLOAD,
        source_format=SourceFormat.TLG,
        filename="synthetic",
        sha256="0" * 64,
        retrieved_at=dt.datetime.now(dt.timezone.utc),
        account_id=ACCOUNT_ID,
        status=ImportStatus.SUCCESS,
    )
    db.add(import_file)
    db.flush()
    return inst, import_file


def _add_exec(db, inst, import_file, *, tid, action, qty, price, when, commission=0.0, flags=None):
    executed_at = localize(when.date(), when.time())
    proceeds = qty * float(inst.multiplier) * price
    e = Execution(
        import_file_id=import_file.id,
        source_line=1,
        account_id=ACCOUNT_ID,
        broker_trade_id=tid,
        instrument_id=inst.id,
        action=action,
        flags=flags or (["O"] if "OPEN" in action.value else ["C"]),
        executed_at=executed_at,
        trading_day=derive_trading_day(executed_at, inst.asset_class),
        quantity=qty,
        multiplier=inst.multiplier,
        price=price,
        proceeds=proceeds,
        commission=commission,
        fx_rate=1,
        cash_flow=-proceeds + commission,
        exchange="TEST",
    )
    db.add(e)
    db.flush()
    return e


def test_partial_close_realizes_only_the_closed_portion(db, stock_instrument):
    """A trade still holding lots must report P&L on what it actually sold,
    not -sum(proceeds), which is the open cost basis in disguise."""
    inst, imp = stock_instrument
    base = dt.datetime(2026, 6, 1, 10, 0, 0)
    _add_exec(db, inst, imp, tid="1", action=Action.BUYTOOPEN, qty=100, price=10.0, when=base)
    _add_exec(
        db, inst, imp, tid="2", action=Action.SELLTOCLOSE, qty=-40, price=12.0,
        when=base + dt.timedelta(days=1),
    )

    trades = rebuild_trades(db, ACCOUNT_ID)
    assert len(trades) == 1
    t = trades[0]
    assert t.status == TradeStatus.OPEN
    assert float(t.remaining_qty) == pytest.approx(60.0)
    assert float(t.gross_pnl) == pytest.approx(40 * (12.0 - 10.0))
    assert float(t.net_pnl) == pytest.approx(80.0)


def test_reopening_after_flat_starts_a_new_trade(db, stock_instrument):
    """§5.1 — going flat ends the round trip; reopening is a new trade."""
    inst, imp = stock_instrument
    base = dt.datetime(2026, 6, 1, 10, 0, 0)
    _add_exec(db, inst, imp, tid="1", action=Action.BUYTOOPEN, qty=10, price=10.0, when=base)
    _add_exec(db, inst, imp, tid="2", action=Action.SELLTOCLOSE, qty=-10, price=11.0, when=base + dt.timedelta(hours=1))
    _add_exec(db, inst, imp, tid="3", action=Action.BUYTOOPEN, qty=5, price=12.0, when=base + dt.timedelta(hours=2))
    _add_exec(db, inst, imp, tid="4", action=Action.SELLTOCLOSE, qty=-5, price=13.0, when=base + dt.timedelta(hours=3))

    trades = sorted(rebuild_trades(db, ACCOUNT_ID), key=lambda t: t.opened_at)
    assert len(trades) == 2
    assert [float(t.net_pnl) for t in trades] == pytest.approx([10.0, 5.0])
    assert all(t.status == TradeStatus.CLOSED for t in trades)


def test_position_flip_splits_execution_across_two_trades(db, stock_instrument):
    """§5.3 — long 5, sell 8 closes the long and opens a short with the residual."""
    inst, imp = stock_instrument
    base = dt.datetime(2026, 6, 1, 10, 0, 0)
    _add_exec(db, inst, imp, tid="1", action=Action.BUYTOOPEN, qty=5, price=10.0, when=base)
    _add_exec(db, inst, imp, tid="2", action=Action.SELLTOCLOSE, qty=-8, price=12.0, when=base + dt.timedelta(hours=1))

    trades = sorted(rebuild_trades(db, ACCOUNT_ID), key=lambda t: (t.opened_at, t.id))
    assert len(trades) == 2

    closed, opened = trades[0], trades[1]
    assert closed.side == TradeSide.LONG
    assert closed.status == TradeStatus.CLOSED
    assert float(closed.gross_pnl) == pytest.approx(5 * (12.0 - 10.0))

    assert opened.side == TradeSide.SHORT
    assert opened.status == TradeStatus.OPEN
    assert float(opened.remaining_qty) == pytest.approx(-3.0)
    assert float(opened.gross_pnl) == pytest.approx(0.0)

    positions = compute_open_positions(db, ACCOUNT_ID)
    assert positions[inst.id].quantity == pytest.approx(-3.0)


def test_short_round_trip_pnl_sign(db, stock_instrument):
    inst, imp = stock_instrument
    base = dt.datetime(2026, 6, 1, 10, 0, 0)
    _add_exec(db, inst, imp, tid="1", action=Action.SELLTOOPEN, qty=-10, price=20.0, when=base, commission=-1.0)
    _add_exec(
        db, inst, imp, tid="2", action=Action.BUYTOCLOSE, qty=10, price=18.0,
        when=base + dt.timedelta(hours=1), commission=-1.0,
    )

    trades = rebuild_trades(db, ACCOUNT_ID)
    assert len(trades) == 1
    t = trades[0]
    assert t.side == TradeSide.SHORT
    assert float(t.gross_pnl) == pytest.approx(20.0)
    assert float(t.net_pnl) == pytest.approx(18.0)


def test_futures_session_boundary_is_dst_aware():
    """§14.1 — the 18:00 boundary is 18:00 Eastern, not a fixed UTC offset."""
    winter = dt.datetime(2026, 1, 15, 18, 5, tzinfo=EASTERN)
    summer = dt.datetime(2026, 7, 15, 18, 5, tzinfo=EASTERN)
    assert derive_trading_day(winter, AssetClass.FOP) == dt.date(2026, 1, 16)
    assert derive_trading_day(summer, AssetClass.FOP) == dt.date(2026, 7, 16)
    assert winter.utcoffset() != summer.utcoffset()


def test_equities_ignore_the_session_boundary():
    evening = dt.datetime(2026, 7, 15, 19, 0, tzinfo=EASTERN)
    assert derive_trading_day(evening, AssetClass.STK) == dt.date(2026, 7, 15)
    assert derive_trading_day(evening, AssetClass.OPT) == dt.date(2026, 7, 15)
