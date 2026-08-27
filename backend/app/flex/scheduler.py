"""§3.8 — the daily sync schedule.

Cadence is once daily at 05:00 ET. The hour matters: IB's overnight
settlement and P&L processing must finish before the statement is final,
and several of the transient error codes in §3.8 exist precisely because it
often isn't ready earlier.

Days the market was closed are skipped. Nothing is lost by skipping —
because the window is a trailing 30 days (§3.8), Monday's run covers the
whole weekend automatically, including Sunday-evening futures sessions.

**Why an advisory lock.** APScheduler ticks inside the application process,
so running two uvicorn workers would give you two schedulers and two
simultaneous syncs against a token limited to one request per second. A
Postgres advisory lock makes the second one step aside. It is also the
right shape if the app is ever run as two containers against one database.

The schedule is timezone-aware rather than a fixed UTC offset, so it stays
at 05:00 Eastern across DST rather than drifting by an hour twice a year.
"""

import datetime as dt
import logging
from contextlib import contextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.flex.client import FlexClient
from app.flex.sync import run_sync
from app.market_calendar import is_trading_day
from app.models.flex_sync_run import SyncTrigger
from app.trading_day import EASTERN

logger = logging.getLogger(__name__)

JOB_ID = "daily_flex_sync"
#: Arbitrary but fixed: any process using this database must pick the same
#: number for the lock to mean anything.
ADVISORY_LOCK_KEY = 0x5E77_1ED0

SYNC_HOUR_ET = 5
SYNC_MINUTE_ET = 0

_scheduler: BackgroundScheduler | None = None


@contextmanager
def advisory_lock(key: int = ADVISORY_LOCK_KEY):
    """Yields True if this process took the lock, False if another holds it.

    Uses a dedicated connection: the lock is session-scoped, so it must not
    ride on a pooled connection that might be returned mid-job.
    """
    connection = engine.connect()
    acquired = False
    try:
        acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        yield acquired
    finally:
        if acquired:
            connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            connection.commit()
        connection.close()


def should_run_today(today: dt.date) -> bool:
    return is_trading_day(today)


def run_scheduled_sync(now: dt.datetime | None = None) -> str:
    """The job body. Returns a short reason string, which is what the tests
    assert on and what gets logged."""
    now = now or dt.datetime.now(EASTERN)
    today = now.astimezone(EASTERN).date()

    if not should_run_today(today):
        logger.info("Skipping sync on %s: not a trading day", today)
        return "skipped_non_trading_day"

    settings = get_settings()
    if not settings.flex_token or not settings.flex_query_id:
        logger.warning("Skipping sync: Flex credentials are not configured")
        return "skipped_no_credentials"

    with advisory_lock() as acquired:
        if not acquired:
            logger.info("Skipping sync: another process holds the sync lock")
            return "skipped_locked"

        db = SessionLocal()
        try:
            with FlexClient(settings.flex_token, settings.flex_query_id) as client:
                result = run_sync(
                    db, client, trigger=SyncTrigger.SCHEDULED, token=settings.flex_token
                )
            logger.info("Scheduled sync finished: %s", result.status.value)
            return f"ran_{result.status.value.lower()}"
        finally:
            db.close()


def start_scheduler() -> BackgroundScheduler | None:
    """Started from the app lifespan. Returns None when disabled or
    unconfigured, so a deployment without Flex credentials simply has no
    scheduler rather than a job that fails every morning."""
    global _scheduler

    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled by configuration")
        return None
    if not settings.flex_token or not settings.flex_query_id:
        logger.info("Scheduler not started: Flex credentials are not configured")
        return None
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=EASTERN)
    _scheduler.add_job(
        run_scheduled_sync,
        CronTrigger(hour=SYNC_HOUR_ET, minute=SYNC_MINUTE_ET, timezone=EASTERN),
        id=JOB_ID,
        # A missed run (laptop asleep, container restarting) should still
        # fire rather than be dropped, and overlapping runs coalesce into
        # one — the window is trailing, so a single catch-up covers the gap.
        misfire_grace_time=6 * 60 * 60,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started; next run at %s", next_run_at())
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_run_at() -> dt.datetime | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(JOB_ID)
    return job.next_run_time if job else None


def next_effective_run_at() -> dt.datetime | None:
    """The next fire that will actually sync, rather than the next tick.

    The trigger fires every day and the job body skips non-trading days, so
    the raw next-fire time is often a Sunday that will do nothing. Showing
    that to a user answers the wrong question: what they want to know is
    when the data will next refresh.
    """
    fires_at = next_run_at()
    if fires_at is None:
        return None
    cursor = fires_at
    while not is_trading_day(cursor.astimezone(EASTERN).date()):
        cursor += dt.timedelta(days=1)
    return cursor


def scheduled_next_run(after: dt.datetime) -> dt.datetime:
    """What the schedule *would* produce, independent of a running
    scheduler — so the UI can state the cadence even when no scheduler is
    running (no credentials, or a deployment driving sync by cron)."""
    trigger = CronTrigger(hour=SYNC_HOUR_ET, minute=SYNC_MINUTE_ET, timezone=EASTERN)
    return trigger.get_next_fire_time(None, after.astimezone(EASTERN))
