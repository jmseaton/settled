import datetime as dt

from sqlalchemy import Date, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PriceBar(Base):
    """§4.10 — daily closes for the benchmark and, later, mark-to-market.

    **The only table any benchmark calculation reads from.** No code calls a
    price API directly: adapters write here, calculations read here. That is
    what makes a provider swap a one-adapter change (test 53) and what turns
    a provider outage into stale cached data rather than a blank chart
    (test 54).

    `adj_close` must be dividend-adjusted, not merely split-adjusted (§7.5).
    Benchmarking against a price-only series overstates relative performance
    by roughly the dividend yield every year, which compounds into a
    materially flattering answer over three.
    """

    __tablename__ = "price_bars"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    close: Mapped[float] = mapped_column(Numeric(18, 6))
    adj_close: Mapped[float] = mapped_column(Numeric(18, 6))
    #: Which provider supplied this row, so a swap is auditable rather than
    #: silently mixed across a series.
    source: Mapped[str] = mapped_column(String(32))
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
