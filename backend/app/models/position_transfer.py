import datetime as dt

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PositionTransfer(Base):
    """§4.7b — ACATS/internal position transfers.

    Kept separate from cash transactions because the synthetic opening leg in
    §5.5 needs per-unit detail, and because `original_cost_basis` is often
    absent or wrong on arrival and gets corrected by hand later.

    Do **not** read `transfer_price`: confirmed zero in live data. Derive the
    per-unit basis from `original_cost_basis / quantity` and the transfer
    mark from `position_amount / quantity` (§5.5 caveat).
    """

    __tablename__ = "position_transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_file_id: Mapped[int] = mapped_column(ForeignKey("import_files.id"))
    account_id: Mapped[str] = mapped_column(String(32))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)

    transferred_on: Mapped[dt.date] = mapped_column(Date, index=True)
    direction: Mapped[str] = mapped_column(String(8))
    transfer_type: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float] = mapped_column(Numeric(18, 6))
    transfer_price: Mapped[float] = mapped_column(Numeric(18, 6))
    position_amount: Mapped[float] = mapped_column(Numeric(18, 6))
    original_cost_basis: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    broker_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    basis_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    instrument = relationship("Instrument")

    def mark_price(self) -> float | None:
        qty = abs(float(self.quantity))
        return float(self.position_amount) / qty if qty else None

    def original_cost_price(self) -> float | None:
        qty = abs(float(self.quantity))
        if not qty or self.original_cost_basis is None:
            return None
        return float(self.original_cost_basis) / qty
