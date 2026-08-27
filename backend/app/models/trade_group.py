import datetime as dt

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import StrategyType


class TradeGroup(Base):
    """§4.6 — a multi-leg strategy treated as one analytical unit.

    The most important departure from a naive per-leg journal. `ACME
    310813P00172000` shows -61.37 and `ACME 310813P00180600` shows +103.75;
    neither number means anything alone. The spread made +42.37.

    Derived and rebuildable, like `trades` — except `user_confirmed`, which
    records a manual grouping decision a rebuild must not clobber (§6.4).
    """

    __tablename__ = "trade_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32))
    strategy_type: Mapped[StrategyType] = mapped_column(Enum(StrategyType, native_enum=False))
    book: Mapped[str | None] = mapped_column(String(32), nullable=True)
    underlying_root: Mapped[str | None] = mapped_column(String(32), nullable=True)

    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    net_debit_credit: Mapped[float] = mapped_column(Numeric(18, 6), default=0)

    # §6.3 — null means *unbounded*, never zero, and never dropped from a
    # denominator. A silently excluded position inflates every average it
    # should have dragged down.
    max_risk: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    margin_requirement: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    post_assignment_capital: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)

    gross_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    commissions: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    net_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    return_on_risk: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    return_on_margin: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    #: §5.4 — the second return figure an assigned position needs. A
    #: $2,000-wide put spread's defined risk converts into a stock position
    #: tying up far more capital, so return-on-risk against the frozen
    #: at-open figure and against the capital actually committed are both
    #: reported, labelled, and never averaged together.
    return_on_post_assignment_capital: Mapped[float | None] = mapped_column(
        Numeric(18, 6), nullable=True
    )

    # §5.4 — "Duration stops being one number." A chain's option leg and the
    # underlying it turned into are held for different reasons over
    # different windows; one combined figure describes neither. Both are
    # reported and no total is emitted (test 48).
    option_duration_seconds: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    underlying_duration_seconds: Mapped[float | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )

    leg_count: Mapped[int] = mapped_column(Integer, default=0)
    dte_at_open: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_group_id: Mapped[int | None] = mapped_column(ForeignKey("trade_groups.id"), nullable=True)
    auto_detected: Mapped[bool] = mapped_column(Boolean, default=True)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # How §6.1 arrived at this grouping, so a timestamp-inferred combo is
    # never mistaken for one the broker confirmed.
    detection_source: Mapped[str] = mapped_column(String(24), default="SINGLE")

    legs = relationship("Trade", back_populates="group")
