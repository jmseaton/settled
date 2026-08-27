"""§3.8 — the daily schedule, the trading-day guard, and the advisory lock."""

import datetime as dt

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.flex.scheduler import (
    ADVISORY_LOCK_KEY,
    SYNC_HOUR_ET,
    advisory_lock,
    run_scheduled_sync,
    scheduled_next_run,
    should_run_today,
    start_scheduler,
)
from app.flex.sync import sync_health
from app.models import FlexSyncRun
from app.trading_day import EASTERN


def test_schedule_is_0500_eastern():
    """§3.8 — IB's overnight settlement must finish before the statement is
    final, which is why the hour is 05:00 rather than midnight."""
    assert SYNC_HOUR_ET == 5
    after = dt.datetime(2026, 8, 5, 12, 0, tzinfo=EASTERN)
    nxt = scheduled_next_run(after)
    assert nxt.astimezone(EASTERN).hour == 5
    assert nxt.astimezone(EASTERN).date() == dt.date(2026, 8, 6)


def test_schedule_holds_0500_eastern_across_dst():
    """A fixed UTC offset would drift an hour twice a year and start firing
    before IB's processing completes."""
    trigger = CronTrigger(hour=SYNC_HOUR_ET, minute=0, timezone=EASTERN)

    summer = trigger.get_next_fire_time(None, dt.datetime(2026, 7, 1, 12, tzinfo=EASTERN))
    winter = trigger.get_next_fire_time(None, dt.datetime(2026, 1, 5, 12, tzinfo=EASTERN))

    assert summer.astimezone(EASTERN).hour == 5
    assert winter.astimezone(EASTERN).hour == 5
    # Same local hour, different UTC hour — which is the whole point.
    assert summer.astimezone(dt.timezone.utc).hour != winter.astimezone(dt.timezone.utc).hour


def test_should_run_today_skips_weekends_and_holidays():
    assert should_run_today(dt.date(2026, 8, 7))  # Friday
    assert not should_run_today(dt.date(2026, 8, 8))  # Saturday
    assert not should_run_today(dt.date(2026, 11, 26))  # Thanksgiving
    assert not should_run_today(dt.date(2026, 7, 3))  # Independence Day observed


def test_non_trading_day_is_skipped_without_recording_a_failed_run(db):
    """A skipped holiday is not a failure and must not pollute the run
    history the staleness banner reads."""
    result = run_scheduled_sync(now=dt.datetime(2026, 11, 26, 5, 0, tzinfo=EASTERN))
    assert result == "skipped_non_trading_day"
    assert db.query(FlexSyncRun).count() == 0


def test_missing_credentials_skip_rather_than_fail(db):
    settings = get_settings()
    assert not settings.flex_token, "tests must not carry real credentials"

    result = run_scheduled_sync(now=dt.datetime(2026, 8, 7, 5, 0, tzinfo=EASTERN))
    assert result == "skipped_no_credentials"
    assert db.query(FlexSyncRun).count() == 0


def test_scheduler_does_not_start_without_credentials():
    """An upload-only deployment should have no scheduler at all, rather
    than a job that fails every morning."""
    assert start_scheduler() is None


def test_scheduler_respects_the_disable_switch(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "flex_token", "t", raising=False)
    monkeypatch.setattr(settings, "flex_query_id", "q", raising=False)
    monkeypatch.setattr(settings, "scheduler_enabled", False, raising=False)
    assert start_scheduler() is None


# ------------------------------------------------------------ advisory lock
def test_advisory_lock_is_granted_once_at_a_time():
    """Two uvicorn workers means two schedulers. Without this, both would
    sync at once against a token limited to one request per second."""
    with advisory_lock() as first:
        assert first is True
        with advisory_lock() as second:
            assert second is False, "a second holder must be refused, not queued"


def test_advisory_lock_is_released_afterwards():
    with advisory_lock() as acquired:
        assert acquired
    # Released, so the next caller gets it.
    with advisory_lock() as acquired_again:
        assert acquired_again


def test_advisory_lock_is_released_even_when_the_body_raises():
    with pytest.raises(RuntimeError):
        with advisory_lock() as acquired:
            assert acquired
            raise RuntimeError("boom")

    with advisory_lock() as acquired_again:
        assert acquired_again, "a crashed sync must not wedge the lock forever"


def test_a_locked_out_run_skips_cleanly(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "flex_token", "token", raising=False)
    monkeypatch.setattr(settings, "flex_query_id", "query", raising=False)

    with advisory_lock():
        result = run_scheduled_sync(now=dt.datetime(2026, 8, 7, 5, 0, tzinfo=EASTERN))

    assert result == "skipped_locked"
    assert db.query(FlexSyncRun).count() == 0, "a skipped run is not an attempt"


def test_lock_key_is_stable():
    """Two processes must choose the same number or the lock means nothing."""
    assert ADVISORY_LOCK_KEY == 0x5E77_1ED0


def test_next_effective_run_skips_a_fire_that_would_do_nothing(db, monkeypatch):
    """The trigger fires daily and the body skips non-trading days, so the
    raw next-fire time is often a Sunday. Reporting that answers the wrong
    question — the user wants to know when data will next refresh."""
    settings = get_settings()
    monkeypatch.setattr(settings, "flex_token", "t", raising=False)
    monkeypatch.setattr(settings, "flex_query_id", "q", raising=False)
    monkeypatch.setattr(settings, "scheduler_enabled", True, raising=False)

    from app.flex.scheduler import next_effective_run_at, next_run_at, shutdown_scheduler

    try:
        assert start_scheduler() is not None
        raw, effective = next_run_at(), next_effective_run_at()
        assert raw is not None and effective is not None
        assert should_run_today(effective.astimezone(EASTERN).date())
        assert effective >= raw
    finally:
        shutdown_scheduler()


# ------------------------------------------------------------------ health
def test_health_reports_that_no_scheduler_is_running(db):
    """Distinguishes 'no sync yet' from 'nothing is scheduled to ever do
    one' — otherwise an unconfigured install looks merely idle."""
    health = sync_health(db).as_dict()
    assert health["scheduler_running"] is False
    assert health["next_run_at"] is None
