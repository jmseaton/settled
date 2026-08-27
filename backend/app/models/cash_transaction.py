import datetime as dt
import enum

from sqlalchemy import Date, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CashTransactionType(str, enum.Enum):
    """§4.7.

    `TRANSFER_IN`/`TRANSFER_OUT` carry a transferred position's market value
    on the transfer date. They are *external flows* for TWR purposes (§7.3),
    never trading P&L — the position arriving is money entering the account
    exactly as a wire would be, and counting it as performance would credit
    this account with a gain earned somewhere else.
    """

    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    DIVIDEND = "DIVIDEND"
    INTEREST = "INTEREST"
    FEE = "FEE"
    ADJUSTMENT = "ADJUSTMENT"


#: §7.3 — the flows TWR must strip out. Dividends, interest and fees change
#: the account's value without any money crossing its boundary, so they are
#: returns, not flows; treating them as flows would erase them from
#: performance entirely.
EXTERNAL_FLOW_TYPES = frozenset(
    {
        CashTransactionType.DEPOSIT,
        CashTransactionType.WITHDRAWAL,
        CashTransactionType.TRANSFER_IN,
        CashTransactionType.TRANSFER_OUT,
    }
)

#: Cash effects that are neither external flows nor closed-trade P&L. They
#: back the "+ dividends" equity-curve overlay (§7.4).
INCOME_TYPES = frozenset(
    {
        CashTransactionType.DIVIDEND,
        CashTransactionType.INTEREST,
        CashTransactionType.FEE,
        CashTransactionType.ADJUSTMENT,
    }
)


class CashSource(str, enum.Enum):
    """§7.1 — `MANUAL` rows survive every resync.

    Without this column a hand-entered correction would be silently
    reinstated to IB's version on the next nightly sync, which is the one
    thing a correction must never do.
    """

    FLEX = "FLEX"
    MANUAL = "MANUAL"


class CashTransaction(Base):
    """§4.7 — deposits, withdrawals, dividends, interest, fees, transfers.

    Signed convention: money into the account is positive, money out
    negative. IB already signs its `amount` this way, so the sign is carried
    through rather than re-derived from the type — a "Deposits/Withdrawals"
    row is a deposit or a withdrawal depending on nothing but that sign.
    """

    __tablename__ = "cash_transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "broker_transaction_id", name="uq_cash_broker_txn"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    occurred_on: Mapped[dt.date] = mapped_column(Date, index=True)
    type: Mapped[CashTransactionType] = mapped_column(
        Enum(CashTransactionType, native_enum=False)
    )
    amount: Mapped[float] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    source: Mapped[CashSource] = mapped_column(
        Enum(CashSource, native_enum=False), default=CashSource.FLEX
    )
    related_instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), nullable=True
    )
    broker_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    instrument = relationship("Instrument")

    @property
    def is_external_flow(self) -> bool:
        return self.type in EXTERNAL_FLOW_TYPES


#: §7.1 — "Map IB's `type` values onto the `cash_transactions.type` enum and
#: flag unrecognized types as `ADJUSTMENT` with the raw string preserved in
#: `note`." Keys are lowercased and whitespace-collapsed before lookup so a
#: change in IB's capitalisation does not silently reroute a deposit into
#: the adjustment bucket.
IB_TYPE_MAP: dict[str, CashTransactionType | None] = {
    # None means "decide from the sign" — IB reports both directions under
    # one label.
    "deposits/withdrawals": None,
    "deposits & withdrawals": None,
    "deposits": CashTransactionType.DEPOSIT,
    "withdrawals": CashTransactionType.WITHDRAWAL,
    "dividends": CashTransactionType.DIVIDEND,
    "payment in lieu of dividends": CashTransactionType.DIVIDEND,
    "broker interest paid": CashTransactionType.INTEREST,
    "broker interest received": CashTransactionType.INTEREST,
    "broker interest paid and received": CashTransactionType.INTEREST,
    "bond interest paid": CashTransactionType.INTEREST,
    "bond interest received": CashTransactionType.INTEREST,
    "other fees": CashTransactionType.FEE,
    "broker fees": CashTransactionType.FEE,
    "advisor fees": CashTransactionType.FEE,
    "client fees": CashTransactionType.FEE,
    "commission adjustments": CashTransactionType.FEE,
    "withholding tax": CashTransactionType.FEE,
}


def classify_ib_cash_type(raw: str, amount: float) -> tuple[CashTransactionType, bool]:
    """Map an IB `CashTransaction/@type` onto the enum.

    Returns the type and whether the raw string was recognized. An
    unrecognized type is not an error — IB adds categories — but it must be
    visible rather than absorbed, so it lands in `ADJUSTMENT` and the caller
    preserves the raw string in `note`.
    """
    key = " ".join((raw or "").split()).lower()
    if key not in IB_TYPE_MAP:
        return CashTransactionType.ADJUSTMENT, False
    mapped = IB_TYPE_MAP[key]
    if mapped is None:
        return (
            CashTransactionType.DEPOSIT if amount >= 0 else CashTransactionType.WITHDRAWAL
        ), True
    return mapped, True
