"""§16.1 — Flex sync tests. Mocked HTTP; no live calls in CI."""

import httpx
import pytest

from app.flex.client import FlexClient, redact
from app.flex.errors import (
    ErrorClass,
    FlexError,
    attempt_budget,
    classify,
    is_retryable,
)

TOKEN = "SECRET_TOKEN_123456"
QUERY_ID = "999888"

SEND_OK = """<FlexStatementResponse timestamp='...'>
<Status>Success</Status>
<ReferenceCode>REF12345</ReferenceCode>
<Url>https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url>
</FlexStatementResponse>"""

STATEMENT_OK = "<FlexQueryResponse queryName='Test' type='AF'><FlexStatements count='1'/></FlexQueryResponse>"


def _fail(code: str) -> str:
    return (
        f"<FlexStatementResponse><Status>Fail</Status><ErrorCode>{code}</ErrorCode>"
        f"<ErrorMessage>Something went wrong for token {TOKEN}</ErrorMessage></FlexStatementResponse>"
    )


class Recorder:
    """Captures every request so the tests can assert on what was sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.responses.pop(0) if self.responses else STATEMENT_OK
        return httpx.Response(200, text=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _client(recorder, **kwargs):
    slept: list[float] = []
    client = FlexClient(
        TOKEN, QUERY_ID, transport=recorder.transport(), sleep=slept.append, **kwargs
    )
    client._slept = slept  # type: ignore[attr-defined]
    return client


# --------------------------------------------------------------- test 17
def test_17_user_agent_is_non_optional():
    recorder = Recorder([SEND_OK, STATEMENT_OK])
    with pytest.raises(ValueError, match="User-Agent"):
        FlexClient(TOKEN, QUERY_ID, user_agent="", transport=recorder.transport())
    assert recorder.requests == [], "client must raise before sending anything"


def test_17b_user_agent_is_sent_on_every_request():
    recorder = Recorder([SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        client.fetch(period="30")
    assert len(recorder.requests) == 2
    for request in recorder.requests:
        assert "Settled" in request.headers["user-agent"]


# --------------------------------------------------------------- test 18
def test_18_version_3_on_both_calls():
    recorder = Recorder([SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        client.fetch(period="30")
    for request in recorder.requests:
        assert request.url.params["v"] == "3"


# --------------------------------------------------------------- test 19
def test_19_get_statement_uses_url_from_send_request():
    moved = """<FlexStatementResponse><Status>Success</Status>
    <ReferenceCode>REF1</ReferenceCode>
    <Url>https://relocated.example.com/NewPath/GetStatement</Url></FlexStatementResponse>"""
    recorder = Recorder([moved, STATEMENT_OK])
    with _client(recorder) as client:
        client.fetch(period="30")
    assert str(recorder.requests[1].url).startswith("https://relocated.example.com/NewPath/GetStatement")


# --------------------------------------------------------------- test 20
@pytest.mark.parametrize("code", ["1005", "1009", "1019"])
def test_20_transient_error_retries_then_succeeds(code):
    recorder = Recorder([_fail(code), SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        xml, reference, attempts = client.fetch(period="30")
    assert xml == STATEMENT_OK
    assert reference == "REF12345"
    assert attempts == 2


def test_20b_transient_error_backs_off_exponentially():
    recorder = Recorder([_fail("1005"), _fail("1005"), SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        client.fetch(period="30")
        delays = [d for d in client._slept if d > 0]
    # Retry delays double; the constant first-poll delay appears alongside.
    assert any(b > a for a, b in zip(delays, delays[1:]))


# --------------------------------------------------------------- test 21
@pytest.mark.parametrize("code", ["1012", "1015"])
def test_21_credential_error_never_retries(code):
    recorder = Recorder([_fail(code), SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30")
    assert exc.value.error_class == ErrorClass.CREDENTIAL
    assert len(recorder.requests) == 1, "a credential failure must not be retried"


def test_21b_configuration_error_never_retries():
    recorder = Recorder([_fail("1014"), SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30")
    assert exc.value.error_class == ErrorClass.CONFIGURATION
    assert len(recorder.requests) == 1


def test_21k_a_non_success_status_is_an_error_with_its_code_intact():
    """Checking only for the literal "Fail" let every other status fall
    through and be read as a statement, discarding the ErrorCode and
    ErrorMessage that said what was wrong."""
    warned = (
        "<FlexStatementResponse><Status>Warn</Status><ErrorCode>1018</ErrorCode>"
        "<ErrorMessage>Too many requests</ErrorMessage></FlexStatementResponse>"
    )
    recorder = Recorder([warned, warned, warned, warned, warned, warned])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30", max_attempts=1)

    assert exc.value.code == "1018"
    assert exc.value.error_class == ErrorClass.RATE_LIMIT
    assert "Too many requests" in str(exc.value)


def test_21l_a_status_with_no_code_still_reports_the_status():
    recorder = Recorder(["<FlexStatementResponse><Status>Queued</Status></FlexStatementResponse>"])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30", max_attempts=1)
    assert "Queued" in str(exc.value)


def test_21m_a_missing_reference_code_quotes_the_body():
    """The last place the body is still in hand. Naming only the missing
    elements discards the one piece of evidence that identifies the cause,
    and every unrecognised SendRequest response lands here."""
    odd = "<FlexStatementResponse><Status>Success</Status><Note>nothing useful</Note></FlexStatementResponse>"
    recorder = Recorder([odd])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30", max_attempts=1)

    message = str(exc.value)
    assert "ReferenceCode, Url" in message
    assert "nothing useful" in message, "the body must be quoted, not summarised"
    assert "does not parse" not in message, "it parsed fine; do not say otherwise"


def test_21o_an_unreachable_statement_host_falls_back_to_the_send_request_host():
    """§3.7's "use the returned <Url>" still stands — this survives a host
    that answers nothing at all, rather than overriding IBKR's routing."""
    moved = """<FlexStatementResponse><Status>Success</Status>
    <ReferenceCode>REF1</ReferenceCode>
    <Url>https://gdcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url>
    </FlexStatementResponse>"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "SendRequest" in str(request.url):
            return httpx.Response(200, text=moved)
        if request.url.host == "gdcdyn.interactivebrokers.com":
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return httpx.Response(200, text=STATEMENT_OK)

    client = FlexClient(
        TOKEN, QUERY_ID, transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    with client:
        xml, _reference, _attempts = client.fetch(period="30")

    assert xml == STATEMENT_OK
    assert seen[-1].startswith("https://ndcdyn.interactivebrokers.com/")
    assert "GetStatement" in seen[-1], "the path must be preserved, only the host swapped"


def test_21p_an_answering_statement_host_is_never_second_guessed():
    """Any answer means the host works and IBKR meant it — including an
    error. Only silence justifies trying somewhere else."""
    recorder = Recorder([SEND_OK, _fail("1014")])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30")
    assert exc.value.code == "1014"
    assert len(recorder.requests) == 2, "no second host on a real answer"


def test_21c_error_classification_table():
    assert classify("1012") == ErrorClass.CREDENTIAL
    assert classify("1013") == ErrorClass.CREDENTIAL
    assert classify("1018") == ErrorClass.RATE_LIMIT
    assert classify("1005") == ErrorClass.TRANSIENT
    assert classify("1016") == ErrorClass.CONFIGURATION
    assert classify("1003") == ErrorClass.REQUEST
    assert classify("9999") == ErrorClass.UNKNOWN
    assert is_retryable(ErrorClass.TRANSIENT)
    assert is_retryable(ErrorClass.RATE_LIMIT)
    assert not is_retryable(ErrorClass.CREDENTIAL)
    assert not is_retryable(ErrorClass.CONFIGURATION)

    # A request-specific failure is retryable, but only just: the budget,
    # not the boolean, is what a retry loop has to consult.
    assert is_retryable(ErrorClass.REQUEST)
    assert attempt_budget(ErrorClass.REQUEST, 6) == 2
    assert attempt_budget(ErrorClass.TRANSIENT, 6) == 6
    assert attempt_budget(ErrorClass.CREDENTIAL, 6) == 1
    assert attempt_budget(ErrorClass.UNKNOWN, 6) == 1
    # Never above the caller's own ceiling.
    assert attempt_budget(ErrorClass.REQUEST, 1) == 1


def test_21n_a_lockout_is_alerting_and_never_retried():
    """1025 is the one code the app's own behaviour causes, so retrying it
    deepens the thing it reports."""
    assert classify("1025") == ErrorClass.CONFIGURATION
    assert not is_retryable(ErrorClass.CONFIGURATION)

    locked = (
        "<FlexStatementResponse><Status>Warn</Status><ErrorCode>1025</ErrorCode>"
        "<ErrorMessage>Too many failed attempts. Please review your configuration.</ErrorMessage>"
        "</FlexStatementResponse>"
    )
    recorder = Recorder([locked, SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(from_date="20310712", to_date="20310811")

    assert exc.value.code == "1025"
    assert len(recorder.requests) == 1, "a lockout must not be hit again"


@pytest.mark.parametrize("code", ["1003", "1017"])
def test_21d_request_error_restarts_from_send_request_once(code):
    """§3.8 — "restart from SendRequest once, then give up for this run".

    1003 and 1017 are properties of one exchange rather than of the token
    or the query, so the restart has to take a *fresh* reference code for
    it to be worth making at all — which is also why it is only offered to
    a failure that had a reference code, i.e. one from GetStatement.
    """
    recorder = Recorder([SEND_OK, _fail(code), SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        xml, _reference, attempts = client.fetch(period="30")

    assert xml == STATEMENT_OK
    assert attempts == 2
    assert len(recorder.requests) == 4, "the restart must re-enter at SendRequest"
    assert "SendRequest" in str(recorder.requests[2].url)


def test_21e_request_error_gives_up_after_that_one_restart():
    recorder = Recorder([SEND_OK, _fail("1017"), SEND_OK, _fail("1017"), SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30")

    assert exc.value.error_class == ErrorClass.REQUEST
    assert exc.value.attempts == 2
    assert len(recorder.requests) == 4, "one restart, not a ladder"


def test_21e2_a_request_error_at_send_request_is_never_restarted():
    """The restart refreshes a reference code. A failure *at* SendRequest
    has none, so asking again asks an identical question — and IBKR locks a
    token out after too many failed attempts (1025), which makes a
    deterministic rejection retried every run a standing generator of the
    failures that cause the lockout."""
    recorder = Recorder([_fail("1003"), SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(from_date="20310712", to_date="20310811")

    assert exc.value.attempts == 1
    assert len(recorder.requests) == 1, "one request, not two"


def test_21f_a_transient_error_after_a_request_error_gets_its_own_ladder():
    """The budget follows the error in hand, not the one that opened the
    sequence — a 1003 that turns into a genuinely transient failure has met
    a different problem and deserves that problem's retries."""
    recorder = Recorder(
        [SEND_OK, _fail("1003"), _fail("1005"), _fail("1005"), SEND_OK, STATEMENT_OK]
    )
    with _client(recorder) as client:
        xml, _reference, attempts = client.fetch(period="30")
    assert xml == STATEMENT_OK
    assert attempts == 4


def test_21h_a_1003_on_an_overridden_send_request_names_the_query_period():
    """§3.9 — a query on a relative period rejects every date override.

    "Statement is not available." is the same sentence IBKR returns for a
    genuinely unavailable statement, and on its own it sends you looking at
    the token or the dates. The one reading the caller can narrow — that a
    date override was in play, at SendRequest, before any statement existed
    — is narrowed here rather than left for a human to rediscover.
    """
    recorder = Recorder([_fail("1003"), _fail("1003")])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(from_date="20310712", to_date="20310811")

    message = str(exc.value)
    # What IBKR said is kept, with the hint appended — not replaced by it.
    assert "Something went wrong" in message
    assert "***REDACTED***" in message and TOKEN not in message
    assert "fd=20310712&td=20310811" in message
    assert "relative period" in message
    assert "diagnose" in message
    assert exc.value.step == "SendRequest"


def test_21i_a_1003_without_an_override_does_not_blame_the_query_period():
    """The hint is only true of an overridden request. A 1003 from
    GetStatement is a different event with a different cause, and pointing
    it at Client Portal would send a human to the wrong screen."""
    recorder = Recorder([SEND_OK, _fail("1003"), SEND_OK, _fail("1003")])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(from_date="20310712", to_date="20310811")

    assert exc.value.step == "GetStatement"
    assert "relative period" not in str(exc.value)


def test_21j_a_transport_failure_names_the_host_that_failed():
    """The two steps do not share a host: SendRequest is hardcoded to
    `ndcdyn`, GetStatement goes wherever `<Url>` points (in practice
    `gdcdyn`). A resolver that handles one and not the other is otherwise
    unplaceable — "Temporary failure in name resolution" names nothing, and
    neither does the source, since IBKR picked the host at runtime."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "GetStatement" in str(request.url):
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return httpx.Response(200, text=SEND_OK)

    client = FlexClient(
        TOKEN, QUERY_ID, transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    with client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30", max_attempts=1)

    assert exc.value.error_class == ErrorClass.TRANSIENT
    assert "ndcdyn.interactivebrokers.com" in str(exc.value)
    assert "GetStatement" in str(exc.value)


def test_21g_failures_name_the_step_they_came_from():
    """1003 from SendRequest is IBKR declining to build the statement; 1003
    from GetStatement is one it built and could not hand back. The stored
    message is identical without the step, and points at the wrong fix."""
    recorder = Recorder([_fail("1003"), _fail("1003")])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30")
    assert exc.value.step == "SendRequest"
    assert "from SendRequest" in str(exc.value)

    recorder = Recorder([SEND_OK, _fail("1003"), SEND_OK, _fail("1003")])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30")
    assert exc.value.step == "GetStatement"
    assert "from GetStatement" in str(exc.value)


# --------------------------------------------------------------- test 22
def test_22_rate_limit_is_respected():
    recorder = Recorder([_fail("1018"), SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        client.fetch(period="30")
        slept = client._slept
    assert sum(slept) > 0, "a rate-limit response must back off"


def test_22b_requests_are_throttled_below_one_per_second():
    from app.flex.client import MIN_REQUEST_INTERVAL_SECONDS

    recorder = Recorder([SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        client.fetch(period="30")
    assert MIN_REQUEST_INTERVAL_SECONDS >= 1.0


# --------------------------------------------------------------- test 23
def test_23_token_never_appears_in_an_error_message():
    recorder = Recorder([_fail("1012")])
    with _client(recorder) as client:
        with pytest.raises(FlexError) as exc:
            client.fetch(period="30")
    assert TOKEN not in str(exc.value)
    assert "***REDACTED***" in str(exc.value)


def test_23b_redact_strips_tokens_from_urls():
    url = f"https://example.com/GetStatement?t={TOKEN}&q=REF1&v=3"
    cleaned = redact(url, TOKEN)
    assert TOKEN not in cleaned
    assert "q=REF1" in cleaned, "redaction must not destroy the rest of the message"


def test_23c_redact_handles_a_token_it_was_not_given():
    url = "https://example.com/GetStatement?t=SOME_OTHER_TOKEN&q=REF1"
    assert "SOME_OTHER_TOKEN" not in redact(url)


# --------------------------------------------------------------- test 25
def test_25_date_and_period_overrides_are_sent():
    recorder = Recorder([SEND_OK, STATEMENT_OK])
    with _client(recorder) as client:
        client.fetch(from_date="20310602", to_date="20310808")
    params = recorder.requests[0].url.params
    assert params["fd"] == "20310602"
    assert params["td"] == "20310808"

    recorder2 = Recorder([SEND_OK, STATEMENT_OK])
    with _client(recorder2) as client:
        client.fetch(period="30")
    assert recorder2.requests[0].url.params["p"] == "30"


def test_send_request_rejects_a_success_with_no_reference_code():
    incomplete = "<FlexStatementResponse><Status>Success</Status></FlexStatementResponse>"
    recorder = Recorder([incomplete])
    with _client(recorder) as client:
        with pytest.raises(FlexError, match="no ReferenceCode"):
            client.send_request(period="30")
