import datetime as dt

from sqlalchemy import Date, Enum, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import AssetClass, ExerciseStyle, Right


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("broker_symbol", "asset_class", name="uq_instrument_symbol_class"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # §3.9 — IBKR's stable contract identifier. Nullable because the `.tlg`
    # format does not carry it; populated whenever a Flex import supplies it.
    conid: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass, native_enum=False))
    root_symbol: Mapped[str] = mapped_column(String(32))
    broker_symbol: Mapped[str] = mapped_column(String(64))
    broker_local_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    multiplier: Mapped[float] = mapped_column(Numeric(18, 6))

    # §4.2 / Appendix D — tick reference. Two-tier for options (D.3); plain for futures (D.1).
    tick_size: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    tick_value: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    tick_size_high: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    tick_value_high: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    tick_threshold: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)

    expiry: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    right: Mapped[Right | None] = mapped_column(Enum(Right, native_enum=False), nullable=True)
    exercise_style: Mapped[ExerciseStyle] = mapped_column(
        Enum(ExerciseStyle, native_enum=False), default=ExerciseStyle.UNKNOWN
    )

    # §4.2 — PHYSICAL | CASH | INTO_FUTURE | null. Drives §5.4: a
    # cash-settled index option settles to cash and creates no position, so
    # linking one to a phantom holding is the specific error this prevents
    # (test 45).
    settlement: Mapped[str | None] = mapped_column(String(16), nullable=True)

    underlying_root: Mapped[str | None] = mapped_column(String(32), nullable=True)
    underlying_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    underlying_conid: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    parse_status: Mapped[str] = mapped_column(String(16), default="ok")
