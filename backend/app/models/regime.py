import datetime as dt

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RegimeMarker(Base):
    """§6.6 — a dated annotation, drawn as a vertical line on time series.

    **Why the app does not detect these.** §6.6 is explicit: automatic
    regime detection "is over-engineering for one user". What the app owes
    is to make a change *visible and separable*, and the cheapest form of
    that is letting the person who made the change write down when they
    made it.

    The fixture shows exactly the case: MES positions were vertical spreads
    through 2026-07-05 and naked short puts from 2026-07-12. A single "MES
    win rate" spans two materially different risk profiles, and a line on
    the chart at the switch turns "why did this change?" into a one-glance
    answer instead of an investigation.

    Deliberately not tied to a symbol or a strategy type. A regime change is
    a change in how the account is being run; scoping it to one instrument
    would miss the case where several move together, which is the usual
    case.
    """

    __tablename__ = "regime_markers"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    occurred_on: Mapped[dt.date] = mapped_column(Date, index=True)
    label: Mapped[str] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Optional hex colour so several concurrent regimes stay legible.
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    #: Ordering hint for the rare case of two markers on one day.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "occurred_on": self.occurred_on.isoformat(),
            "label": self.label,
            "note": self.note,
            "color": self.color,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
