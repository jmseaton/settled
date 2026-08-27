"""Global filters (§9, Phase 3).

"Right-click to apply that bucket as a global filter" is the interaction
that makes a large chart count usable rather than overwhelming, so the
filter set is defined once, applied in one place, and echoed back on every
response — a chart whose filters are invisible is a chart that gets
misread.

Applied to `ChartUnit`s in memory rather than as SQL. At personal-account
volume the whole unit set is a few thousand rows, and keeping the filter
logic beside the dimension logic means the two can never disagree about
what "Options Income in July" means.
"""

import datetime as dt
from dataclasses import dataclass, field

from app.charts.units import ChartUnit


@dataclass
class ChartFilters:
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    books: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    underlyings: list[str] = field(default_factory=list)
    asset_classes: list[str] = field(default_factory=list)
    strategy_types: list[str] = field(default_factory=list)
    sides: list[str] = field(default_factory=list)
    #: Bucket keys from a drill-down, paired with the dimension they came
    #: from — this is what a right-click on a chart column sends back.
    dimension: str | None = None
    dimension_keys: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            [
                self.date_from,
                self.date_to,
                self.books,
                self.symbols,
                self.underlyings,
                self.asset_classes,
                self.strategy_types,
                self.sides,
                self.dimension_keys,
            ]
        )

    def as_dict(self) -> dict:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "books": self.books,
            "symbols": self.symbols,
            "underlyings": self.underlyings,
            "asset_classes": self.asset_classes,
            "strategy_types": self.strategy_types,
            "sides": self.sides,
            "dimension": self.dimension,
            "dimension_keys": self.dimension_keys,
            "active": not self.is_empty(),
        }


def _matches(value: str | None, allowed: list[str]) -> bool:
    if not allowed:
        return True
    return value is not None and value in allowed


def apply_filters(units: list[ChartUnit], filters: ChartFilters) -> list[ChartUnit]:
    from app.charts.dimensions import DIMENSIONS

    drill = DIMENSIONS.get(filters.dimension) if filters.dimension else None
    out: list[ChartUnit] = []

    for unit in units:
        if filters.date_from and (unit.trading_day is None or unit.trading_day < filters.date_from):
            continue
        if filters.date_to and (unit.trading_day is None or unit.trading_day > filters.date_to):
            continue
        if not _matches(unit.book, filters.books):
            continue
        if not _matches(unit.symbol, filters.symbols):
            continue
        if not _matches(unit.underlying, filters.underlyings):
            continue
        if not _matches(unit.asset_class, filters.asset_classes):
            continue
        if not _matches(unit.strategy_type, filters.strategy_types):
            continue
        if not _matches(unit.side, filters.sides):
            continue
        if drill is not None and filters.dimension_keys:
            bucket = drill(unit)
            if bucket is None or bucket.key not in filters.dimension_keys:
                continue
        out.append(unit)

    return out


def parse_csv(raw: str | None) -> list[str]:
    """Query params arrive as `books=Holdings,Options Income`."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]
