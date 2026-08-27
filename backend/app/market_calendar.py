"""NYSE trading calendar — weekends and market holidays.

Used in two places that both go subtly wrong without it:

  * **Trading-day DTE** (§9.5, test 73). A 2-DTE position over a holiday
    weekend is not a 2-day position.
  * **The sync scheduler** (§3.8), which skips days the market was closed.

Computed from NYSE's published rules rather than a hardcoded date list.
A list would need extending every few years and would fail silently the
moment it ran out; the rules are stable and are pinned here by tests
asserting dates that can be checked independently.

**The one thing that will eventually make this wrong** is a *new* holiday
being added — Juneteenth became an NYSE holiday in 2022, and nothing here
could have predicted that. `HOLIDAY_RULES_VALID_FROM` records the era these
rules describe, and `holidays_for_year` refuses to answer for earlier years
rather than returning a confidently wrong set.

Half-days (the 1pm closes after Thanksgiving and before Christmas) are
deliberately not modeled: the market is open, so they are trading days, and
nothing here counts hours.
"""

import datetime as dt
import functools

#: Juneteenth was added in 2022. Before that the holiday set differs, and
#: this module would quietly overstate the number of closures.
HOLIDAY_RULES_VALID_FROM = 2022

_SATURDAY = 5
_SUNDAY = 6


class CalendarOutOfRange(Exception):
    pass


def easter(year: int) -> dt.date:
    """Gregorian Easter (anonymous / Meeus-Jones-Butcher algorithm).

    Needed only for Good Friday, which is the one NYSE holiday with no
    fixed date and no nth-weekday rule.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lunar = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lunar) // 451
    month, day = divmod(h + lunar - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The nth given weekday of a month (Monday = 0)."""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    if month == 12:
        last = dt.date(year, 12, 31)
    else:
        last = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(date: dt.date) -> dt.date | None:
    """NYSE observance: a holiday on Saturday is taken the preceding Friday,
    one on Sunday the following Monday.

    The exception is New Year's Day on a Saturday, which NYSE does *not*
    observe on the preceding Friday — that Friday is the last trading day
    of the prior year and the market is open. Returning None expresses
    "no closure at all" rather than a date that would be wrong.
    """
    if date.weekday() == _SATURDAY:
        if date.month == 1 and date.day == 1:
            return None
        return date - dt.timedelta(days=1)
    if date.weekday() == _SUNDAY:
        return date + dt.timedelta(days=1)
    return date


@functools.lru_cache(maxsize=64)
def holidays_for_year(year: int) -> frozenset[dt.date]:
    if year < HOLIDAY_RULES_VALID_FROM:
        raise CalendarOutOfRange(
            f"NYSE holiday rules here describe {HOLIDAY_RULES_VALID_FROM} onward "
            f"(Juneteenth was added in 2022); refusing to answer for {year}."
        )

    fixed = [
        dt.date(year, 1, 1),  # New Year's Day
        dt.date(year, 6, 19),  # Juneteenth
        dt.date(year, 7, 4),  # Independence Day
        dt.date(year, 12, 25),  # Christmas
    ]

    dates = {observed for d in fixed if (observed := _observed(d)) is not None}
    dates.update(
        {
            _nth_weekday(year, 1, 0, 3),  # MLK Jr. Day, 3rd Monday of January
            _nth_weekday(year, 2, 0, 3),  # Washington's Birthday, 3rd Monday of February
            easter(year) - dt.timedelta(days=2),  # Good Friday
            _last_weekday(year, 5, 0),  # Memorial Day, last Monday of May
            _nth_weekday(year, 9, 0, 1),  # Labor Day, 1st Monday of September
            _nth_weekday(year, 11, 3, 4),  # Thanksgiving, 4th Thursday of November
        }
    )
    return frozenset(dates)


def is_market_holiday(date: dt.date) -> bool:
    return date in holidays_for_year(date.year)


def is_trading_day(date: dt.date) -> bool:
    return date.weekday() < _SATURDAY and not is_market_holiday(date)


def trading_days_between(start: dt.date, end: dt.date) -> int:
    """Trading days strictly after `start`, up to and including `end`.

    Exclusive of `start` so "days from today until expiry" reads naturally:
    an expiry tomorrow is 1, and an expiry today is 0.
    """
    if end <= start:
        return 0
    count = 0
    cursor = start
    while cursor < end:
        cursor += dt.timedelta(days=1)
        if is_trading_day(cursor):
            count += 1
    return count


def next_trading_day(date: dt.date) -> dt.date:
    cursor = date + dt.timedelta(days=1)
    while not is_trading_day(cursor):
        cursor += dt.timedelta(days=1)
    return cursor
