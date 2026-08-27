"""§3.7 — Flex Web Service client.

Two-step and asynchronous: request generation, then poll for the result.

Three details that trip up nearly every first implementation, all enforced
here rather than left to the caller:

  * A ``User-Agent`` naming the technology and version is **required**;
    requests without it are rejected. `httpx` sets its own default, so it
    must be set explicitly.
  * ``v=3`` on both calls. Omitting the version silently falls back to
    Version 2, which has a different response shape.
  * The GetStatement endpoint comes from the ``<url>`` in the SendRequest
    response, never hardcoded — IBKR has moved these hosts before, and older
    forms still circulate in sample code.

The token is a bearer credential travelling as a **query parameter**, so it
would otherwise land in any proxy or webserver access log. It is redacted
from every exception, log line, and stored error message (§3.10).
"""

import logging
import re
import time
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

from app.flex.errors import ErrorClass, FlexError, attempt_budget, classify

SEND_REQUEST_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
logger = logging.getLogger(__name__)

FLEX_VERSION = "3"
DEFAULT_USER_AGENT = "Settled/0.1 (Python/3.11; httpx)"

#: §3.8 — hard-throttle well under one request per second and ten per minute.
MIN_REQUEST_INTERVAL_SECONDS = 1.5
#: Generation typically takes a few seconds; hammering it just triggers 1018.
FIRST_POLL_DELAY_SECONDS = 3.0
MAX_ATTEMPTS = 6

#: Enough of an unparseable body to identify what it is, short enough that a
#: whole error page does not land in a log line or an error banner.
_EXCERPT_CHARS = 200

#: §3.7 — the two steps, named, so a failure can say which one it came from.
SEND_REQUEST_STEP = "SendRequest"
GET_STATEMENT_STEP = "GetStatement"

#: §3.9 — the one Flex failure with an overwhelmingly likely single cause.
#:
#: A 1003 at *SendRequest* is not the same event as a 1003 at GetStatement.
#: GetStatement has a reference code in hand, so its 1003 is about a
#: statement that was accepted for generation and could not be handed back.
#: SendRequest's 1003 arrives before any statement exists, and when the
#: request carried `fd`/`td` it means IBKR declined to build one *for those
#: dates* — which in practice means the query's Period does not accept an
#: override at all, not that anything is wrong with the dates themselves.
#: The tell is that the same query succeeds with no override, which is
#: exactly what `python -m app.flex.diagnose` sets out to establish.
#:
#: There is no query setting that fixes this. Client Portal's Period list
#: offers only relative periods — Last Business Day/Week/Month/Quarter, Last
#: 30/365/N Calendar Days, Month/Quarter/Year to Date — and no custom date
#: range at all, so a query that rejects `fd`/`td` cannot be reconfigured to
#: accept it. The sync falls back to the query's own period instead (§3.8);
#: the single-day epoch fetch (§0.3) has no fallback available and the NAV
#: has to be typed.
DATE_OVERRIDE_REJECTED_HINT = (
    "this request carried a date override (fd={fd}&td={td}) and IBKR declined to "
    "build a statement for it, before any statement existed. The dates are "
    "rarely the problem: a query on a relative period answers 1003 for every "
    "override, however reasonable, and Client Portal offers no period that "
    "accepts one. Confirm with `python -m app.flex.diagnose` — if the same "
    "query succeeds with no override and fails with one, this is that (§3.9)"
)

#: What a GetStatement response is allowed to be. `FlexQueryResponse` is the
#: statement itself; `FlexStatementResponse` is the envelope an in-band
#: failure arrives in, which the status check above handles.
STATEMENT_ROOTS = {"FlexQueryResponse", "FlexStatementResponse"}


def redact(text: str, *secrets: str) -> str:
    """Strip tokens from anything that might reach a log or the UI."""
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***REDACTED***")
    # Also catch a token embedded in a URL we did not construct ourselves.
    return re.sub(r"([?&]t=)[^&\s]+", r"\1***REDACTED***", out)


def describe_unexpected_body(body: str, token: str) -> str:
    """What did IBKR actually send?

    The parse error alone ("not well-formed, line 1, column 5") names the
    symptom and nothing else, which leaves the only useful evidence — the
    body — discarded at the moment it is needed. A CSV-configured query, an
    HTML maintenance page and a truncated statement all produce much the
    same ParseError and want completely different responses from a human.
    """
    stripped = body.strip()
    if not stripped:
        return "the response body was empty"

    # IBKR's Flex CSV opens with a BOF ("beginning of file") record. This is
    # a configuration mistake rather than a fault, and by far the most likely
    # reason a working token and a valid query still yield something
    # unreadable: the query's Format is a separate setting from its period,
    # and nothing about the request says which one you will get. Both calls
    # answer 200 and look healthy. Naming the setting is the whole fix.
    if stripped.startswith('"BOF"') or stripped.startswith("BOF,"):
        return (
            "the query's CSV/text output rather than XML. The Flex query's Format "
            "setting is delivering text; set it to XML in Client Portal → "
            "Performance & Reports → Flex Queries → edit the query → Format. "
            "This app parses Flex XML only (§3.9)"
        )

    head = stripped[:_EXCERPT_CHARS].lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        kind = "an HTML page rather than XML (usually a maintenance window or a rejected request)"
    elif not stripped.startswith("<"):
        kind = "a non-XML body"
    else:
        kind = "XML that does not parse (possibly truncated)"

    return f"{kind}; body began: {body_excerpt(body, token)}"


def body_excerpt(body: str, token: str) -> str:
    """A quotable, redacted, length-capped opening of a response body.

    Separate from `describe_unexpected_body` because not every body worth
    quoting is malformed: a response can parse perfectly and still not be
    the document expected, and calling that one "XML that does not parse"
    would be a confident, wrong answer.
    """
    stripped = " ".join(body.strip()[:_EXCERPT_CHARS].split())
    if len(body.strip()) > _EXCERPT_CHARS:
        stripped += "…"
    return repr(redact(stripped, token))


def alternate_statement_url(statement_url: str) -> str | None:
    """The same GetStatement path, served from the SendRequest host.

    Returns None when the returned URL already points there, so there is
    nothing to fall back to.
    """
    returned = httpx.URL(statement_url)
    base = httpx.URL(SEND_REQUEST_URL)
    if returned.host == base.host:
        return None
    return str(returned.copy_with(scheme=base.scheme, host=base.host, port=base.port))


@dataclass
class FlexResponse:
    reference_code: str
    statement_url: str


class FlexClient:
    def __init__(
        self,
        token: str,
        query_id: str,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        timeout: float = 30.0,
    ):
        if not user_agent or not user_agent.strip():
            # Non-optional: a request without it is rejected by IBKR, and
            # failing here gives a clear cause instead of an opaque rejection.
            raise ValueError(
                "A User-Agent naming the technology and version is required by the Flex Web Service"
            )
        self._token = token
        self._query_id = query_id
        self._user_agent = user_agent
        self._sleep = sleep
        self._client = httpx.Client(
            headers={"User-Agent": user_agent}, transport=transport, timeout=timeout
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < MIN_REQUEST_INTERVAL_SECONDS:
            self._sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, url: str, params: dict, step: str) -> str:
        self._throttle()
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            # Name the host. The two steps do not talk to the same one —
            # SendRequest goes to the hardcoded `ndcdyn`, GetStatement to
            # whatever `<Url>` came back, in practice `gdcdyn` — so a
            # resolver or firewall that handles one and not the other
            # produces a transport error that is impossible to place from
            # the message alone. "Temporary failure in name resolution" does
            # not say what failed to resolve, and the code does not say
            # either, because that host was chosen by IBKR at runtime.
            raise FlexError(
                None,
                f"{redact(str(exc), self._token)} (host {httpx.URL(url).host}, {step})",
                ErrorClass.TRANSIENT,
                step=step,
            ) from exc
        response.raise_for_status()
        return response.text

    @staticmethod
    def _raise_for_status_element(xml: str, token: str, step: str) -> ET.Element:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            detail = describe_unexpected_body(xml, token)
            raise FlexError(
                None,
                f"Unparseable response: {redact(str(exc), token)} — {detail}",
                ErrorClass.UNKNOWN,
                step=step,
            ) from exc

        status = (root.findtext("Status") or root.findtext(".//Status") or "").strip()
        # Any status that is present and is not Success. Checking only for
        # the literal "Fail" left every other value falling through to be
        # read as a statement, taking its ErrorCode and ErrorMessage with
        # it — so an envelope that said exactly what was wrong surfaced
        # later as a generic complaint about a missing ReferenceCode, with
        # the code IBKR sent discarded. A successful GetStatement carries no
        # Status element at all, so an absent one still means a statement.
        if status and status.lower() != "success":
            code = (root.findtext("ErrorCode") or root.findtext(".//ErrorCode") or "").strip() or None
            message = (root.findtext("ErrorMessage") or root.findtext(".//ErrorMessage") or "").strip()
            raise FlexError(
                code,
                redact(message or f"IBKR reported <Status>{status}</Status>", token),
                classify(code),
                step=step,
            )
        return root

    def send_request(self, *, from_date: str | None = None, to_date: str | None = None, period: str | None = None) -> FlexResponse:
        params = {"t": self._token, "q": self._query_id, "v": FLEX_VERSION}
        overrode_dates = bool(from_date and to_date)
        if overrode_dates:
            params["fd"] = from_date
            params["td"] = to_date
        elif period:
            params["p"] = period

        body = self._get(SEND_REQUEST_URL, params, SEND_REQUEST_STEP)
        try:
            root = self._raise_for_status_element(body, self._token, SEND_REQUEST_STEP)
        except FlexError as exc:
            if exc.code == "1003" and overrode_dates:
                # "Statement is not available." is all IBKR says, and it is
                # the same sentence whether the token is dead, the dates are
                # wrong, or the query rejects overrides. This is the one
                # reading the caller can narrow, so it is narrowed here
                # rather than left for a human to rediscover.
                raise FlexError(
                    exc.code,
                    f"{exc.message} — "
                    + DATE_OVERRIDE_REJECTED_HINT.format(fd=from_date, td=to_date),
                    exc.error_class,
                    step=SEND_REQUEST_STEP,
                ) from exc
            raise
        reference_code = (root.findtext("ReferenceCode") or root.findtext(".//ReferenceCode") or "").strip()
        statement_url = (root.findtext("Url") or root.findtext(".//Url") or root.findtext(".//url") or "").strip()
        if not reference_code or not statement_url:
            # Quote the body. Saying only that the two elements are missing
            # names the symptom and discards the single piece of evidence
            # that identifies the cause — and this is the branch every
            # unrecognised SendRequest response falls into, so it is the
            # last place the body is still in hand. Which of the two is
            # missing matters too: a ReferenceCode with no Url is IBKR
            # changing shape, and neither is something else entirely.
            missing = ", ".join(
                name
                for name, value in (("ReferenceCode", reference_code), ("Url", statement_url))
                if not value
            )
            raise FlexError(
                None,
                f"SendRequest returned <{root.tag}> with no {missing}; "
                f"body: {body_excerpt(body, self._token)}",
                ErrorClass.UNKNOWN,
                step=SEND_REQUEST_STEP,
            )
        return FlexResponse(reference_code=reference_code, statement_url=statement_url)

    def get_statement(self, response: FlexResponse) -> str:
        params = {"t": self._token, "q": response.reference_code, "v": FLEX_VERSION}
        try:
            xml = self._get(response.statement_url, params, GET_STATEMENT_STEP)
        except FlexError as exc:
            # §3.7 says to use the returned `<Url>` and not hardcode this
            # endpoint, because IBKR has moved these hosts before. That
            # still stands as the default — this does not override IBKR's
            # routing, it survives a host that cannot be reached at all.
            #
            # The trigger is deliberately narrow: a transport failure, where
            # nothing was ever answered. IBKR currently hands back `gdcdyn`
            # from a `ndcdyn` SendRequest, the two are CNAMEs onto the same
            # Akamai edge, and a resolver that manages one and not the other
            # otherwise kills the run after the hard part already succeeded.
            # Any answer from the returned host — including an error — is
            # left alone, because that host is working and IBKR meant it.
            alternate = alternate_statement_url(response.statement_url)
            if exc.code is not None or exc.error_class is not ErrorClass.TRANSIENT or alternate is None:
                raise
            logger.warning(
                "GetStatement host %s unreachable (%s); retrying the same path on %s",
                httpx.URL(response.statement_url).host,
                exc,
                httpx.URL(alternate).host,
            )
            xml = self._get(alternate, params, GET_STATEMENT_STEP)
        # A failure here is reported in-band with a 200, so it still has to
        # be inspected; a successful statement has no Status element.
        root = self._raise_for_status_element(xml, self._token, GET_STATEMENT_STEP)

        # An error page is not always malformed. IBKR's maintenance page is
        # well-formed XHTML, so it parses cleanly, carries no Status element,
        # and would otherwise be handed to the statement parser as though it
        # were data — failing much later as "No FlexStatement element found",
        # which names neither IBKR nor the page. Check what document this is
        # while the body is still here to quote.
        if root.tag not in STATEMENT_ROOTS:
            raise FlexError(
                None,
                f"Expected a Flex statement, got <{root.tag}>: "
                f"{describe_unexpected_body(xml, self._token)}",
                ErrorClass.UNKNOWN,
                step=GET_STATEMENT_STEP,
            )
        return xml

    def fetch(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        period: str | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> tuple[str, str, int]:
        """Run the full two-step exchange with classified retries.

        Returns ``(xml, reference_code, attempts)``. How many attempts a
        failure earns is a property of its class, not a single global
        number (§3.8): credential and configuration failures raise
        immediately because retrying them wastes a rate-limit budget and
        delays the alert a human needs to see, while a request-specific
        failure earns exactly one restart.

        Every retry re-enters at SendRequest and takes a **fresh reference
        code**, which is what makes that single restart worth making at
        all: 1017 says in so many words that the old code is no longer
        good, and a 1003 against a code that was valid a moment ago is the
        same story told less clearly. Reusing the code would ask the
        question that has already been answered.
        """
        attempts = 0
        delay = FIRST_POLL_DELAY_SECONDS

        while True:
            attempts += 1
            try:
                response = self.send_request(from_date=from_date, to_date=to_date, period=period)
                self._sleep(FIRST_POLL_DELAY_SECONDS)
                return self.get_statement(response), response.reference_code, attempts
            except FlexError as exc:
                exc.attempts = attempts
                # Budgeted against the error actually in hand, not the one
                # that started the sequence: a run that opens with a 1003
                # and then hits a genuinely transient code has met a
                # different problem, and deserves that code's full ladder.
                budget = attempt_budget(exc.error_class, max_attempts)
                if exc.error_class is ErrorClass.REQUEST and exc.step == SEND_REQUEST_STEP:
                    # §3.8's "restart from SendRequest once" is a remedy for
                    # a reference code that has gone bad — 1017 says so
                    # outright, and a 1003 against a code that was valid a
                    # moment ago is the same story. A failure *at*
                    # SendRequest has no reference code to refresh, so the
                    # restart asks an identical question and can only get an
                    # identical answer.
                    #
                    # The cost is not merely a wasted request. IBKR locks a
                    # token out after too many failed attempts (1025), so a
                    # deterministic rejection retried on every run is a
                    # standing generator of exactly the failures that
                    # trigger the lockout.
                    budget = 1
                if attempts >= budget:
                    raise
                self._sleep(delay)
                delay *= 2
