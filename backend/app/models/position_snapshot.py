import datetime as dt

from sqlalchemy import Date, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import AssetClass


class BrokerPositionSnapshot(Base):
    """End-of-period `STK_LOT` / `OPT_LOT` records (§2.5).

    Originally import reconciliation only (§3.4). Since §12, also the source
    of **marks**: Flex publishes `markPrice` and `fifoPnlUnrealized` on every
    open position, so unrealized P&L and a true account value need no
    external vendor at all — "the cheap, high-value first step".

    Still never a source of *trade* data. A snapshot says what is held at one
    instant; trades are derived from executions and nothing else.
    """

    __tablename__ = "broker_position_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_file_id: Mapped[int] = mapped_column(ForeignKey("import_files.id"))
    account_id: Mapped[str] = mapped_column(String(32))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass, native_enum=False))

    quantity: Mapped[float] = mapped_column(Numeric(18, 6))
    multiplier: Mapped[float] = mapped_column(Numeric(18, 6))
    cost_basis_price: Mapped[float] = mapped_column(Numeric(18, 6))
    cost_basis_money: Mapped[float] = mapped_column(Numeric(18, 6))
    fx_rate: Mapped[float] = mapped_column(Numeric(18, 6), default=1)

    # §12 — the mark and IB's own unrealized figure for it. Both nullable:
    # a `.tlg` carries neither, and a snapshot without a mark is still a
    # valid reconciliation record.
    mark_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    close_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    #: The date this snapshot describes — the statement's `period_end`, not
    #: the day it was fetched. A curve built from snapshots has to place each
    #: one on the day it was true, or every mark lands on the import date.
    snapshot_on: Mapped[dt.date | None] = mapped_column(Date, index=True, nullable=True)

    instrument = relationship("Instrument")

    def market_value(self) -> float | None:
        """Signed market value at the mark. Null when there is no mark."""
        if self.mark_price is None:
            return None
        return float(self.quantity) * float(self.multiplier) * float(self.mark_price)
