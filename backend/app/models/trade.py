import datetime as dt

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import LegRole, Origin, StrategyType, TradeSide, TradeStatus


class Trade(Base):
    """A round trip in one instrument (§4.4, §5.1) — derived and fully
    rebuildable from `executions`."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))

    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide, native_enum=False))
    status: Mapped[TradeStatus] = mapped_column(Enum(TradeStatus, native_enum=False))
    origin: Mapped[Origin] = mapped_column(Enum(Origin, native_enum=False), default=Origin.TRADED)
    book: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_type: Mapped[StrategyType | None] = mapped_column(
        Enum(StrategyType, native_enum=False), nullable=True
    )

    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)

    open_qty: Mapped[float] = mapped_column(Numeric(18, 6))
    total_buy_qty: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    total_sell_qty: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    remaining_qty: Mapped[float] = mapped_column(Numeric(18, 6), default=0)

    avg_open_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    avg_close_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)

    gross_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    commissions: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    net_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)

    cost_basis: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    return_pct: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    pnl_per_qty: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    pnl_points: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    pnl_ticks: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)

    excluded_from_win_rate: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded_from_duration: Mapped[bool] = mapped_column(Boolean, default=False)

    #: §0.3 — opened before the epoch, closed after it. Included with a
    #: rebased opening basis, but excluded from duration statistics: a hold
    #: time that began before you were measuring is not a hold time you can
    #: learn from.
    straddles_epoch: Mapped[bool] = mapped_column(Boolean, default=False)

    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    trade_group_id: Mapped[int | None] = mapped_column(ForeignKey("trade_groups.id"), nullable=True)

    instrument = relationship("Instrument")
    legs = relationship("TradeExecution", back_populates="trade", order_by="TradeExecution.id")
    group = relationship("TradeGroup", back_populates="legs")


class TradeExecution(Base):
    """Join table (§4.5) — a single execution can be split across trades
    under FIFO, so matched quantity lives here, not on the execution."""

    __tablename__ = "trade_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"))
    execution_id: Mapped[int | None] = mapped_column(ForeignKey("executions.id"), nullable=True)
    matched_qty: Mapped[float] = mapped_column(Numeric(18, 6))
    leg_role: Mapped[LegRole] = mapped_column(Enum(LegRole, native_enum=False))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    synthetic_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    trade = relationship("Trade", back_populates="legs")
    execution = relationship("Execution")
