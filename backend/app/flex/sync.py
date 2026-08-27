"""§3.8 — the sync runner and its staleness guard.

**The failure mode that matters** is a silently dead sync: the token
expires, nightly runs fail for three weeks, and the dashboard keeps showing
stale numbers that look plausible. Every attempt therefore leaves a
`flex_sync_runs` row behind — including the ones that fail, which are the
rows that matter — and staleness is computed from the newest *successful*
run rather than the newest attempt.

**Window.** A trailing 30 days on every run, not "since last sync". Trades
get amended and same-day busts corrected, and a since-last-sync window
would never see them again. Idempotent upsert on the broker's identifiers
makes the overlap free.
"""

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.flex.client import SEND_REQUEST_STEP, FlexClient, redact
from app.flex.errors import ErrorClass, FlexError
from app.importer.pipeline import ImportReport, import_file
from app.market_calendar import trading_days_between
from app.models.enums import ImportSource, SourceFormat
from app.models.flex_sync_run import FlexSyncRun, SyncStatus, SyncTrigger
from app.models.import_file import ImportFile
from app.trading_day import EASTERN

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 30
#: §3.8 — the dashboard must shout when the newest success is older than this.
STALENESS_BUSINESS_DAYS = 2

_ERROR_CLASS_TO_STATUS = {
    ErrorClass.TRANSIENT: SyncStatus.TRANSIENT_FAIL,
    ErrorClass.RATE_LIMIT: SyncStatus.TRANSIENT_FAIL,
    ErrorClass.CREDENTIAL: SyncStatus.CREDENTIAL_FAIL,
    ErrorClass.CONFIGURATION: SyncStatus.CONFIG_FAIL,
    ErrorClass.REQUEST: SyncStatus.REQUEST_FAIL,
    ErrorClass.UNKNOWN: SyncStatus.REQUEST_FAIL,
}

#: Credential and configuration failures need a human, so they get the same
#: alert class rather than being buried in a warnings list (§0.6, §3.8).
ALERTING_STATUSES = {SyncStatus.CREDENTIAL_FAIL, SyncStatus.CONFIG_FAIL}


@dataclass
class SyncResult:
    run_id: int
    status: SyncStatus
    error_code: str | None = None
    error_message: str | None = None
    report: ImportReport | None = None
    needs_attention: bool = False
    #: §7.5 — the benchmark fetch outcome. Present and unsuccessful is a
    #: normal, non-alerting state: the chart shows a stale line and the run
    #: is still a success, because a dead price API is not a reason to lose
    #: a day of trade data.
    price_refresh: dict | None = None


def is_date_override_rejection(exc: FlexError) -> bool:
    """A 1003 answering an overridden SendRequest, and nothing else.

    Narrow on purpose. A 1003 from GetStatement is a statement that was
    accepted for generation and could not be handed back — retrying it
    without dates would ask a different question than the one that failed,
    and would quietly widen the window on what is probably a transient
    IBKR-side problem.
    """
    return exc.code == "1003" and exc.step == SEND_REQUEST_STEP


def broker_today() -> dt.date:
    """Today in the broker's timezone, which is the only "today" IBKR has.

    A Flex window is a pair of *broker* dates, so it has to be built from
    the Eastern calendar the statements are written against (§14.1) rather
    than from UTC. The two disagree every evening: from 20:00 ET (19:00 in
    winter) UTC has already rolled over, so a "Sync now" at 9pm computed a
    `td` of tomorrow and asked IBKR for a statement covering a day that had
    not happened yet — which is answered with 1003, statement not
    available, and reads like a broken token rather than a clock.

    The scheduled path never saw it, because 05:00 ET is a time of day when
    the two calendars agree; only the on-demand button could reach it.
    """
    return dt.datetime.now(EASTERN).date()


def business_days_between(start: dt.date, end: dt.date) -> int:
    """Trading days elapsed, exclusive of `start`.

    Counted against the market calendar rather than plain weekdays: on a
    day the market was closed there is no new statement to fetch, so
    counting a holiday toward staleness would raise a false alarm every
    Thanksgiving and every Good Friday.
    """
    return trading_days_between(start, end)


def run_sync(
    db: Session,
    client: FlexClient,
    *,
    trigger: SyncTrigger = SyncTrigger.SCHEDULED,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: dt.date | None = None,
    token: str = "",
    price_source=None,
) -> SyncResult:
    today = today or broker_today()
    window_from = today - dt.timedelta(days=window_days)

    run = FlexSyncRun(
        started_at=dt.datetime.now(dt.timezone.utc),
        trigger=trigger,
        window_from=window_from,
        window_to=today,
        status=SyncStatus.TRANSIENT_FAIL,
        attempt_count=0,
    )
    db.add(run)
    db.flush()

    used_query_period = False
    try:
        try:
            xml, reference_code, attempts = client.fetch(
                from_date=window_from.strftime("%Y%m%d"), to_date=today.strftime("%Y%m%d")
            )
        except FlexError as exc:
            if not is_date_override_rejection(exc):
                raise
            # §3.9 — the query is on a relative period and refuses the
            # override, and no Client Portal setting changes that: the
            # Period list has no custom date range in it. Ask again without
            # any date parameters and take the query's own window.
            #
            # This is a widening, never a narrowing. A query configured for
            # fewer days than `window_days` would hand back less than §3.8
            # asks for, so the shortfall is reported below rather than
            # passed off as a normal run. What it must not do is fail: a
            # window narrower than intended still catches today's trades,
            # and a dead sync catches nothing.
            logger.warning(
                "Flex query rejected the %s–%s date override (1003 at SendRequest); "
                "falling back to the query's own period",
                window_from,
                today,
            )
            xml, reference_code, attempts = client.fetch()
            used_query_period = True
    except FlexError as exc:
        # What the exchange actually cost, not a hardcoded 1: a run that
        # burned six attempts over half an hour and one that gave up
        # immediately are different failures, and this column is where that
        # difference is recorded.
        run.attempt_count = exc.attempts
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        run.status = _ERROR_CLASS_TO_STATUS.get(exc.error_class, SyncStatus.REQUEST_FAIL)
        run.error_code = exc.code
        # Redacted twice over: the client already strips the token, and this
        # value is persisted and later rendered in the UI (§3.10).
        run.error_message_redacted = redact(str(exc), token)
        db.commit()
        return SyncResult(
            run_id=run.id,
            status=run.status,
            error_code=exc.code,
            error_message=run.error_message_redacted,
            needs_attention=run.status in ALERTING_STATUSES,
        )

    run.attempt_count = attempts
    run.reference_code = reference_code

    filename = f"flex-{window_from:%Y%m%d}-{today:%Y%m%d}.xml"
    report = import_file(
        db,
        filename,
        xml.encode("utf-8"),
        source=ImportSource.FLEX_SYNC,
        source_format=SourceFormat.FLEX_XML,
    )

    if used_query_period:
        # The run asked for one window and got another, so record the one it
        # got. `flex_sync_runs.window_from/to` is the answer to "what does
        # this database actually cover", and leaving the requested window
        # there would make every fallback run a small lie — the more
        # dangerous kind, because it reads as a normal successful sync.
        covered = db.get(ImportFile, report.import_file_id)
        if covered is not None and covered.period_start and covered.period_end:
            run.window_from = covered.period_start
            run.window_to = covered.period_end
        report.warnings.append(
            "The Flex query rejected this app's date override, so the statement "
            "covers the query's own configured period"
            + (
                f" ({run.window_from} to {run.window_to})"
                if covered is not None and covered.period_start
                else ""
            )
            + f" rather than the {window_days}-day window §3.8 asks for. Widen the "
            "query's period in Client Portal if it reaches back less far than that."
        )

    # §7.5 — "Fetch on the same schedule as the Flex sync, incrementally.
    # Failures are non-fatal: log, retry next run, show the benchmark line
    # as stale rather than blanking the chart." Deliberately after the
    # import and outside its transaction boundary in spirit: the trade data
    # is already safe by the time a price provider gets a chance to fail.
    price_refresh = refresh_benchmark_prices(db, report.account_id, price_source)

    run.finished_at = dt.datetime.now(dt.timezone.utc)
    run.status = SyncStatus.SUCCESS
    run.rows_ingested = report.rows_parsed.get("executions", 0)
    run.rows_new = report.rows_inserted
    db.commit()

    return SyncResult(
        run_id=run.id, status=SyncStatus.SUCCESS, report=report, price_refresh=price_refresh
    )


def refresh_benchmark_prices(db: Session, account_id: str, source=None) -> dict | None:
    """Top up `price_bars` for the benchmark and replay the shadow portfolio.

    Returns a summary, never raises. Every failure path — no epoch, no
    adapter, provider down, adapter throwing something unexpected — lands on
    a dict describing what happened, because the caller is a sync run whose
    job is trade data and which must not fail on the benchmark's behalf.
    """
    from app.config import get_settings
    from app.models.settings_row import get_settings_row
    from app.performance.epoch import first_activity_day, resolve_epoch
    from app.performance.service import rebuild_benchmark_shadow
    from app.prices.factory import build_price_source, source_basis_note
    from app.prices.store import refresh_bars

    settings = get_settings()
    if not settings.price_fetch_enabled:
        return {"skipped": "price fetching is disabled (SETTLED_PRICE_FETCH_ENABLED)"}

    symbol = get_settings_row(db).benchmark_symbol
    epoch = resolve_epoch(db, account_id)
    start = epoch.start_date or first_activity_day(db, account_id)
    if start is None:
        return {"skipped": "no activity to benchmark against yet"}

    try:
        source = source or build_price_source()
    except Exception as exc:  # noqa: BLE001 — configuration must not fail a sync
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # Market days here too, for the same reason as the Flex window above:
    # `date.today()` is the container's clock, which in a UTC container is
    # already tomorrow for the last four hours of every trading day.
    today = broker_today()
    result = refresh_bars(db, source, symbol, start, today)
    shadow = rebuild_benchmark_shadow(db, account_id, symbol)

    # §9.5 / §12 — the underlyings of open option positions, so Expiry Watch
    # can show a distance to strike for contracts whose underlying is not
    # itself held. A handful of extra symbols on the same schedule; each is
    # independently non-fatal, because a benchmark that works and one
    # underlying that does not is still a working dashboard.
    underlyings = _open_option_underlyings(db, account_id)
    underlying_results = {}
    for extra in sorted(underlyings - {symbol}):
        extra_result = refresh_bars(db, source, extra, today - dt.timedelta(days=10), today)
        underlying_results[extra] = {
            "ok": extra_result.ok,
            "bars_written": extra_result.bars_written,
            "error": extra_result.error,
        }
    db.flush()

    return {
        "ok": result.ok,
        "symbol": result.symbol,
        "source": result.source,
        "bars_written": result.bars_written,
        "latest_cached": result.latest_cached.isoformat() if result.latest_cached else None,
        "error": result.error,
        "basis_note": source_basis_note(source),
        "shadow": shadow,
        "underlyings": underlying_results,
    }


def _open_option_underlyings(db: Session, account_id: str) -> set[str]:
    """Root symbols of the underlyings behind currently open option legs.

    Equities only. A futures underlying (MESU1) is not a symbol any equity
    price provider knows, and guessing one would cache a wrong price under
    a right-looking name — the §7.5 argument for source independence
    applies just as much to what gets asked for.
    """
    from app.importer.reconciliation import compute_open_positions
    from app.models.enums import AssetClass
    from app.models.instrument import Instrument

    symbols: set[str] = set()
    for instrument_id, position in compute_open_positions(db, account_id).items():
        if abs(position.quantity) < 1e-9:
            continue
        instrument = db.get(Instrument, instrument_id)
        if instrument is None or instrument.asset_class != AssetClass.OPT:
            continue
        root = (instrument.underlying_root or instrument.underlying_symbol or "").strip().upper()
        if root:
            symbols.add(root)
    return symbols


@dataclass
class SyncHealth:
    last_success_at: dt.datetime | None
    last_attempt_at: dt.datetime | None
    last_status: str | None
    last_error_code: str | None
    last_error_message: str | None
    business_days_since_success: int | None
    is_stale: bool
    needs_attention: bool
    scheduler_running: bool = False
    next_run_at: dt.datetime | None = None

    def as_dict(self) -> dict:
        return {
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "last_status": self.last_status,
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "business_days_since_success": self.business_days_since_success,
            "is_stale": self.is_stale,
            "needs_attention": self.needs_attention,
            "staleness_threshold_business_days": STALENESS_BUSINESS_DAYS,
            # A scheduler that is not running is itself worth surfacing:
            # it is the difference between "no sync yet" and "no sync ever,
            # because nothing is scheduled to do one" (§3.8).
            "scheduler_running": self.scheduler_running,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
        }


def sync_health(db: Session, now: dt.datetime | None = None) -> SyncHealth:
    # Imported here rather than at module scope: the scheduler imports
    # run_sync from this module, and a top-level import would be circular.
    from app.flex.scheduler import next_effective_run_at, next_run_at as scheduler_next_run

    now = now or dt.datetime.now(dt.timezone.utc)

    last_success = (
        db.query(FlexSyncRun)
        .filter(FlexSyncRun.status == SyncStatus.SUCCESS)
        .order_by(FlexSyncRun.started_at.desc())
        .first()
    )
    last_attempt = db.query(FlexSyncRun).order_by(FlexSyncRun.started_at.desc()).first()

    days = None
    if last_success is not None:
        days = business_days_between(last_success.started_at.date(), now.date())

    # No successful sync ever is stale by definition — otherwise a brand-new
    # install that has never worked looks identical to a healthy one.
    is_stale = last_attempt is not None and (last_success is None or (days or 0) > STALENESS_BUSINESS_DAYS)

    return SyncHealth(
        last_success_at=last_success.started_at if last_success else None,
        last_attempt_at=last_attempt.started_at if last_attempt else None,
        last_status=last_attempt.status.value if last_attempt else None,
        last_error_code=last_attempt.error_code if last_attempt else None,
        last_error_message=last_attempt.error_message_redacted if last_attempt else None,
        business_days_since_success=days,
        is_stale=is_stale,
        needs_attention=bool(last_attempt and last_attempt.status in ALERTING_STATUSES),
        scheduler_running=scheduler_next_run() is not None,
        next_run_at=next_effective_run_at(),
    )
