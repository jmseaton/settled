"""§4.10 / §7.5 — `price_bars` is the sole read path.

Adapters write here; every benchmark calculation reads from here and calls
no price API itself. That separation is what makes test 53 (swapping the
adapter changes no calculation code) hold by construction rather than by
discipline, and what makes test 54 (a fetch failure leaves the last cached
bar in place and does not fail the sync) the natural behaviour instead of a
special case.
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.price_bar import PriceBar
from app.prices.base import DailyBar, PriceFetchError, PriceSource

#: Refetch this far back on every incremental run. Providers restate recent
#: bars — a split or a dividend applied a day late rewrites the adjusted
#: series behind you — and an append-only fetch would never see it.
RESTATEMENT_OVERLAP_DAYS = 7


@dataclass
class PriceRefreshResult:
    symbol: str
    source: str
    ok: bool
    bars_written: int = 0
    fetched_from: dt.date | None = None
    fetched_to: dt.date | None = None
    error: str | None = None
    #: The newest bar in the cache after this run, successful or not. What
    #: the UI needs in order to say "benchmark as of ..." honestly.
    latest_cached: dt.date | None = None


def load_bars(
    db: Session, symbol: str, start: dt.date | None = None, end: dt.date | None = None
) -> list[PriceBar]:
    stmt = select(PriceBar).where(PriceBar.symbol == symbol)
    if start is not None:
        stmt = stmt.where(PriceBar.date >= start)
    if end is not None:
        stmt = stmt.where(PriceBar.date <= end)
    return list(db.scalars(stmt.order_by(PriceBar.date)))


def latest_bar_date(db: Session, symbol: str) -> dt.date | None:
    return db.scalar(
        select(PriceBar.date).where(PriceBar.symbol == symbol).order_by(PriceBar.date.desc()).limit(1)
    )


def upsert_bars(db: Session, symbol: str, source: str, bars: list[DailyBar]) -> int:
    """Idempotent by `(symbol, date)`. Returns the number of rows touched."""
    if not bars:
        return 0
    existing = {
        row.date: row
        for row in load_bars(db, symbol, min(b.date for b in bars), max(b.date for b in bars))
    }
    now = dt.datetime.now(dt.timezone.utc)
    touched = 0
    for bar in bars:
        row = existing.get(bar.date)
        if row is None:
            row = PriceBar(symbol=symbol, date=bar.date)
            db.add(row)
        row.close = bar.close
        row.adj_close = bar.adj_close
        row.source = source
        row.fetched_at = now
        touched += 1
    db.flush()
    return touched


def refresh_bars(
    db: Session,
    source: PriceSource,
    symbol: str,
    start: dt.date,
    end: dt.date,
) -> PriceRefreshResult:
    """Incremental fetch, non-fatal on failure (§7.5).

    Fetches only what the cache is missing, minus a restatement overlap. A
    provider outage returns `ok=False` with the cache untouched — the caller
    logs it, the benchmark line renders stale, and the sync run stays
    green, because a dead price API is not a reason to lose a day of trade
    data.
    """
    cached_to = latest_bar_date(db, symbol)
    fetch_from = start
    if cached_to is not None and cached_to >= start:
        fetch_from = max(start, cached_to - dt.timedelta(days=RESTATEMENT_OVERLAP_DAYS))

    if fetch_from > end:
        return PriceRefreshResult(
            symbol=symbol, source=source.name, ok=True, bars_written=0, latest_cached=cached_to
        )

    try:
        bars = source.daily_bars(symbol, fetch_from, end)
    except PriceFetchError as exc:
        return PriceRefreshResult(
            symbol=symbol,
            source=source.name,
            ok=False,
            error=str(exc),
            fetched_from=fetch_from,
            fetched_to=end,
            latest_cached=cached_to,
        )
    except Exception as exc:  # noqa: BLE001 — an adapter must never take the sync down
        return PriceRefreshResult(
            symbol=symbol,
            source=source.name,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            fetched_from=fetch_from,
            fetched_to=end,
            latest_cached=cached_to,
        )

    written = upsert_bars(db, symbol, source.name, bars)
    return PriceRefreshResult(
        symbol=symbol,
        source=source.name,
        ok=True,
        bars_written=written,
        fetched_from=fetch_from,
        fetched_to=end,
        latest_cached=latest_bar_date(db, symbol),
    )
