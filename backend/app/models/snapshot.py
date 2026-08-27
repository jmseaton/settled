import datetime as dt

from sqlalchemy import Boolean, Date, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DailySnapshot(Base):
    """Materialized per-trading-day rollup (§4.11), rebuilt on every import.

    Backs the equity curve, the calendar heatmap, the drawdown chart and
    every daily-frequency ratio. Keyed on `trading_day` (§14.1-derived),
    never on calendar date — deriving the day twice is how the two halves of
    a dashboard end up disagreeing.

    Rows are written for **every trading day in the measured period**, not
    only days with activity. A curve drawn from activity days alone silently
    compresses the x-axis and makes a flat month look like a fast one, and
    the daily-return series behind Sharpe would be missing its zeroes.
    """

    __tablename__ = "daily_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32))
    trading_day: Mapped[dt.date] = mapped_column(Date, index=True)

    realized_gross: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    realized_net: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    commissions: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)
    volume: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    cumulative_net_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)

    # §8.1 distinguishes "total net closed P&L" from "realized P&L incl.
    # partials", and the two really are different numbers: a position half
    # closed has banked cash without producing an outcome. `realized_net`
    # above counts only completed round trips, so the win rate and the
    # trade count stay honest; this column carries the rest, and
    # `account_value` uses both. Omitting it understated the fixture's
    # account by $3.96 — small, permanent, and growing.
    realized_partial_net: Mapped[float] = mapped_column(Numeric(18, 6), default=0)

    # §7.2 — external flows only (deposits, withdrawals, transfers in/out).
    # This is the `F_t` that TWR strips out.
    cash_flow: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    # Dividends, interest and fees: cash effects that are *not* external
    # flows. Kept separate so they can be added back for the "+ dividends"
    # equity-curve overlay (§7.4) without ever being mistaken for a flow.
    income_cash: Mapped[float] = mapped_column(Numeric(18, 6), default=0)

    # §12 / §7.2's "[+ unrealized_t if available]" — now available, from
    # the marks Flex already publishes on Open Positions. Only days with a
    # position snapshot carry a real figure; `unrealized_priced` says which
    # those are, so a curve that is realized-only for its first two months
    # and marked thereafter cannot be misread as a sudden good day.
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    unrealized_priced: Mapped[bool] = mapped_column(Boolean, default=False)

    account_value: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    drawdown: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    drawdown_pct: Mapped[float] = mapped_column(Numeric(18, 9), default=0)

    #: §7.5 — the cash-flow-matched shadow portfolio's value. Null when no
    #: benchmark bar covers the day; the UI renders the gap as stale rather
    #: than as zero.
    benchmark_shadow_value: Mapped[float | None] = mapped_column(
        Numeric(18, 6), nullable=True
    )
    #: True when the shadow value was carried forward from an older bar
    #: because the fetch is behind. Test 54: a price-fetch failure degrades
    #: to stale, it does not blank the chart or fail the sync.
    benchmark_stale: Mapped[bool] = mapped_column(Boolean, default=False)
