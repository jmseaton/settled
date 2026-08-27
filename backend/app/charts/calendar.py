"""§9.3 #3 — the monthly P&L calendar heatmap.

Read straight off `daily_snapshots`, which is why that table is keyed on
`trading_day` rather than on a calendar date (§4.11): a futures fill at
18:12 ET on a Sunday belongs to Monday's session, and a calendar that put
it on Sunday would disagree with every other view in the app.

Days the market was open with no activity are returned with zeroes rather
than omitted. An empty cell and a flat cell mean different things, and the
heatmap is where that difference is most visible.
"""

import calendar as _calendar
import datetime as dt

from sqlalchemy.orm import Session

from app.models.snapshot import DailySnapshot


def build_calendar(
    db: Session,
    account_id: str,
    year: int | None = None,
    month: int | None = None,
) -> dict:
    query = db.query(DailySnapshot).filter(DailySnapshot.account_id == account_id)
    if year is not None:
        start = dt.date(year, month or 1, 1)
        if month is not None:
            last = _calendar.monthrange(year, month)[1]
            end = dt.date(year, month, last)
        else:
            end = dt.date(year, 12, 31)
        query = query.filter(DailySnapshot.trading_day >= start, DailySnapshot.trading_day <= end)

    rows = query.order_by(DailySnapshot.trading_day).all()
    days = [
        {
            "trading_day": r.trading_day.isoformat(),
            "realized_net": float(r.realized_net),
            "realized_gross": float(r.realized_gross),
            "commissions": float(r.commissions),
            "trade_count": r.trade_count,
            "win_count": r.win_count,
            "loss_count": r.loss_count,
            "cash_flow": float(r.cash_flow),
            "account_value": float(r.account_value),
            "drawdown": float(r.drawdown),
        }
        for r in rows
    ]

    months: dict[str, dict] = {}
    for r in rows:
        key = r.trading_day.strftime("%Y-%m")
        bucket = months.setdefault(
            key, {"month": key, "realized_net": 0.0, "trade_count": 0, "winning_days": 0, "losing_days": 0}
        )
        net = float(r.realized_net)
        bucket["realized_net"] += net
        bucket["trade_count"] += r.trade_count
        if net > 0:
            bucket["winning_days"] += 1
        elif net < 0:
            bucket["losing_days"] += 1

    nets = [d["realized_net"] for d in days]
    active = [n for n in nets if n]
    return {
        "days": days,
        "months": sorted(months.values(), key=lambda m: m["month"]),
        # Scale bounds for the heatmap. Computed from days that actually
        # traded: including the zeroes would compress every colour toward
        # the middle and make a flat month and a good month look alike.
        "scale": {
            "max_gain": max(active, default=0.0),
            "max_loss": min(active, default=0.0),
            "active_days": len(active),
            "flat_days": len(nets) - len(active),
        },
    }
