import datetime as dt

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Action


class Execution(Base):
    """A single broker fill (§4.3). Raw and immutable once imported — all
    derived data (trades, stats) is recomputed from this table, never the
    reverse (§3.2 auditability)."""

    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("account_id", "broker_trade_id", name="uq_execution_account_broker_trade_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_file_id: Mapped[int] = mapped_column(ForeignKey("import_files.id"))
    source_line: Mapped[int] = mapped_column(Integer)

    account_id: Mapped[str] = mapped_column(String(32))
    # The canonical dedupe key (§3.2). Resolved per source: Flex prefers
    # transactionID; the `.tlg` has only the order ID, which is how a `.tlg`
    # row and a Flex row for the same fill are matched to each other.
    broker_trade_id: Mapped[str] = mapped_column(String(64))
    ib_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ib_exec_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # §6.1 — legs of one combo order share this; ibOrderID does not.
    brokerage_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    action: Mapped[Action] = mapped_column(Enum(Action, native_enum=False))
    flags: Mapped[list] = mapped_column(JSON, default=list)

    executed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    trading_day: Mapped[dt.date] = mapped_column(Date, index=True)

    quantity: Mapped[float] = mapped_column(Numeric(18, 6))
    multiplier: Mapped[float] = mapped_column(Numeric(18, 6))
    price: Mapped[float] = mapped_column(Numeric(18, 6))

    # .tlg convention: signed cost — buy positive, sell negative (§2.2 field 13).
    proceeds: Mapped[float] = mapped_column(Numeric(18, 6))
    commission: Mapped[float] = mapped_column(Numeric(18, 6))
    fx_rate: Mapped[float] = mapped_column(Numeric(18, 6), default=1)

    # cash_flow = -proceeds + commission (commission already negative).
    cash_flow: Mapped[float] = mapped_column(Numeric(18, 6))

    exchange: Mapped[str] = mapped_column(String(32))
    #: §5.4 — an assignment produces two records, the option closing and the
    #: resulting underlying position. The link is written in both directions
    #: so a chain can be walked from either end.
    related_execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("executions.id"), nullable=True
    )
    out_of_window_warning: Mapped[bool] = mapped_column(default=False)

    #: §11.3 — `MANUAL` marks a hand-entered fill for something that appears
    #: in no broker export. The importer never deletes or overwrites one,
    #: and the executions grid shows it distinctly: a reconstructed row must
    #: never be mistaken for a broker record (the same rule §4.5 applies to
    #: synthetic legs).
    source: Mapped[str] = mapped_column(String(16), default="BROKER", index=True)

    #: §11.3 — cumulative split adjustment for this row.
    #:
    #: The raw broker figures above stay exactly as imported; §3.2's
    #: auditability rests on that. A split is applied here as a *derivation*
    #: factor instead, recomputed from `corporate_actions` on every import,
    #: so it is reversible, visible, and cannot drift from the actions that
    #: produced it. `quantity × factor` and `price ÷ factor` leave proceeds
    #: and commission untouched, which is exactly what a split does.
    split_factor: Mapped[float] = mapped_column(Numeric(18, 9), default=1)

    instrument = relationship("Instrument")

    def effective_quantity(self) -> float:
        return float(self.quantity) * float(self.split_factor or 1)

    def effective_price(self) -> float:
        factor = float(self.split_factor or 1)
        return float(self.price) / factor if factor else float(self.price)

    def effective_multiplier(self) -> float:
        return float(self.multiplier)
