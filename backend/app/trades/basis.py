"""§5.5 — supplying an opening basis for a close that has none.

Two distinct causes needing different treatment:

  (a) **Window truncation** — the position was opened before the file's date
      range. A genuine data gap; fixed by importing the earlier period.
  (b) **Transferred in** (ACATS/ATON or internal) — no opening execution
      exists in this account, ever. More history will not help.

In both cases the close is never dropped and proceeds are never treated as
pure profit.

Resolution order, best source first:

  1. **Transfers section** — identifies case (b) outright and carries both
     the delivering broker's cost and the transfer-date market value.
  2. **CLOSED_LOT** — the §5.5 shortcut. A lot carries the opening date and
     cost basis *even when the opening execution falls outside the window*,
     so for anything closed in-window the orphan problem does not arise.
  3. **Nothing** — remain an `ORPHAN_CLOSE` with no P&L attributed, and say
     so in the import report.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.closed_lot import ClosedLot
from app.models.enums import Origin
from app.models.position_transfer import PositionTransfer
from app.models.settings_row import TransferBasisPolicy


@dataclass
class SyntheticBasis:
    """An opening leg reconstructed from broker records rather than a fill."""

    unit_price: float
    origin: Origin
    source: str  # TRANSFER | CLOSED_LOT
    note: str


class BasisResolver:
    """Looks up a synthetic opening basis per instrument. Built once per
    rebuild so trade construction stays a pure pass over executions."""

    def __init__(self, db: Session, account_id: str, policy: TransferBasisPolicy):
        self.policy = policy
        self._transfers: dict[int, PositionTransfer] = {}
        for transfer in (
            db.query(PositionTransfer)
            .filter(PositionTransfer.account_id == account_id, PositionTransfer.direction == "IN")
            .order_by(PositionTransfer.transferred_on)
            .all()
        ):
            self._transfers.setdefault(transfer.instrument_id, transfer)

        self._lots: dict[int, ClosedLot] = {}
        for lot in (
            db.query(ClosedLot)
            .filter(ClosedLot.account_id == account_id, ClosedLot.opening_transaction_id.is_(None))
            .all()
        ):
            self._lots.setdefault(lot.instrument_id, lot)

    def resolve(self, instrument_id: int) -> SyntheticBasis | None:
        transfer = self._transfers.get(instrument_id)
        if transfer is not None:
            mark = transfer.mark_price()
            original = transfer.original_cost_price()

            if self.policy == TransferBasisPolicy.ORIGINAL_COST and original is not None:
                return SyntheticBasis(
                    unit_price=original,
                    origin=Origin.TRANSFERRED,
                    source="TRANSFER",
                    note=(
                        f"Opening leg synthesized from the delivering broker's carried-over cost "
                        f"({original:.6f}/unit) under ORIGINAL_COST. This measures the total economic "
                        f"result of the holding, including gains earned before it arrived."
                    ),
                )
            if mark is not None:
                return SyntheticBasis(
                    unit_price=mark,
                    origin=Origin.TRANSFERRED,
                    source="TRANSFER",
                    note=(
                        f"Opening leg synthesized from the transfer-date market value "
                        f"({mark:.6f}/unit) under TRANSFER_MARK. Measures decisions made in this "
                        f"account only; P&L will differ from IBKR's, which uses acquisition cost."
                    ),
                )

        lot = self._lots.get(instrument_id)
        if lot is not None and float(lot.quantity):
            unit = abs(float(lot.cost)) / abs(float(lot.quantity))
            return SyntheticBasis(
                unit_price=unit,
                # Not TRANSFERRED, which this used to claim. The distinction
                # is not cosmetic: it decided the §3.4 check's carve-out, so
                # a truncated trade was skipped as a transfer and explained
                # with a note about acquisition cost that had nothing to do
                # with the cause. It is also the wrong remedy to imply —
                # a transfer will never gain an opening execution, and this
                # one gains it the moment an earlier window is imported.
                origin=Origin.WINDOW_TRUNCATED,
                source="CLOSED_LOT",
                note=(
                    f"Opening leg synthesized from IB's closed-lot basis ({unit:.6f}/unit, opened "
                    f"{lot.open_date}). The opening execution falls outside the imported window."
                ),
            )

        return None
