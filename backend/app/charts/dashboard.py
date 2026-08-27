"""§9.3 / §14.6.1 — the default dashboard, and un-hardcoding it.

§14.6 left chart curation open with an honest reason: "§9.3's default
dashboard is a guess. Revisit once there is enough history to see which
views you actually open."

That revisit cannot be done from here — nobody has usage data yet, and
picking a different twelve charts would just be a second guess. What *can*
be done is to stop the guess being load-bearing. The §9.3 set ships as the
default, and the layout is a stored list the user edits: add a panel,
remove one, reorder. Curation becomes something that happens continuously
as you notice what you open, rather than a decision the spec has to get
right in advance.

Every panel is a `(dimension, metric)` pair against §9's existing grid,
plus a handful of purpose-built views that are not metric × dimension
charts at all. That keeps the promise from §9.2: ~200 charts from one
component, and a dashboard is a saved selection over them rather than
twelve bespoke widgets.
"""

from dataclasses import dataclass, field

from app.charts.dimensions import DIMENSIONS
from app.charts.metrics import METRICS

#: Panels that are not `metric × dimension` — each has its own endpoint and
#: its own component, because none of them is a bucketed aggregate.
SPECIAL_PANELS = {
    "equity_curve": "Account equity curve, with drawdown beneath",
    "cumulative_pnl": "Cumulative net P&L",
    "calendar": "Monthly P&L calendar",
    "drawdown": "Underwater drawdown plot",
    "trend": "Rolling per-trade trend (§9.4)",
    "distribution": "P&L distribution histogram",
}


@dataclass
class Panel:
    key: str
    title: str
    kind: str = "chart"  # "chart" (metric × dimension) or "special"
    dimension: str | None = None
    metric: str | None = None
    limit: int | None = None
    #: Only meaningful for `trend`.
    window: int | None = None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "kind": self.kind,
            "dimension": self.dimension,
            "metric": self.metric,
            "limit": self.limit,
            "window": self.window,
        }


#: §9.3's twelve, in the spec's own order. Items 1-3 and the histogram are
#: purpose-built views; the rest are ordinary grid pairs, which is the
#: point — most of a "dashboard" is a saved selection.
DEFAULT_PANELS: list[Panel] = [
    Panel("cumulative_pnl", "Cumulative net P&L", kind="special"),
    Panel("equity_curve", "Account equity curve", kind="special"),
    Panel("calendar", "Monthly P&L calendar", kind="special"),
    Panel("pnl_dow", "P&L by day of week", dimension="day_of_week", metric="net_pnl"),
    Panel("pnl_tod", "P&L by time of day", dimension="time_of_day", metric="net_pnl"),
    Panel("winrate_strategy", "Win rate by strategy", dimension="strategy_type", metric="win_rate"),
    Panel("distribution", "P&L distribution", kind="special"),
    Panel("pnl_underlying", "P&L by underlying", dimension="underlying", metric="net_pnl", limit=10),
    Panel("rolling_expectancy", "Rolling expectancy", kind="special", metric="expectancy", window=20),
    Panel("commissions_month", "Commissions by month", dimension="month", metric="commissions"),
    Panel("duration_pnl", "P&L by hold time", dimension="hold_time", metric="avg_pnl"),
    Panel("dte_ror", "Return on risk by DTE", dimension="dte_at_open", metric="return_on_risk"),
]

#: `rolling_expectancy` is the trend view under a §9.3 name.
_SPECIAL_ALIASES = {"rolling_expectancy": "trend"}


class InvalidPanel(ValueError):
    pass


def validate_panel(raw: dict) -> Panel:
    key = (raw.get("key") or "").strip()
    if not key:
        raise InvalidPanel("Every panel needs a key.")

    kind = raw.get("kind") or "chart"
    title = (raw.get("title") or key).strip()

    if kind == "special":
        resolved = _SPECIAL_ALIASES.get(key, key)
        if resolved not in SPECIAL_PANELS:
            raise InvalidPanel(
                f"Unknown panel {key!r}. Available: {', '.join(sorted(SPECIAL_PANELS))}"
            )
        return Panel(
            key=key,
            title=title,
            kind="special",
            metric=raw.get("metric"),
            window=raw.get("window"),
        )

    dimension, metric = raw.get("dimension"), raw.get("metric")
    if dimension not in DIMENSIONS:
        raise InvalidPanel(
            f"Unknown dimension {dimension!r}. Available: {', '.join(sorted(DIMENSIONS))}"
        )
    if metric not in METRICS:
        raise InvalidPanel(
            f"Unknown metric {metric!r}. Available: {', '.join(sorted(METRICS))}"
        )
    limit = raw.get("limit")
    return Panel(
        key=key,
        title=title,
        kind="chart",
        dimension=dimension,
        metric=metric,
        limit=int(limit) if limit else None,
    )


@dataclass
class DashboardLayout:
    panels: list[Panel] = field(default_factory=lambda: list(DEFAULT_PANELS))
    customized: bool = False

    def as_dict(self) -> dict:
        return {
            "panels": [p.as_dict() for p in self.panels],
            "customized": self.customized,
            "available_special": [
                {"key": k, "label": v} for k, v in sorted(SPECIAL_PANELS.items())
            ],
            "note": (
                "§9.3's default set is an educated guess and the spec says so (§14.6). It is "
                "a starting point you edit, not a fixed dashboard — add what you keep "
                "opening, remove what you never do, and the guess stops mattering."
                if not self.customized
                else "Your layout. Reset returns to §9.3's default set."
            ),
        }


def parse_layout(stored: list | None) -> DashboardLayout:
    """Read a stored layout, falling back to the default.

    A stored panel that no longer validates — a dimension removed in a
    later version, say — is dropped rather than failing the whole
    dashboard. Losing one panel is recoverable; a dashboard that refuses to
    render is not.
    """
    if not stored:
        return DashboardLayout()
    panels: list[Panel] = []
    for raw in stored:
        try:
            panels.append(validate_panel(raw))
        except InvalidPanel:
            continue
    if not panels:
        return DashboardLayout()
    return DashboardLayout(panels=panels, customized=True)
