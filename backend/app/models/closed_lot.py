import datetime as dt

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ClosedLot(Base):
    """IB's own FIFO lot matching, from `levelOfDetail=CLOSED_LOT` (§3.4).

    Stored verbatim and never used to *build* trades — only to check them.
    Reconciling against the broker's own books validates the entire matching
    algorithm in §5.2, which the position snapshot alone cannot do.

    `cost` includes the opening commission and `fifo_pnl_realized` is net of
    the closing commission, so `fifo_pnl_realized` is directly comparable to
    `Trade.net_pnl` (verified against the fixture).
    """

    __tablename__ = "closed_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_file_id: Mapped[int] = mapped_column(ForeignKey("import_files.id"))
    account_id: Mapped[str] = mapped_column(String(32))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))

    closing_broker_trade_id: Mapped[str] = mapped_column(String(64), index=True)
    # Empty when the opening execution falls outside the query window — the
    # §5.5 shortcut: the lot still carries the basis the opening would have.
    opening_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    quantity: Mapped[float] = mapped_column(Numeric(18, 6))
    trade_price: Mapped[float] = mapped_column(Numeric(18, 6))
    cost: Mapped[float] = mapped_column(Numeric(18, 6))
    fifo_pnl_realized: Mapped[float] = mapped_column(Numeric(18, 6))
    open_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    instrument = relationship("Instrument")
