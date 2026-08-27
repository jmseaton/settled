"""§9.4 — trend analysis, and the regime shift it is meant to reveal.

"Any metric plotted **per-trade** (not per-date) with an SMA/EMA overlay;
answers 'is my edge improving over the last 50 trades?'"

Per-trade rather than per-date is the whole point. A calendar axis spaces
points by how much time passed; this one spaces them by how many decisions
were made, which is the axis a change in *behaviour* shows up on. §6.6
names this as the view that reveals a regime change — the fixture's own MES
book switched from verticals to naked shorts on 2026-07-12, and on a
per-trade axis with a moving average that turn is visible rather than
inferred.

Two overlays, because they answer differently: an SMA weights the window
evenly and lags predictably, while an EMA reacts sooner and is the better
choice for spotting that something *has already* changed.
"""

import math
from dataclasses import dataclass

from app.charts.metrics import METRICS, MetricSpec
from app.charts.units import ChartUnit

#: Long enough to mean something, short enough to move. §9.4's own example
#: is "the last 50 trades"; 20 is the default because at this account's
#: volume 50 spans most of the history.
DEFAULT_WINDOW = 20

#: A moving average over fewer points than this is the series itself with
#: extra steps, and drawing it implies a smoothing that is not happening.
MIN_WINDOW = 2


class UnknownMetric(ValueError):
    pass


@dataclass
class TrendPoint:
    sequence: int
    unit_id: int
    unit_kind: str
    label: str
    trading_day: str | None
    value: float | None
    sma: float | None
    ema: float | None
    cumulative: float | None


def _rolling(values: list[float | None], window: int) -> list[float | None]:
    """Simple moving average over the last `window` *defined* values.

    Units with no value on the chosen metric are skipped rather than
    treated as zero — a trade with no return-on-risk has no return on risk,
    and averaging a zero in would drag the line toward a number nobody
    earned.
    """
    out: list[float | None] = []
    seen: list[float] = []
    for value in values:
        if value is not None:
            seen.append(value)
        if len(seen) >= window:
            out.append(sum(seen[-window:]) / window)
        else:
            out.append(None)
    return out


def _ema(values: list[float | None], window: int) -> list[float | None]:
    alpha = 2.0 / (window + 1.0)
    out: list[float | None] = []
    current: float | None = None
    for value in values:
        if value is None:
            out.append(current)
            continue
        current = value if current is None else alpha * value + (1 - alpha) * current
        out.append(current)
    return out


def _per_unit_value(unit: ChartUnit, spec: MetricSpec) -> float | None:
    """The metric evaluated over a single unit.

    Reusing the same functions the grid aggregates with, on a one-element
    list, so a metric can never mean one thing on the chart grid and
    another here. Some are undefined for one unit — a single trade has no
    profit factor unless it lost — and those come back None.
    """
    return spec.fn([unit])


def build_trend(
    units: list[ChartUnit],
    metric: str,
    window: int = DEFAULT_WINDOW,
) -> dict:
    spec = METRICS.get(metric)
    if spec is None:
        raise UnknownMetric(
            f"Unknown metric {metric!r}. Available: {', '.join(sorted(METRICS))}"
        )
    window = max(MIN_WINDOW, int(window))

    ordered = sorted(
        [u for u in units if u.closed_at is not None],
        key=lambda u: (u.closed_at, u.unit_id),
    )
    values = [_per_unit_value(u, spec) for u in ordered]
    sma = _rolling(values, window)
    ema = _ema(values, window)

    cumulative: list[float | None] = []
    running = 0.0
    for value in values:
        if value is not None:
            running += value
        cumulative.append(running)

    points = [
        TrendPoint(
            sequence=i + 1,
            unit_id=u.unit_id,
            unit_kind=u.unit_kind,
            label=u.symbol or u.underlying or str(u.unit_id),
            trading_day=u.trading_day.isoformat() if u.trading_day else None,
            value=values[i],
            sma=sma[i],
            ema=ema[i],
            cumulative=cumulative[i],
        )
        for i, u in enumerate(ordered)
    ]

    defined = [v for v in values if v is not None]
    return {
        "metric": metric,
        "metric_label": spec.label,
        "metric_unit": spec.unit,
        "window": window,
        "unit_count": len(points),
        "undefined_units": len(points) - len(defined),
        "axis": "per_trade",
        "axis_note": (
            "Ordered by close, one point per unit — not per calendar day. Spacing reflects "
            "how many decisions were made rather than how much time passed, which is the "
            "axis a change in behaviour shows up on (§9.4)."
        ),
        "window_note": (
            f"The moving averages need {window} defined values before they start, so the "
            "first stretch of the line is deliberately absent rather than drawn from a "
            "shorter window that would look smoother than the data is."
        ),
        "points": [p.__dict__ for p in points],
    }


#: §9.3 #7 / §9.4 — a distribution needs enough values to have a shape.
#: Below this the histogram is a bar chart of individual trades wearing a
#: statistical costume, so it is refused rather than drawn.
MIN_DISTRIBUTION_UNITS = 5


def build_distribution(units: list[ChartUnit], metric: str, bins: int = 15) -> dict:
    """§9.3 #7 — "P&L distribution histogram (with a normal overlay)".

    The overlay is a normal fitted to the same mean and standard deviation,
    drawn so the *departure* from it is visible. That departure is the
    interesting part for a trading distribution: premium selling produces a
    left tail that a normal badly understates, and seeing the bars sit
    outside the curve is the point.

    Scaled to counts rather than density, so the curve and the bars are
    directly comparable by eye — a density curve against a count histogram
    is a chart that looks wrong and is not.
    """
    spec = METRICS.get(metric)
    if spec is None:
        raise UnknownMetric(
            f"Unknown metric {metric!r}. Available: {', '.join(sorted(METRICS))}"
        )

    values = [v for v in (spec.fn([u]) for u in units) if v is not None]
    if len(values) < MIN_DISTRIBUTION_UNITS:
        return {
            "metric": metric,
            "metric_label": spec.label,
            "metric_unit": spec.unit,
            "count": len(values),
            "bins": [],
            "normal": [],
            "insufficient": True,
            "note": (
                f"{len(values)} value(s) is not a distribution. A histogram needs at least "
                f"{MIN_DISTRIBUTION_UNITS} to have a shape worth reading."
            ),
        }

    low, high = min(values), max(values)
    if high == low:
        high = low + 1.0
    width = (high - low) / bins

    counts = [0] * bins
    for value in values:
        index = min(int((value - low) / width), bins - 1)
        counts[index] += 1

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    stdev = variance**0.5

    normal: list[float | None] = []
    for i in range(bins):
        centre = low + width * (i + 0.5)
        if stdev <= 0:
            normal.append(None)
            continue
        density = math.exp(-((centre - mean) ** 2) / (2 * variance)) / (
            stdev * math.sqrt(2 * math.pi)
        )
        # Counts, not density, so bars and curve share a y-axis.
        normal.append(density * len(values) * width)

    return {
        "metric": metric,
        "metric_label": spec.label,
        "metric_unit": spec.unit,
        "count": len(values),
        "mean": mean,
        "stdev": stdev,
        "bins": [
            {
                "from": low + width * i,
                "to": low + width * (i + 1),
                "centre": low + width * (i + 0.5),
                "count": counts[i],
            }
            for i in range(bins)
        ],
        "normal": normal,
        "insufficient": False,
        "note": (
            "The overlay is a normal with the same mean and standard deviation, scaled to "
            "counts. It is there to be departed from: a premium-selling distribution has a "
            "left tail a normal badly understates, and the bars sitting outside the curve "
            "is the finding (§9.3)."
        ),
    }
