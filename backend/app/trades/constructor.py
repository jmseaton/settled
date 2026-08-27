"""§5 — trade construction: execution -> position -> round-trip trade.

FIFO only (§5.2, "decided, and fixed"). Full rebuild on every import (§13
"Recompute strategy") — trades are entirely derived and disposable.

Money handling note: gross P&L is accumulated as `-sum(proceeds)` over a
trade's attributed executions and net as `gross + sum(commission)` (§2.2),
*not* reconstructed from per-lot prices — this matches the broker
convention exactly and sidesteps rounding drift. The commission-inclusive
"cost basis price" used for reconciliation (§2.5) is a separate, LOT-scoped
calculation kept in `unit_commission` below and never used for trade P&L.
"""

import datetime as dt
from collections import deque
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.enums import (
    Action,
    AssetClass,
    LegRole,
    Origin,
    StrategyType,
    TradeSide,
    TradeStatus,
)
from app.models.execution import Execution
from app.models.instrument import Instrument
from app.models.trade import Trade, TradeExecution
from app.trading_day import derive_trading_day

_OPENING_ACTIONS = {Action.BUYTOOPEN, Action.SELLTOOPEN}
_CLOSING_ACTIONS = {Action.BUYTOCLOSE, Action.SELLTOCLOSE}


@dataclass
class _Lot:
    remaining_qty: float  # signed: same sign as the trade side
    unit_price: float
    unit_commission: float  # opening commission per contract/share; fixed once set
    open_execution_id: int


@dataclass
class _TradeAccumulator:
    trade: Trade
    lots: deque
    multiplier: float = 1.0
    open_notional: float = 0.0
    open_qty_abs: float = 0.0
    close_notional: float = 0.0
    close_qty_abs: float = 0.0
    # Realized only — accumulated per FIFO match, so a partially closed or
    # still-open trade reports P&L on the closed portion and nothing more.
    realized_gross: float = 0.0
    realized_commissions: float = 0.0
    matched_open_basis: float = 0.0
    execution_ids: set | None = None

    def __post_init__(self):
        if self.execution_ids is None:
            self.execution_ids = set()

    def realize(self, lot: _Lot, matched: float, close_proceeds_per_unit: float, close_commission_per_unit: float) -> None:
        lot_sign = 1.0 if lot.remaining_qty > 0 else -1.0
        open_portion_proceeds = matched * self.multiplier * lot.unit_price * lot_sign
        close_portion_proceeds = close_proceeds_per_unit * matched
        self.realized_gross += -(open_portion_proceeds + close_portion_proceeds)
        self.realized_commissions += lot.unit_commission * matched + close_commission_per_unit * matched
        self.matched_open_basis += matched * self.multiplier * lot.unit_price


def rebuild_trades(db: Session, account_id: str, basis_resolver=None) -> list[Trade]:
    db.query(TradeExecution).filter(
        TradeExecution.trade_id.in_(db.query(Trade.id).filter(Trade.account_id == account_id))
    ).delete(synchronize_session=False)
    db.query(Trade).filter(Trade.account_id == account_id).delete(synchronize_session=False)
    db.flush()

    executions = (
        db.query(Execution)
        .filter(Execution.account_id == account_id)
        .order_by(Execution.instrument_id, Execution.executed_at, Execution.broker_trade_id)
        .all()
    )

    by_instrument: dict[int, list[Execution]] = {}
    for e in executions:
        by_instrument.setdefault(e.instrument_id, []).append(e)

    all_trades: list[Trade] = []
    for instrument_id, execs in by_instrument.items():
        instrument = db.get(Instrument, instrument_id)
        all_trades.extend(
            _construct_for_instrument(db, account_id, instrument, execs, basis_resolver)
        )

    db.flush()
    return all_trades


def _is_opening(exc: Execution) -> bool:
    if exc.action in _OPENING_ACTIONS:
        return True
    if exc.action in _CLOSING_ACTIONS:
        return False
    return "O" in exc.flags


def _new_trade(db: Session, account_id: str, instrument: Instrument, side: TradeSide, opened_at) -> Trade:
    trade = Trade(
        account_id=account_id,
        instrument_id=instrument.id,
        side=side,
        status=TradeStatus.OPEN,
        origin=Origin.TRADED,
        book="Options Income" if instrument.asset_class in (AssetClass.OPT, AssetClass.FOP) else "Holdings",
        opened_at=opened_at,
        open_qty=0,
    )
    db.add(trade)
    db.flush()
    return trade


def _attach_leg(
    db: Session,
    trade: Trade,
    execution_id: int | None,
    matched_qty: float,
    role: LegRole,
    synthetic: bool = False,
    synthetic_source: str | None = None,
) -> None:
    db.add(
        TradeExecution(
            trade_id=trade.id,
            execution_id=execution_id,
            matched_qty=matched_qty,
            leg_role=role,
            synthetic=synthetic,
            synthetic_source=synthetic_source,
        )
    )


def _finalize_trade(acc: _TradeAccumulator, closed_at, status: TradeStatus) -> None:
    trade = acc.trade
    trade.status = status
    trade.closed_at = closed_at
    trade.gross_pnl = acc.realized_gross
    trade.commissions = acc.realized_commissions
    trade.net_pnl = trade.gross_pnl + trade.commissions
    trade.avg_open_price = acc.open_notional / acc.open_qty_abs if acc.open_qty_abs else None
    trade.avg_close_price = acc.close_notional / acc.close_qty_abs if acc.close_qty_abs else None
    trade.total_buy_qty = acc.open_qty_abs if trade.side == TradeSide.LONG else acc.close_qty_abs
    trade.total_sell_qty = acc.close_qty_abs if trade.side == TradeSide.LONG else acc.open_qty_abs
    trade.open_qty = acc.open_qty_abs
    trade.remaining_qty = sum(lot.remaining_qty for lot in acc.lots)
    trade.execution_count = len(acc.execution_ids)
    if closed_at is not None:
        trade.duration_seconds = (closed_at - trade.opened_at).total_seconds()

    multiplier = acc.multiplier
    # Basis of the *matched* portion — the only part the realized figures
    # relate to, so a partial close's return_pct isn't diluted by lots that
    # are still open.
    trade.cost_basis = acc.matched_open_basis or None
    if acc.matched_open_basis:
        trade.return_pct = trade.net_pnl / abs(acc.matched_open_basis)
    if acc.close_qty_abs:
        trade.pnl_per_qty = trade.net_pnl / acc.close_qty_abs
    if trade.instrument.asset_class == AssetClass.FOP and multiplier:
        trade.pnl_points = trade.net_pnl / multiplier
        if trade.instrument.tick_size:
            trade.pnl_ticks = trade.pnl_points / float(trade.instrument.tick_size)

    # Strategy classification for options is deliberately withheld here.
    # A `Trade` is per-instrument, so an option trade is always one leg —
    # but a leg is not a strategy. Labelling a vertical's short leg
    # NAKED_SHORT_PUT would assert cash-secured risk for a defined-risk
    # position, the exact per-leg falsehood §6 opens by warning about.
    # Classification belongs to §6 grouping (Phase 4); until then options
    # legs stay UNGROUPED rather than carry a wrong risk label.
    if trade.instrument.asset_class == AssetClass.STK:
        trade.strategy_type = StrategyType.LONG_STOCK if trade.side == TradeSide.LONG else StrategyType.SHORT_STOCK
    else:
        trade.strategy_type = StrategyType.UNGROUPED


def _construct_for_instrument(
    db: Session,
    account_id: str,
    instrument: Instrument,
    execs: list[Execution],
    basis_resolver=None,
) -> list[Trade]:
    trades: list[Trade] = []
    acc: _TradeAccumulator | None = None
    multiplier = float(instrument.multiplier)

    for exc in execs:
        qty = float(exc.quantity)
        opening = _is_opening(exc)

        if acc is None:
            if not opening:
                synthetic = basis_resolver.resolve(instrument.id) if basis_resolver else None

                if synthetic is None:
                    # Case (a) pending backfill: no basis anywhere, so no P&L
                    # can be attributed (§5.5) — proceeds are explicitly not
                    # treated as pure profit. The cash flow still lives on the
                    # execution and reaches the equity curve.
                    trade = _new_trade(
                        db, account_id, instrument, TradeSide.LONG if qty > 0 else TradeSide.SHORT, exc.executed_at
                    )
                    trade.status = TradeStatus.ORPHAN_CLOSE
                    trade.excluded_from_win_rate = True
                    trade.excluded_from_duration = True
                    _attach_leg(db, trade, exc.id, abs(qty), LegRole.CLOSE)
                    acc = _TradeAccumulator(trade=trade, lots=deque(), multiplier=multiplier)
                    acc.close_notional += abs(qty) * float(exc.price)
                    acc.close_qty_abs += abs(qty)
                    acc.execution_ids.add(exc.id)
                    _finalize_trade(acc, exc.executed_at, TradeStatus.ORPHAN_CLOSE)
                    trades.append(trade)
                    acc = None
                    continue

                # Case (b): reconstruct the opening leg from broker records.
                # The synthetic leg carries no execution_id and is flagged so
                # it can never be mistaken for a real fill in the grid (§4.5).
                opening_qty = -qty  # the position that must have existed to be closed
                side = TradeSide.LONG if opening_qty > 0 else TradeSide.SHORT
                trade = _new_trade(db, account_id, instrument, side, exc.executed_at)
                trade.origin = synthetic.origin
                # An open date inherited from elsewhere makes hold time
                # meaningless, under either basis policy (§5.5).
                trade.excluded_from_duration = True
                acc = _TradeAccumulator(trade=trade, lots=deque(), multiplier=multiplier)
                acc.lots.append(
                    _Lot(
                        remaining_qty=opening_qty,
                        unit_price=synthetic.unit_price,
                        unit_commission=0.0,
                        open_execution_id=None,
                    )
                )
                _attach_leg(
                    db, trade, None, abs(opening_qty), LegRole.OPEN,
                    synthetic=True, synthetic_source=synthetic.source,
                )
                acc.open_notional += abs(opening_qty) * synthetic.unit_price
                acc.open_qty_abs += abs(opening_qty)
                # Deliberately no `continue`: the synthetic leg has opened the
                # position, so this execution still has to be matched as a
                # close by the FIFO block below.
            else:
                side = TradeSide.LONG if qty > 0 else TradeSide.SHORT
                trade = _new_trade(db, account_id, instrument, side, exc.executed_at)
                acc = _TradeAccumulator(trade=trade, lots=deque(), multiplier=multiplier)
                unit_commission = float(exc.commission) / abs(qty)
                acc.lots.append(
                    _Lot(
                        remaining_qty=qty,
                        unit_price=float(exc.price),
                        unit_commission=unit_commission,
                        open_execution_id=exc.id,
                    )
                )
                _attach_leg(db, trade, exc.id, abs(qty), LegRole.OPEN)
                acc.open_notional += abs(qty) * float(exc.price)
                acc.open_qty_abs += abs(qty)
                acc.execution_ids.add(exc.id)
                continue

        running_qty = sum(lot.remaining_qty for lot in acc.lots)
        same_direction = (running_qty >= 0) == (qty >= 0) if running_qty != 0 else opening

        if same_direction and opening:
            unit_commission = float(exc.commission) / abs(qty)
            acc.lots.append(_Lot(remaining_qty=qty, unit_price=float(exc.price), unit_commission=unit_commission, open_execution_id=exc.id))
            _attach_leg(db, acc.trade, exc.id, abs(qty), LegRole.OPEN)
            acc.open_notional += abs(qty) * float(exc.price)
            acc.open_qty_abs += abs(qty)
            acc.execution_ids.add(exc.id)
            continue

        # Closing / reducing — FIFO match against open lots.
        remaining = abs(qty)
        total_exec_qty = abs(qty)
        exec_proceeds_per_unit = float(exc.proceeds) / total_exec_qty
        exec_commission_per_unit = float(exc.commission) / total_exec_qty

        while remaining > 1e-9 and acc.lots:
            lot = acc.lots[0]
            matched = min(remaining, abs(lot.remaining_qty))
            _attach_leg(db, acc.trade, exc.id, matched, LegRole.CLOSE)
            acc.realize(lot, matched, exec_proceeds_per_unit, exec_commission_per_unit)
            acc.close_notional += matched * float(exc.price)
            acc.close_qty_abs += matched
            acc.execution_ids.add(exc.id)

            lot.remaining_qty -= matched * (1 if lot.remaining_qty > 0 else -1)
            remaining -= matched
            if abs(lot.remaining_qty) < 1e-9:
                acc.lots.popleft()

        if remaining > 1e-9:
            # Position flipped through zero: close current trade, open a new one
            # with the residual, splitting this execution's proceeds/commission.
            _finalize_trade(acc, exc.executed_at, TradeStatus.CLOSED)
            trades.append(acc.trade)

            residual_qty = remaining * (1 if qty > 0 else -1)
            new_side = TradeSide.LONG if residual_qty > 0 else TradeSide.SHORT
            new_trade = _new_trade(db, account_id, instrument, new_side, exc.executed_at)
            new_acc = _TradeAccumulator(trade=new_trade, lots=deque(), multiplier=multiplier)
            new_acc.lots.append(
                _Lot(
                    remaining_qty=residual_qty,
                    unit_price=float(exc.price),
                    unit_commission=exec_commission_per_unit,
                    open_execution_id=exc.id,
                )
            )
            _attach_leg(db, new_trade, exc.id, remaining, LegRole.OPEN)
            new_acc.open_notional += remaining * float(exc.price)
            new_acc.open_qty_abs += remaining
            new_acc.execution_ids.add(exc.id)
            acc = new_acc
            continue

        new_running = sum(lot.remaining_qty for lot in acc.lots)
        if abs(new_running) < 1e-9:
            _finalize_trade(acc, exc.executed_at, TradeStatus.CLOSED)
            trades.append(acc.trade)
            acc = None

    if acc is not None:
        _finalize_trade(acc, None, TradeStatus.OPEN)
        trades.append(acc.trade)

    return trades
