"""§9.1 — the x-axis library.

Each dimension maps a `ChartUnit` onto a bucket: a stable key, a display
label, and a sort key so that "Mon, Tue, Wed" and "0-7 DTE, 8-30 DTE"
render in their natural order rather than alphabetically. Buckets are
returned as `None` when the unit cannot be placed — an option-only
dimension applied to a stock trade, say — and the aggregator drops those
units rather than inventing an "Unknown" column that then gets read as a
finding.
"""

from dataclasses import dataclass
from typing import Callable

from app.charts.units import ChartUnit
from app.trading_day import EASTERN


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    sort: tuple


#: A dimension returns the bucket a unit belongs in, `None` when it has no
#: value on that axis, or a *list* when the axis is genuinely multi-valued.
#: Tags are the only such axis: a trade can carry three of them, and forcing
#: a single bucket would either drop two or invent a "multiple" category.
DimensionFn = Callable[[ChartUnit], "Bucket | list[Bucket] | None"]

#: Axes on which one unit can land in several buckets. Charts over these do
#: **not** sum to the period total, and the API says so rather than letting
#: the double counting look like a discrepancy.
OVERLAPPING_DIMENSIONS = frozenset({"tag", "tag_group"})

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _by_time_of_day(unit: ChartUnit) -> Bucket | None:
    if unit.opened_at is None:
        return None
    # Hour of entry, in the broker's local time — the same clock §14.1
    # derives the trading day from, so an evening futures fill lands in the
    # 18:00 bucket rather than in whatever UTC calls it.
    hour = unit.opened_at.astimezone(EASTERN).hour
    return Bucket(key=f"{hour:02d}", label=f"{hour:02d}:00 ET", sort=(hour,))


def _by_day_of_week(unit: ChartUnit) -> Bucket | None:
    if unit.trading_day is None:
        return None
    idx = unit.trading_day.weekday()
    return Bucket(key=_WEEKDAYS[idx][:3], label=_WEEKDAYS[idx], sort=(idx,))


def _by_day_of_month(unit: ChartUnit) -> Bucket | None:
    if unit.trading_day is None:
        return None
    day = unit.trading_day.day
    return Bucket(key=str(day), label=str(day), sort=(day,))


def _by_month(unit: ChartUnit) -> Bucket | None:
    if unit.trading_day is None:
        return None
    key = unit.trading_day.strftime("%Y-%m")
    return Bucket(key=key, label=unit.trading_day.strftime("%b %Y"), sort=(key,))


def _by_year(unit: ChartUnit) -> Bucket | None:
    if unit.trading_day is None:
        return None
    return Bucket(key=str(unit.trading_day.year), label=str(unit.trading_day.year), sort=(unit.trading_day.year,))


#: Wall-clock duration bands. Chosen to separate the behaviours that
#: actually differ for this account: same-session scalps, overnight holds,
#: a week, and everything longer.
_DURATION_BANDS = [
    (60 * 60, "< 1 hour"),
    (60 * 60 * 6, "1-6 hours"),
    (60 * 60 * 24, "6-24 hours"),
    (60 * 60 * 24 * 7, "1-7 days"),
    (60 * 60 * 24 * 30, "1-4 weeks"),
    (float("inf"), "> 1 month"),
]


def _by_duration(unit: ChartUnit) -> Bucket | None:
    if unit.duration_seconds is None or unit.excluded_from_duration:
        # §0.3, §5.5 — a hold time inherited from before the epoch or from
        # another broker is not a hold time you can learn from, so it is
        # not bucketed at all.
        return None
    for i, (limit, label) in enumerate(_DURATION_BANDS):
        if unit.duration_seconds < limit:
            return Bucket(key=label, label=label, sort=(i,))
    return None


def _by_hold_days(unit: ChartUnit) -> Bucket | None:
    if unit.opened_at is None or unit.closed_at is None or unit.excluded_from_duration:
        return None
    days = (unit.closed_at.date() - unit.opened_at.date()).days
    if days == 0:
        return Bucket(key="intraday", label="Intraday", sort=(0,))
    for i, limit in enumerate([1, 3, 7, 14, 30, 90], start=1):
        if days <= limit:
            return Bucket(key=f"<={limit}d", label=f"{limit} day{'s' if limit > 1 else ''} or less", sort=(i,))
    return Bucket(key=">90d", label="Over 90 days", sort=(7,))


def _by_symbol(unit: ChartUnit) -> Bucket | None:
    return Bucket(key=unit.symbol, label=unit.symbol, sort=(unit.symbol,)) if unit.symbol else None


def _by_underlying(unit: ChartUnit) -> Bucket | None:
    return (
        Bucket(key=unit.underlying, label=unit.underlying, sort=(unit.underlying,))
        if unit.underlying
        else None
    )


def _by_asset_class(unit: ChartUnit) -> Bucket | None:
    return (
        Bucket(key=unit.asset_class, label=unit.asset_class, sort=(unit.asset_class,))
        if unit.asset_class
        else None
    )


def _by_strategy(unit: ChartUnit) -> Bucket | None:
    return (
        Bucket(key=unit.strategy_type, label=unit.strategy_type.replace("_", " ").title(), sort=(unit.strategy_type,))
        if unit.strategy_type
        else None
    )


def _by_book(unit: ChartUnit) -> Bucket | None:
    return Bucket(key=unit.book, label=unit.book, sort=(unit.book,)) if unit.book else None


def _by_side(unit: ChartUnit) -> Bucket | None:
    return Bucket(key=unit.side, label=unit.side.title(), sort=(unit.side,)) if unit.side else None


def _by_account(unit: ChartUnit) -> Bucket | None:
    return Bucket(key=unit.account_id, label=unit.account_id, sort=(unit.account_id,))


_SIZE_BANDS = [1, 2, 5, 10, 25, 50, 100]


def _by_position_size(unit: ChartUnit) -> Bucket | None:
    qty = abs(unit.quantity)
    if not qty:
        return None
    for i, limit in enumerate(_SIZE_BANDS):
        if qty <= limit:
            return Bucket(key=f"<={limit}", label=f"{limit} or fewer", sort=(i,))
    return Bucket(key=">100", label="Over 100", sort=(len(_SIZE_BANDS),))


_PRICE_BANDS = [1, 5, 10, 25, 50, 100, 250, 500]


def _by_price_range(unit: ChartUnit) -> Bucket | None:
    price = unit.open_price
    if price is None:
        return None
    price = abs(price)
    for i, limit in enumerate(_PRICE_BANDS):
        if price < limit:
            return Bucket(key=f"<{limit}", label=f"Under ${limit}", sort=(i,))
    return Bucket(key=">=500", label="$500 and up", sort=(len(_PRICE_BANDS),))


#: DTE bands matched to how options behave rather than to round numbers:
#: gamma risk in the last week, the 30-45 day window most premium selling
#: targets, and everything longer-dated.
_DTE_BANDS = [(0, "0 DTE"), (7, "1-7 DTE"), (14, "8-14 DTE"), (30, "15-30 DTE"), (45, "31-45 DTE"), (90, "46-90 DTE")]


def _by_dte(unit: ChartUnit) -> Bucket | None:
    dte = unit.dte_at_open
    if dte is None:
        return None
    for i, (limit, label) in enumerate(_DTE_BANDS):
        if dte <= limit:
            return Bucket(key=label, label=label, sort=(i,))
    return Bucket(key="90+ DTE", label="Over 90 DTE", sort=(len(_DTE_BANDS),))


def _by_tag(unit: ChartUnit) -> list[Bucket] | None:
    if not unit.tags:
        return None
    return [Bucket(key=t, label=t, sort=(t,)) for t in unit.tags]


def _by_tag_group(unit: ChartUnit) -> list[Bucket] | None:
    if not unit.tag_groups:
        return None
    return [Bucket(key=g, label=g, sort=(g,)) for g in unit.tag_groups]


DIMENSIONS: dict[str, DimensionFn] = {
    "time_of_day": _by_time_of_day,
    "day_of_week": _by_day_of_week,
    "day_of_month": _by_day_of_month,
    "month": _by_month,
    "year": _by_year,
    "duration": _by_duration,
    "hold_time": _by_hold_days,
    "symbol": _by_symbol,
    "underlying": _by_underlying,
    "asset_class": _by_asset_class,
    "strategy_type": _by_strategy,
    "book": _by_book,
    "side": _by_side,
    "position_size": _by_position_size,
    "price_range": _by_price_range,
    "dte_at_open": _by_dte,
    "account": _by_account,
    "tag": _by_tag,
    "tag_group": _by_tag_group,
}

DIMENSION_LABELS = {
    "time_of_day": "Time of day",
    "day_of_week": "Day of week",
    "day_of_month": "Day of month",
    "month": "Month",
    "year": "Year",
    "duration": "Trade duration",
    "hold_time": "Hold time",
    "symbol": "Symbol",
    "underlying": "Underlying",
    "asset_class": "Asset class",
    "strategy_type": "Strategy type",
    "book": "Book",
    "side": "Side",
    "position_size": "Position size",
    "price_range": "Price range",
    "dte_at_open": "DTE at open",
    "account": "Account",
    "tag": "Tag",
    "tag_group": "Tag group",
}

#: Nothing from §9.1 is missing any more — tags landed with §11.1. Kept as
#: the seam for the next dimension that is designed before it is buildable.
NOT_YET_AVAILABLE: dict[str, str] = {}
