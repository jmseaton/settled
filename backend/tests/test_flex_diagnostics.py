"""What the app says when IBKR answers with something that is not a statement.

Both of these were found the first time the app ran against the live Flex
service rather than a fixture. The parse failure itself was handled; what
was missing was any way to find out *why* from the outside.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.flex.client import FlexClient
from app.flex.errors import ErrorClass, FlexError
from app.main import app

TOKEN = "supersecrettoken"


def _client(handler) -> FlexClient:
    return FlexClient(
        TOKEN,
        "999",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )


def _send_request_ok(request: httpx.Request) -> bool:
    return "SendRequest" in str(request.url)


SEND_OK = (
    '<FlexStatementResponse timestamp="1"><Status>Success</Status>'
    "<ReferenceCode>1234</ReferenceCode>"
    "<Url>https://example.invalid/GetStatement</Url></FlexStatementResponse>"
)


def test_a_well_formed_html_page_is_not_mistaken_for_a_statement():
    """The nastier case, and the one that was silently passing.

    IBKR's maintenance page is well-formed XHTML: it parses, it carries no
    Status element, so the client handed it on as a statement and the
    failure surfaced pages later as "No FlexStatement element found" —
    naming neither IBKR nor the page.
    """
    body = (
        "<html><head><title>Service Unavailable</title></head>"
        "<body>We are performing scheduled maintenance.</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SEND_OK if _send_request_ok(request) else body)

    with _client(handler) as client:
        with pytest.raises(FlexError) as caught:
            client.fetch()

    message = str(caught.value)
    assert "Expected a Flex statement, got <html>" in message
    assert "scheduled maintenance" in message


def test_a_malformed_html_error_page_is_named_as_one():
    # Truncated mid-tag, the way a proxy cutting a response off looks.
    body = "<!DOCTYPE html><html><head><title>502 Bad Gateway</ti"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SEND_OK if _send_request_ok(request) else body)

    with _client(handler) as client:
        with pytest.raises(FlexError) as caught:
            client.fetch()

    message = str(caught.value)
    # The bare ParseError said only "not well-formed"; the point of the fix
    # is that the reader learns what arrived instead.
    assert "an HTML page rather than XML" in message
    assert "502 Bad Gateway" in message


def test_a_real_statement_still_passes():
    statement = (
        '<FlexQueryResponse queryName="Test" type="AF"><FlexStatements count="1">'
        '<FlexStatement accountId="U0000000" fromDate="20310512" toDate="20310808"/>'
        "</FlexStatements></FlexQueryResponse>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SEND_OK if _send_request_ok(request) else statement)

    with _client(handler) as client:
        xml, reference, attempts = client.fetch()

    assert "FlexQueryResponse" in xml
    assert reference == "1234"
    assert attempts == 1


def test_a_csv_flex_query_names_the_setting_to_change():
    """The real-world failure this whole file came from.

    A valid token and a valid query still return something unreadable when
    the query's Format is text: the request looks identical, IBKR answers
    200, and the parse error names a column number. The response is CSV.
    """
    body = (
        '"BOF","U00000000","Settled","12","20310512","20310808","20310810;171043","100","100"\n'
        '"BOA","U00000000"\n"BOS","ACCT","Account Information"\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SEND_OK if _send_request_ok(request) else body)

    with _client(handler) as client:
        with pytest.raises(FlexError) as caught:
            client.fetch()

    message = str(caught.value)
    assert "CSV/text output rather than XML" in message
    # A diagnosis without the remedy would leave the reader hunting through
    # Client Portal for a setting they do not know exists.
    assert "Format" in message


def test_a_plain_text_body_is_quoted_back():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=SEND_OK if _send_request_ok(request) else "Too many requests, try later"
        )

    with _client(handler) as client:
        with pytest.raises(FlexError) as caught:
            client.fetch()

    message = str(caught.value)
    assert "a non-XML body" in message
    assert "Too many requests" in message


def test_an_empty_body_says_so_rather_than_quoting_nothing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SEND_OK if _send_request_ok(request) else "   ")

    with _client(handler) as client:
        with pytest.raises(FlexError) as caught:
            client.fetch()

    assert "the response body was empty" in str(caught.value)


def test_the_excerpt_is_truncated_and_the_token_never_appears():
    # A body long enough to matter, carrying the token the way an echoed
    # request URL would. §3.10: the token must not reach a log or the UI.
    body = f"<broken>{'x' * 500}?t={TOKEN}&q=1"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SEND_OK if _send_request_ok(request) else body)

    with _client(handler) as client:
        with pytest.raises(FlexError) as caught:
            client.fetch()

    message = str(caught.value)
    assert TOKEN not in message
    assert "…" in message
    assert len(message) < 500


def test_fetch_epoch_value_reports_a_flex_failure_as_json(monkeypatch):
    """The bug the user hit: a FlexError escaping as a plain-text 500.

    The frontend reads every response as JSON, so an unhandled exception
    surfaced as "Unexpected token 'I', \"Internal S\"... is not valid JSON"
    — a message about the transport, naming neither the endpoint nor the
    cause.
    """
    from app.api import routes

    class Boom:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, **kwargs):
            raise FlexError(
                "1020",
                "Invalid request or unable to validate request.",
                ErrorClass.CONFIGURATION,
            )

    monkeypatch.setattr(routes, "FlexClient", lambda *a, **k: Boom())

    settings = routes.get_app_settings()
    monkeypatch.setattr(settings, "flex_token", "t", raising=False)
    monkeypatch.setattr(settings, "flex_query_id", "q", raising=False)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/settings/epoch/fetch-value", json={"date": "2031-05-12"}
        )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "2031-05-12" in detail
    assert "1020" in detail
    # It has to point somewhere useful. Not at a plain sync: the sync runner
    # sends fd/td as well, so it fails the same way for the same reason and
    # discriminates nothing.
    assert "app.flex.diagnose" in detail
