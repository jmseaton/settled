"""NYSE calendar. The dates asserted here are checkable against NYSE's
published holiday calendar — that is the point of asserting them rather
than trusting the rules that generated them."""

import datetime as dt

import pytest

from app.market_calendar import (
    CalendarOutOfRange,
    easter,
    holidays_for_year,
    is_market_holiday,
    is_trading_day,
    next_trading_day,
    trading_days_between,
)


def test_easter_matches_known_dates():
    assert easter(2024) == dt.date(2024, 3, 31)
    assert easter(2025) == dt.date(2025, 4, 20)
    assert easter(2026) == dt.date(2026, 4, 5)
    assert easter(2027) == dt.date(2027, 3, 28)


def test_2026_holidays_match_the_published_calendar():
    assert sorted(holidays_for_year(2026)) == [
        dt.date(2026, 1, 1),  # New Year's Day, Thursday
        dt.date(2026, 1, 19),  # MLK Jr. Day
        dt.date(2026, 2, 16),  # Washington's Birthday
        dt.date(2026, 4, 3),  # Good Friday
        dt.date(2026, 5, 25),  # Memorial Day
        dt.date(2026, 6, 19),  # Juneteenth, Friday
        dt.date(2026, 7, 3),  # Independence Day observed — the 4th is a Saturday
        dt.date(2026, 9, 7),  # Labor Day
        dt.date(2026, 11, 26),  # Thanksgiving
        dt.date(2026, 12, 25),  # Christmas, Friday
    ]


def test_2025_holidays_match_the_published_calendar():
    assert sorted(holidays_for_year(2025)) == [
        dt.date(2025, 1, 1),
        dt.date(2025, 1, 20),
        dt.date(2025, 2, 17),
        dt.date(2025, 4, 18),
        dt.date(2025, 5, 26),
        dt.date(2025, 6, 19),
        dt.date(2025, 7, 4),
        dt.date(2025, 9, 1),
        dt.date(2025, 11, 27),
        dt.date(2025, 12, 25),
    ]


def test_saturday_holidays_are_observed_on_the_preceding_friday():
    assert dt.date(2026, 7, 4).weekday() == 5
    assert is_market_holiday(dt.date(2026, 7, 3))
    assert not is_market_holiday(dt.date(2026, 7, 4))  # it is simply a weekend

    assert dt.date(2027, 12, 25).weekday() == 5
    assert is_market_holiday(dt.date(2027, 12, 24))


def test_sunday_holidays_are_observed_on_the_following_monday():
    assert dt.date(2027, 7, 4).weekday() == 6
    assert is_market_holiday(dt.date(2027, 7, 5))


def test_new_years_on_a_saturday_is_not_observed_on_friday():
    """NYSE's documented exception. Treating it like every other holiday
    would close the last trading day of the year, which is wrong."""
    assert dt.date(2028, 1, 1).weekday() == 5
    assert not any(d.month == 1 and d.day <= 3 for d in holidays_for_year(2028))
    assert is_trading_day(dt.date(2027, 12, 31))


def test_pre_2022_is_refused_rather_than_answered_wrongly():
    """Juneteenth was added in 2022. Applying today's rules to 2019 would
    invent a closure that never happened, so the module declines."""
    with pytest.raises(CalendarOutOfRange, match="2022"):
        holidays_for_year(2019)


def test_is_trading_day():
    assert is_trading_day(dt.date(2026, 8, 7))  # ordinary Friday
    assert not is_trading_day(dt.date(2026, 8, 8))  # Saturday
    assert not is_trading_day(dt.date(2026, 8, 9))  # Sunday
    assert not is_trading_day(dt.date(2026, 11, 26))  # Thanksgiving


def test_trading_days_between_excludes_weekends_and_holidays():
    # Wed before Thanksgiving 2026 (Nov 26) through the following Monday.
    assert trading_days_between(dt.date(2026, 11, 25), dt.date(2026, 11, 30)) == 2
    # The same span with no holiday in it.
    assert trading_days_between(dt.date(2026, 12, 2), dt.date(2026, 12, 7)) == 3


def test_trading_days_between_is_exclusive_of_the_start():
    assert trading_days_between(dt.date(2026, 8, 7), dt.date(2026, 8, 7)) == 0
    assert trading_days_between(dt.date(2026, 8, 6), dt.date(2026, 8, 7)) == 1
    assert trading_days_between(dt.date(2026, 8, 10), dt.date(2026, 8, 7)) == 0


def test_next_trading_day_skips_a_holiday_weekend():
    # Thursday 2026-11-26 is Thanksgiving; the next session is Friday.
    assert next_trading_day(dt.date(2026, 11, 25)) == dt.date(2026, 11, 27)
    # Friday 2026-12-25 is Christmas, so Thursday rolls to the next Monday.
    assert next_trading_day(dt.date(2026, 12, 24)) == dt.date(2026, 12, 28)


def test_holiday_count_is_ten_a_year():
    """A sanity net: a rule that silently stopped firing would show up here
    long before it showed up in a DTE."""
    for year in range(2022, 2035):
        assert len(holidays_for_year(year)) in (9, 10), year
