import datetime as dt
import enum

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OptionEventType(str, enum.Enum):
    """§5.4 — what the broker says happened to an option.

    Kept distinct from the `notes` flags (`A`/`Ex`/`Ep`) on an execution: the
    flag says an event occurred, this says which one, and only the
    OptionEAE section is authoritative about that.
    """

    ASSIGNMENT = "ASSIGNMENT"
    EXERCISE = "EXERCISE"
    EXPIRATION = "EXPIRATION"
    UNKNOWN = "UNKNOWN"


#: The two that create a position in the underlying and therefore need a
#: chain. An expiration terminates cleanly and creates nothing.
POSITION_CREATING_TYPES = frozenset({OptionEventType.ASSIGNMENT, OptionEventType.EXERCISE})


class OptionEvent(Base):
    """§5.4 — one row of "Options, Exercises, Assignments and Expirations".

    **Why this table exists rather than a flag on the execution.** An
    assignment appears in the *next* statement, so the option's closing row
    and the news that it was assigned can arrive a day apart, and the
    resulting underlying position can arrive with either. Holding the
    broker's own account of the event separately means the link can be made
    whenever the second half turns up, rather than only if both land in one
    import.

    `acknowledged` backs the persistent dashboard banner. §5.4 calls this
    "the single highest-value notification in the app"; an unexpected
    futures position is exactly the thing to be told about rather than
    discover, so it stays up until a human dismisses it.
    """

    __tablename__ = "option_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_file_id: Mapped[int] = mapped_column(ForeignKey("import_files.id"))
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)

    event_type: Mapped[OptionEventType] = mapped_column(
        Enum(OptionEventType, native_enum=False), index=True
    )
    #: Preserved verbatim when IB's `transactionType` is not one we map, so
    #: an unrecognized event is visible rather than coerced into a wrong one.
    raw_type: Mapped[str] = mapped_column(String(64), default="")
    occurred_on: Mapped[dt.date] = mapped_column(Date, index=True)

    quantity: Mapped[float] = mapped_column(Numeric(18, 6))
    trade_price: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    proceeds: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    commission: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    cost_basis: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    #: IB's own realized figure under convention (b). Stored for the
    #: chain-level reconciliation in §5.4, never used as this app's P&L.
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)

    broker_trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    underlying_conid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    underlying_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: The option's own closing execution, and the underlying execution the
    #: event produced. Either can be absent: a late statement, or an
    #: expiration, which produces no underlying at all.
    option_execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("executions.id"), nullable=True
    )
    underlying_execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("executions.id"), nullable=True
    )
    trade_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("trade_groups.id"), nullable=True
    )

    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    link_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    instrument = relationship("Instrument")

    @property
    def creates_position(self) -> bool:
        return self.event_type in POSITION_CREATING_TYPES

    @property
    def needs_attention(self) -> bool:
        """Assignments and exercises are surprises; expirations are not.

        An expiry was on the calendar and Expiry Watch (§9.5) already
        forecast it. Banner-ing all ten of the fixture's expirations would
        train the user to dismiss the banner, which is exactly how the one
        assignment that matters gets missed.
        """
        return self.creates_position and not self.acknowledged


class CorporateAction(Base):
    """§11.3 — splits and symbol changes.

    Only splits are *applied*; everything else is recorded and surfaced. A
    misapplied spin-off silently corrupts historical basis in a way that
    looks like a P&L error months later, so the unhandled types stay visible
    instead of being half-processed.
    """

    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_file_id: Mapped[int] = mapped_column(ForeignKey("import_files.id"))
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), nullable=True, index=True
    )

    occurred_on: Mapped[dt.date] = mapped_column(Date, index=True)
    action_type: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    proceeds: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    value: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    broker_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Parsed split ratio, when this is a split and the ratio could be read.
    #: `2.0` means one share became two.
    split_ratio: Mapped[float | None] = mapped_column(Numeric(18, 9), nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="FLEX")

    instrument = relationship("Instrument")
