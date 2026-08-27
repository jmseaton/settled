"""§9 — the chart grid: any metric × any dimension, from one function.

"Implementing this as a single parameterized component gives you ~200
charts from one piece of code, which is exactly how TradesViz gets its
numbers up." The backend half of that is this module; there is no
per-chart code anywhere, and adding a chart is adding a dimension or a
metric, never an endpoint.

Every bucket carries the ids of the units behind it, because §9's opening
requirement is that clicking a data point drills into the underlying
trades. Building that generically once is what keeps a large chart count
useful instead of overwhelming.
"""

from dataclasses import dataclass

from app.charts.dimensions import (
    DIMENSION_LABELS,
    DIMENSIONS,
    NOT_YET_AVAILABLE,
    OVERLAPPING_DIMENSIONS,
)
from app.charts.metrics import METRICS
from app.charts.units import ChartUnit

#: Buckets thinner than this are still returned — dropping them would hide
#: data — but flagged, so a 100% win rate off one trade is visibly one
#: trade rather than a tall green bar (§8.2's argument, applied to charts).
THIN_BUCKET_UNITS = 5


class UnknownAxis(ValueError):
    pass


@dataclass
class ChartBucket:
    key: str
    label: str
    value: float | None
    unit_count: int
    net_pnl: float
    unit_ids: list[int]
    thin: bool

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "unit_count": self.unit_count,
            "net_pnl": self.net_pnl,
            "unit_ids": self.unit_ids,
            "thin": self.thin,
        }


def resolve_dimension(name: str):
    if name in NOT_YET_AVAILABLE:
        raise UnknownAxis(f"Dimension {name!r} is not available yet: {NOT_YET_AVAILABLE[name]}")
    if name not in DIMENSIONS:
        raise UnknownAxis(
            f"Unknown dimension {name!r}. Available: {', '.join(sorted(DIMENSIONS))}"
        )
    return DIMENSIONS[name]


def resolve_metric(name: str):
    if name not in METRICS:
        raise UnknownAxis(f"Unknown metric {name!r}. Available: {', '.join(sorted(METRICS))}")
    return METRICS[name]


def build_chart(units: list[ChartUnit], dimension: str, metric: str, limit: int | None = None) -> dict:
    """Aggregate `units` into buckets and reduce each with `metric`.

    `limit` keeps only the largest and smallest buckets by value — §9.3 #8's
    "top/bottom 10 by underlying". Applied after aggregation, so the
    excluded buckets are still counted in the totals rather than silently
    vanishing from them.
    """
    dim_fn = resolve_dimension(dimension)
    spec = resolve_metric(metric)

    grouped: dict[str, list[ChartUnit]] = {}
    labels: dict[str, str] = {}
    sorts: dict[str, tuple] = {}
    unplaced = 0

    for unit in units:
        placed = dim_fn(unit)
        if placed is None:
            # The unit has no value on this axis — an equity trade has no
            # DTE. Counted and reported, never bucketed as "Unknown": an
            # Unknown column gets read as a finding.
            unplaced += 1
            continue
        for bucket in placed if isinstance(placed, list) else [placed]:
            grouped.setdefault(bucket.key, []).append(unit)
            labels[bucket.key] = bucket.label
            sorts[bucket.key] = bucket.sort

    buckets = [
        ChartBucket(
            key=key,
            label=labels[key],
            value=spec.fn(members),
            unit_count=len(members),
            net_pnl=sum(m.net_pnl for m in members),
            unit_ids=[m.unit_id for m in members],
            thin=len(members) < THIN_BUCKET_UNITS,
        )
        for key, members in grouped.items()
    ]
    buckets.sort(key=lambda b: sorts[b.key])

    shown = buckets
    truncated = 0
    if limit is not None and len(buckets) > limit * 2:
        by_value = sorted(
            [b for b in buckets if b.value is not None], key=lambda b: b.value, reverse=True
        )
        keep = {b.key for b in by_value[:limit]} | {b.key for b in by_value[-limit:]}
        shown = [b for b in buckets if b.key in keep]
        truncated = len(buckets) - len(shown)

    return {
        "dimension": dimension,
        "dimension_label": DIMENSION_LABELS.get(dimension, dimension),
        "metric": metric,
        "metric_label": spec.label,
        "metric_unit": spec.unit,
        "metric_signed": spec.signed,
        "unit_count": len(units),
        "bucket_count": len(buckets),
        "unplaced_units": unplaced,
        "unplaced_note": (
            f"{unplaced} unit(s) have no value on this axis and are excluded rather than "
            "bucketed as Unknown."
            if unplaced
            else None
        ),
        "truncated_buckets": truncated,
        "thin_bucket_threshold": THIN_BUCKET_UNITS,
        # A trade can carry three tags, so it lands in three buckets. The
        # columns are each correct and the total across them is not the
        # period total — said plainly, because otherwise it reads as a bug.
        "overlapping": dimension in OVERLAPPING_DIMENSIONS,
        "overlapping_note": (
            "A unit appears in every bucket it is tagged with, so these columns overlap and "
            "do not sum to the period total."
            if dimension in OVERLAPPING_DIMENSIONS
            else None
        ),
        "buckets": [b.as_dict() for b in shown],
    }


def axis_catalog() -> dict:
    """What the axis dropdowns offer, including what they deliberately do not."""
    return {
        "dimensions": [
            {"key": key, "label": DIMENSION_LABELS.get(key, key)} for key in DIMENSIONS
        ],
        "metrics": [
            {"key": spec.key, "label": spec.label, "unit": spec.unit, "signed": spec.signed}
            for spec in METRICS.values()
        ],
        "unavailable": [{"key": k, "reason": v} for k, v in NOT_YET_AVAILABLE.items()],
    }
