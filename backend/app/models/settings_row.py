import datetime as dt
import enum

from sqlalchemy import JSON, Date, Enum, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import AnalysisMode


class TransferBasisPolicy(str, enum.Enum):
    """§5.5 / §14.3.

    `TRANSFER_MARK` is the default because the question this app exists to
    answer is "how well am I trading *here*". IBKR agrees: it backs the
    pre-transfer gain out of its own performance figures via
    `ChangeInNAV.transferredPnlAdjustments`. Choosing `ORIGINAL_COST` makes
    trade P&L match IB's `fifoPnlRealized` but measures the total economic
    result of the holding, including gains earned at another broker.
    """

    TRANSFER_MARK = "TRANSFER_MARK"
    ORIGINAL_COST = "ORIGINAL_COST"


class TrackingValueSource(str, enum.Enum):
    """§0.3 — where `tracking_start_value` came from.

    Worth recording because a typed NAV and a fetched one carry very
    different confidence, and a wrong starting value produces returns that
    look entirely plausible and are wrong.
    """

    FETCHED = "FETCHED"
    MANUAL = "MANUAL"


class AppSettings(Base):
    """§4.6b — single-row settings table."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    transfer_basis_policy: Mapped[TransferBasisPolicy] = mapped_column(
        Enum(TransferBasisPolicy, native_enum=False), default=TransferBasisPolicy.TRANSFER_MARK
    )
    analysis_mode: Mapped[AnalysisMode] = mapped_column(
        Enum(AnalysisMode, native_enum=False), default=AnalysisMode.STRATEGY
    )
    default_book: Mapped[str] = mapped_column(String(32), default="Options Income")
    benchmark_symbol: Mapped[str] = mapped_column(String(16), default="SPY")

    # §0.3 — the performance epoch. Measured from when the current strategy
    # started, not from account funding: early experimentation would
    # otherwise contaminate every statistic, and there is no way to un-mix
    # it later. Null means "not set" — the app then falls back to the first
    # day of activity and says so, rather than inventing an anchor.
    tracking_start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    tracking_start_value: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    tracking_start_value_source: Mapped[TrackingValueSource] = mapped_column(
        Enum(TrackingValueSource, native_enum=False), default=TrackingValueSource.MANUAL
    )
    #: IBKR's own `ChangeInNAV.startingValue` for the epoch date, refreshed
    #: on each sync from a date-overridden Flex request. Kept beside the
    #: stored value rather than replacing it so divergence is always visible
    #: (§0.3) instead of being resolved silently in one direction.
    tracking_start_value_ibkr: Mapped[float | None] = mapped_column(
        Numeric(18, 6), nullable=True
    )

    # §9.3 / §14.6.1 — the dashboard the user actually keeps. Null means
    # "still on the default set", which is a fact worth being able to read:
    # it says the §9.3 guess has not yet been tested against use.
    dashboard_layout: Mapped[list | None] = mapped_column(JSON, nullable=True)


def get_settings_row(db) -> AppSettings:
    row = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
    if row is None:
        row = AppSettings(id=1)
        db.add(row)
        db.flush()
    return row
