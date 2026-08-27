"""§14.1 — timezone and the trading day.

Executions are broker-local (US/Eastern). A configurable session_start_hour
(default 18:00 ET) applies to FUT/FOP only, following CME's 18:00-17:00
session convention: a futures fill at or after 18:00 ET belongs to the
*next* calendar day's trading session. Equities and equity options use the
plain calendar date. Apply the shift once, here, and derive every
daily-frequency statistic from `executions.trading_day` / `daily_snapshots`
— never re-derive it in individual queries (§14.1 implementation note).
"""

import datetime as dt
from zoneinfo import ZoneInfo

from app.models.enums import AssetClass

EASTERN = ZoneInfo("America/New_York")


def localize(trade_date: dt.date, trade_time: dt.time) -> dt.datetime:
    """Broker-local timestamps are US/Eastern for these venues (§2.2 field 8)."""
    return dt.datetime.combine(trade_date, trade_time, tzinfo=EASTERN)


def derive_trading_day(
    executed_at: dt.datetime, asset_class: AssetClass, session_start_hour: int = 18
) -> dt.date:
    if asset_class not in (AssetClass.FUT, AssetClass.FOP):
        return executed_at.date()

    local = executed_at.astimezone(EASTERN)
    if local.time() >= dt.time(session_start_hour, 0, 0):
        return local.date() + dt.timedelta(days=1)
    return local.date()
