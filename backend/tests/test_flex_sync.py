"""§3.8 — sync runner, run history, and the staleness guard (§16.1 test 24)."""

import datetime as dt
from unittest import mock

import httpx
import pytest

from app.flex.client import FlexClient
from app.flex.sync import (
    STALENESS_BUSINESS_DAYS,
    business_days_between,
    run_sync,
    sync_health,
)
from app.models import Execution, FlexSyncRun, ImportFile, SyncStatus, SyncTrigger
from app.models.enums import ImportSource
from app.trading_day import EASTERN
from tests.conftest import FIXTURES

TOKEN = "SECRET_TOKEN_123456"

SEND_OK = """<FlexStatementResponse><Status>Success</Status>
<ReferenceCode>REF12345</ReferenceCode>
<Url>https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url>
</FlexStatementResponse>"""


def _fail(code: str) -> str:
    return (
        f"<FlexStatementResponse><Status>Fail</Status><ErrorCode>{code}</ErrorCode>"
        f"<ErrorMessage>failed for token {TOKEN}</ErrorMessage></FlexStatementResponse>"
    )


def _client(responses):
    remaining = list(responses)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=remaining.pop(0) if remaining else "")

    client = FlexClient(
        TOKEN, "999888", transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    client._requests = requests  # type: ignore[attr-defined]
    return client


def _statement() -> str:
    return (FIXTURES / "fixture-flex.xml").read_text()


def test_successful_sync_ingests_and_records_the_run(db):
    with _client([SEND_OK, _statement()]) as client:
        result = run_sync(db, client, trigger=SyncTrigger.MANUAL, token=TOKEN)

    assert result.status == SyncStatus.SUCCESS
    assert result.report.rows_inserted == 78

    run = db.query(FlexSyncRun).one()
    assert run.status == SyncStatus.SUCCESS
    assert run.trigger == SyncTrigger.MANUAL
    assert run.rows_ingested == 78
    assert run.rows_new == 78
    assert run.reference_code == "REF12345"
    assert run.finished_at is not None

    # The payload is registered exactly as an upload would be (§4.1), so a
    # rebuild from disk is possible regardless of how the data arrived.
    import_record = db.query(ImportFile).one()
    assert import_record.source == ImportSource.FLEX_SYNC
    assert db.query(Execution).count() == 78


def test_sync_requests_a_trailing_30_day_window(db):
    """§3.8 — a trailing window, not 'since last sync': trades get amended
    and a since-last-sync window would never see the correction."""
    today = dt.date(2031, 8, 8)
    with _client([SEND_OK, _statement()]) as client:
        run_sync(db, client, today=today, token=TOKEN)
        params = client._requests[0].url.params

    assert params["fd"] == "20310709"
    assert params["td"] == "20310808"

    run = db.query(FlexSyncRun).one()
    assert run.window_from == dt.date(2031, 7, 9)
    assert run.window_to == today


def test_resync_over_an_overlapping_window_inserts_nothing_new(db):
    with _client([SEND_OK, _statement()]) as client:
        run_sync(db, client, token=TOKEN)
    with _client([SEND_OK, _statement()]) as client:
        second = run_sync(db, client, token=TOKEN)

    assert second.status == SyncStatus.SUCCESS
    assert second.report.rows_inserted == 0
    assert second.report.rows_duplicate == 78
    assert db.query(Execution).count() == 78
    assert db.query(FlexSyncRun).count() == 2


@pytest.mark.parametrize(
    "code,expected",
    [
        ("1012", SyncStatus.CREDENTIAL_FAIL),
        ("1015", SyncStatus.CREDENTIAL_FAIL),
        ("1013", SyncStatus.CREDENTIAL_FAIL),
        ("1014", SyncStatus.CONFIG_FAIL),
        ("1016", SyncStatus.CONFIG_FAIL),
    ],
)
def test_failures_are_classified_and_recorded(db, code, expected):
    """A failed attempt must leave a row behind — those are precisely the
    runs that explain a stale dashboard.

    1003 is deliberately not in this list: at SendRequest it is answered by
    the fallback rather than by a failed run, and the case that does fail —
    a 1003 from GetStatement — is pinned separately below.
    """
    with _client([_fail(code)]) as client:
        result = run_sync(db, client, token=TOKEN)

    assert result.status == expected
    run = db.query(FlexSyncRun).one()
    assert run.status == expected
    assert run.error_code == code
    assert run.finished_at is not None


def test_a_request_failure_records_both_of_its_attempts(db):
    """§3.8 — "restart from SendRequest once, then give up for this run".

    The count is the point: a stored `attempt_count` of 1 on a 1003 means
    the restart never happened, which is exactly the bug this pins. Driven
    from GetStatement, which is the 1003 that still reaches a failed run
    now that the SendRequest one falls back instead.
    """
    with _client([SEND_OK, _fail("1003"), SEND_OK, _fail("1003")]) as client:
        run_sync(db, client, token=TOKEN)

    run = db.query(FlexSyncRun).one()
    assert run.attempt_count == 2
    assert run.error_code == "1003"


def test_an_override_rejection_falls_back_to_the_querys_own_period(db):
    """§3.9 — a query on a relative period refuses every fd/td override, and
    no Client Portal setting changes that. The sync asks again with no date
    parameters rather than failing, because a window narrower than intended
    still catches today's trades and a dead sync catches nothing."""
    with _client([_fail("1003"), SEND_OK, _statement()]) as client:
        result = run_sync(db, client, token=TOKEN)
        first = client._requests[0].url.params
        last = client._requests[-2].url.params

    assert result.status == SyncStatus.SUCCESS
    assert "fd" in first, "the designed window is still what it asks for first"
    assert "fd" not in last and "td" not in last and "p" not in last
    assert db.query(Execution).count() == 78


def test_the_fallback_records_the_window_it_actually_got(db):
    """Not the one it asked for. `window_from/to` answers "what does this
    database cover", and a fallback run that reported the requested window
    would read as an ordinary success while covering something else."""
    with _client([_fail("1003"), SEND_OK, _statement()]) as client:
        result = run_sync(db, client, today=dt.date(2031, 8, 11), token=TOKEN)

    run = db.query(FlexSyncRun).one()
    covered = db.query(ImportFile).one()
    assert run.window_from == covered.period_start
    assert run.window_to == covered.period_end
    assert run.window_from != dt.date(2031, 7, 12), "not the requested window"
    assert any("date override" in w for w in result.report.warnings)


def test_a_1003_from_get_statement_does_not_trigger_the_fallback(db):
    """A statement that was built and could not be handed back is a
    different event. Retrying it without dates would ask a different
    question than the one that failed, and silently widen the window."""
    with _client([SEND_OK, _fail("1003"), SEND_OK, _fail("1003")]) as client:
        result = run_sync(db, client, token=TOKEN)

    assert result.status == SyncStatus.REQUEST_FAIL
    assert result.error_code == "1003"
    assert db.query(Execution).count() == 0


def test_a_credential_failure_records_its_single_attempt(db):
    with _client([_fail("1012")]) as client:
        run_sync(db, client, token=TOKEN)
    assert db.query(FlexSyncRun).one().attempt_count == 1


def test_the_window_is_built_from_the_eastern_day_not_utc(db):
    """The window is a pair of *broker* dates (§14.1).

    Frozen at 21:30 ET, when UTC has already rolled into tomorrow: asking
    IBKR for a `td` that has not happened yet is answered with 1003, and
    only the on-demand path can reach that hour.
    """
    import app.flex.sync as sync_module

    evening = dt.datetime(2031, 8, 11, 21, 30, tzinfo=EASTERN)
    assert evening.astimezone(dt.timezone.utc).date() == dt.date(2031, 8, 12)

    with mock.patch.object(sync_module, "broker_today", return_value=evening.date()):
        with _client([SEND_OK, _statement()]) as client:
            run_sync(db, client, token=TOKEN)
            params = client._requests[0].url.params

    assert params["td"] == "20310811"
    assert db.query(FlexSyncRun).one().window_to == dt.date(2031, 8, 11)


def test_broker_today_follows_eastern():
    from app.flex.sync import broker_today

    assert broker_today() == dt.datetime.now(EASTERN).date()


def test_credential_failure_demands_attention(db):
    with _client([_fail("1012")]) as client:
        result = run_sync(db, client, token=TOKEN)
    assert result.needs_attention is True
    assert sync_health(db).needs_attention is True


def test_transient_failure_does_not_demand_attention(db):
    with _client([_fail("1005")] * 8) as client:
        result = run_sync(db, client, token=TOKEN)
    assert result.status == SyncStatus.TRANSIENT_FAIL
    assert result.needs_attention is False


def test_token_is_never_persisted_in_a_stored_error(db):
    """§3.10 — the stored message is rendered in the UI later, so it is
    redacted before it reaches the database, not on the way out."""
    with _client([_fail("1012")]) as client:
        run_sync(db, client, token=TOKEN)

    run = db.query(FlexSyncRun).one()
    assert TOKEN not in (run.error_message_redacted or "")
    assert "***REDACTED***" in run.error_message_redacted


# ------------------------------------------------------- test 24: staleness
def test_business_days_between_skips_weekends():
    # Friday -> Monday is one business day, not three.
    assert business_days_between(dt.date(2031, 8, 8), dt.date(2031, 8, 11)) == 1
    assert business_days_between(dt.date(2031, 8, 4), dt.date(2031, 8, 8)) == 4
    assert business_days_between(dt.date(2031, 8, 8), dt.date(2031, 8, 8)) == 0


def test_24_staleness_banner_after_two_business_days(db):
    with _client([SEND_OK, _statement()]) as client:
        run_sync(db, client, token=TOKEN)

    run = db.query(FlexSyncRun).one()
    run.started_at = dt.datetime(2031, 8, 4, 9, 0, tzinfo=dt.timezone.utc)  # Monday
    db.flush()

    fresh = sync_health(db, now=dt.datetime(2031, 8, 6, 9, 0, tzinfo=dt.timezone.utc))
    assert fresh.business_days_since_success == 2
    assert fresh.is_stale is False

    stale = sync_health(db, now=dt.datetime(2031, 8, 7, 9, 0, tzinfo=dt.timezone.utc))
    assert stale.business_days_since_success == 3
    assert stale.is_stale is True
    assert stale.as_dict()["staleness_threshold_business_days"] == STALENESS_BUSINESS_DAYS


def test_a_weekend_alone_never_triggers_staleness(db):
    """A Friday sync read on Monday morning is healthy. Counting calendar
    days here would cry wolf every single weekend."""
    with _client([SEND_OK, _statement()]) as client:
        run_sync(db, client, token=TOKEN)

    run = db.query(FlexSyncRun).one()
    run.started_at = dt.datetime(2031, 8, 8, 22, 0, tzinfo=dt.timezone.utc)  # Friday
    db.flush()

    monday = sync_health(db, now=dt.datetime(2031, 8, 11, 12, 0, tzinfo=dt.timezone.utc))
    assert monday.business_days_since_success == 1
    assert monday.is_stale is False


def test_failures_after_a_success_still_report_stale(db):
    """The exact failure §3.8 exists to prevent: the token expires, runs
    keep failing, and the dashboard keeps showing plausible stale numbers."""
    with _client([SEND_OK, _statement()]) as client:
        run_sync(db, client, token=TOKEN)
    success = db.query(FlexSyncRun).one()
    success.started_at = dt.datetime(2031, 7, 21, 9, 0, tzinfo=dt.timezone.utc)
    db.flush()

    for _ in range(3):
        with _client([_fail("1012")]) as client:
            run_sync(db, client, token=TOKEN)

    # `run_sync` stamps `started_at` from the clock, so without pinning these
    # the failures sort *before* the back-dated success and `last_attempt`
    # picks the success — the health report then looks fine while the token
    # is dead, which is the exact condition this test exists to catch.
    for offset, failed in enumerate(
        db.query(FlexSyncRun).filter(FlexSyncRun.id != success.id).order_by(FlexSyncRun.id).all()
    ):
        failed.started_at = dt.datetime(2031, 8, 5 + offset, 9, 0, tzinfo=dt.timezone.utc)
    db.flush()

    health = sync_health(db, now=dt.datetime(2031, 8, 8, 9, 0, tzinfo=dt.timezone.utc))
    assert health.is_stale is True
    assert health.needs_attention is True
    assert health.last_status == "CREDENTIAL_FAIL"
    assert health.last_error_code == "1012"
    # The last *success* is what staleness is measured from, not the last
    # attempt — which is recent, and failing.
    assert health.last_success_at.date() == dt.date(2031, 7, 21)
    assert health.last_attempt_at.date() != dt.date(2031, 7, 21)


def test_never_synced_is_reported_as_stale_not_healthy(db):
    """An install that has never synced successfully must not look identical
    to a healthy one."""
    assert sync_health(db).is_stale is False, "no attempts at all is not yet stale"

    with _client([_fail("1015")]) as client:
        run_sync(db, client, token=TOKEN)

    health = sync_health(db)
    assert health.last_success_at is None
    assert health.is_stale is True
    assert health.needs_attention is True
