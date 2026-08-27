"""§3.4 — reconciliation: computed open positions vs. the broker's own
`STK_LOT` / `OPT_LOT` snapshot.

Runs an independent, lightweight FIFO pass (not reusing `Trade` rows) so
reconciliation stays a check *against* trade construction rather than a
restatement of it — a bug shared by both would otherwise go undetected.
Cost basis follows §2.5's convention exactly: commission is allocated
across the *original opening execution's* quantity and does not shrink as
the lot is later reduced.
"""

from collections import deque
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.enums import Action, Origin
from app.models.execution import Execution
from app.models.position_snapshot import BrokerPositionSnapshot
from app.models.trade import Trade

QUANTITY_TOLERANCE = 1e-6
COST_BASIS_TOLERANCE = 0.01

_OPENING_ACTIONS = {Action.BUYTOOPEN, Action.SELLTOOPEN}


@dataclass
class _Lot:
    remaining_qty: float
    unit_price: float
    # Opening commission per unit of *notional* (qty x multiplier), negative
    # as charged. Fixed at open and never rescaled as the lot is reduced —
    # IB allocates the opening commission across the opening lot and does not
    # net closing commissions into the remaining basis (§2.5).
    unit_commission: float


def _unit_commission(exc: Execution, qty: float) -> float:
    notional_units = abs(qty) * float(exc.multiplier)
    return float(exc.commission) / notional_units if notional_units else 0.0


def _lot_basis_price(lot: _Lot) -> float:
    """§2.5 — commission-inclusive per-unit basis. Commission is negative as
    charged, so a long lot's basis rises above the fill price and a short
    lot's falls below it."""
    if lot.remaining_qty >= 0:
        return lot.unit_price - lot.unit_commission
    return lot.unit_price + lot.unit_commission


@dataclass
class ComputedPosition:
    instrument_id: int
    quantity: float
    cost_basis_price: float | None
    cost_basis_money: float | None
    orphan_close_qty: float = 0.0


@dataclass
class ReconciliationIssue:
    severity: str  # "error" | "warning"
    instrument_id: int
    message: str


@dataclass
class ReconciliationResult:
    issues: list[ReconciliationIssue]
    computed_positions: dict[int, ComputedPosition]

    @property
    def passed(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


def compute_open_positions(db: Session, account_id: str) -> dict[int, ComputedPosition]:
    executions = (
        db.query(Execution)
        .filter(Execution.account_id == account_id)
        .order_by(Execution.instrument_id, Execution.executed_at, Execution.broker_trade_id)
        .all()
    )

    by_instrument: dict[int, list[Execution]] = {}
    for e in executions:
        by_instrument.setdefault(e.instrument_id, []).append(e)

    positions: dict[int, ComputedPosition] = {}
    for instrument_id, execs in by_instrument.items():
        lots: deque[_Lot] = deque()
        orphan_close_qty = 0.0
        for exc in execs:
            qty = float(exc.quantity)
            opening = exc.action in _OPENING_ACTIONS or ("O" in exc.flags)
            running = sum(lot.remaining_qty for lot in lots)

            if not lots and not opening:
                # A close with nothing open is an orphan (§5.5), not a new
                # position in the opposite direction. Inventing a short here
                # would fabricate a position the account never held and make
                # reconciliation disagree with the broker by construction.
                orphan_close_qty += qty
                continue

            if not lots or (running >= 0) == (qty >= 0):
                if opening or not lots:
                    lots.append(
                        _Lot(
                            remaining_qty=qty,
                            unit_price=float(exc.price),
                            unit_commission=_unit_commission(exc, qty),
                        )
                    )
                    continue

            remaining = abs(qty)
            while remaining > QUANTITY_TOLERANCE and lots:
                lot = lots[0]
                matched = min(remaining, abs(lot.remaining_qty))
                lot.remaining_qty -= matched * (1 if lot.remaining_qty > 0 else -1)
                remaining -= matched
                if abs(lot.remaining_qty) < QUANTITY_TOLERANCE:
                    lots.popleft()

            if remaining > QUANTITY_TOLERANCE:
                residual = remaining * (1 if qty > 0 else -1)
                lots.append(
                    _Lot(
                        remaining_qty=residual,
                        unit_price=float(exc.price),
                        unit_commission=_unit_commission(exc, qty),
                    )
                )

        total_qty = sum(lot.remaining_qty for lot in lots)
        if abs(total_qty) < QUANTITY_TOLERANCE:
            positions[instrument_id] = ComputedPosition(
                instrument_id, 0.0, None, None, orphan_close_qty=orphan_close_qty
            )
            continue

        total_qty_abs = sum(abs(lot.remaining_qty) for lot in lots)
        cost_basis_price = (
            sum(abs(lot.remaining_qty) * _lot_basis_price(lot) for lot in lots) / total_qty_abs
        )
        multiplier = float(execs[0].multiplier)
        cost_basis_money = total_qty_abs * multiplier * cost_basis_price
        positions[instrument_id] = ComputedPosition(
            instrument_id, total_qty, cost_basis_price, cost_basis_money, orphan_close_qty=orphan_close_qty
        )

    return positions


def reconcile(db: Session, account_id: str, import_file_id: int) -> ReconciliationResult:
    computed = compute_open_positions(db, account_id)
    snapshot_rows = (
        db.query(BrokerPositionSnapshot)
        .filter(
            BrokerPositionSnapshot.account_id == account_id,
            BrokerPositionSnapshot.import_file_id == import_file_id,
        )
        .all()
    )

    issues: list[ReconciliationIssue] = []
    snapshot_by_instrument = {row.instrument_id: row for row in snapshot_rows}

    # An orphan that the §5.5 basis resolver has already supplied an opening
    # leg for is no longer an orphan. Reporting it anyway would leave a
    # permanent warning describing a problem that has been solved — and a
    # warning nobody can clear is a warning everyone stops reading.
    resolved_instruments = {
        trade.instrument_id
        for trade in db.query(Trade).filter(
            Trade.account_id == account_id,
            # Either synthetic opening counts as resolved. What matters here
            # is that the basis resolver supplied one, not which of the two
            # §5.5 causes it was.
            Trade.origin.in_([Origin.TRANSFERRED, Origin.WINDOW_TRUNCATED]),
        )
    }

    for instrument_id, snap in snapshot_by_instrument.items():
        comp = computed.get(instrument_id)
        comp_qty = comp.quantity if comp else 0.0
        if abs(comp_qty - float(snap.quantity)) > QUANTITY_TOLERANCE:
            issues.append(
                ReconciliationIssue(
                    "error",
                    instrument_id,
                    f"Quantity mismatch: computed {comp_qty} vs broker LOT {snap.quantity}",
                )
            )
            continue

        if comp and comp.cost_basis_price is not None:
            diff = abs(comp.cost_basis_price - float(snap.cost_basis_price))
            if diff > COST_BASIS_TOLERANCE:
                issues.append(
                    ReconciliationIssue(
                        "warning",
                        instrument_id,
                        f"Cost basis mismatch: computed {comp.cost_basis_price:.6f} vs broker LOT "
                        f"{float(snap.cost_basis_price):.6f} (diff {diff:.6f})",
                    )
                )

    for instrument_id, comp in computed.items():
        if abs(comp.quantity) > QUANTITY_TOLERANCE and instrument_id not in snapshot_by_instrument:
            issues.append(
                ReconciliationIssue(
                    "error",
                    instrument_id,
                    f"Computed open position (qty {comp.quantity}) has no corresponding broker LOT record",
                )
            )
        if abs(comp.orphan_close_qty) > QUANTITY_TOLERANCE and instrument_id not in resolved_instruments:
            issues.append(
                ReconciliationIssue(
                    "warning",
                    instrument_id,
                    f"Orphan close of {comp.orphan_close_qty:g} with no opening execution in the data. "
                    "The position was opened before this file's window or transferred in (§5.5). "
                    "No P&L is attributed; import the earlier period or record a transfer basis.",
                )
            )

    return ReconciliationResult(issues=issues, computed_positions=computed)
