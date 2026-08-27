"""A read-only probe of the Flex Web Service, for when a sync fails opaquely.

    docker compose exec backend python -m app.flex.diagnose

Every request the app makes overrides the query's dates: the sync runner
sends a trailing 30-day `fd`/`td` window (§3.8) and the epoch fetch sends a
single day (§0.3). Nothing sends a *plain* request, so when both fail
identically there is no way from inside the app to tell "IBKR is unhappy
with the date override" from "IBKR is unhappy with everything".

This makes both requests and prints what came back, so the two are
distinguishable in one run. It writes nothing and changes nothing.

The token is redacted from every line, including bodies that echo the
request URL back (§3.10). Bodies are truncated — this prints enough to
identify a page, not the page.
"""

import datetime as dt
import sys

import httpx

from app.config import get_settings
from app.flex.client import (
    DEFAULT_USER_AGENT,
    FLEX_VERSION,
    SEND_REQUEST_URL,
    STATEMENT_ROOTS,
    describe_unexpected_body,
    redact,
)
from app.flex.sync import DEFAULT_WINDOW_DAYS, broker_today

EXCERPT = 400
POLL_DELAY_SECONDS = 5.0
WINDOW_DAYS = DEFAULT_WINDOW_DAYS


def _show(label: str, response: httpx.Response, token: str) -> str:
    body = response.text
    print(f"  {label}: HTTP {response.status_code} {response.headers.get('content-type', '?')}")
    excerpt = " ".join(body[:EXCERPT].split())
    print(f"  body: {redact(excerpt, token)!r}" + ("…" if len(body) > EXCERPT else ""))
    return body


def _attempt(client: httpx.Client, token: str, query_id: str, params: dict, label: str) -> None:
    print(f"\n{label}")
    send_params = {"t": token, "q": query_id, "v": FLEX_VERSION, **params}
    printable = {k: ("***" if k == "t" else v) for k, v in send_params.items()}
    print(f"  SendRequest params: {printable}")

    try:
        response = client.get(SEND_REQUEST_URL, params=send_params)
    except httpx.HTTPError as exc:
        print(f"  SendRequest failed at the transport: {redact(str(exc), token)}")
        return

    body = _show("SendRequest", response, token)

    # Pull the reference code and URL out by hand rather than through the
    # client — the point of this script is to see responses the client
    # rejects, so it must not depend on the client accepting them.
    reference = _between(body, "<ReferenceCode>", "</ReferenceCode>")
    url = _between(body, "<Url>", "</Url>") or _between(body, "<url>", "</url>")
    if not reference or not url:
        print("  -> no ReferenceCode/Url, so there is nothing to fetch. Stop here.")
        return

    print(f"  -> reference code obtained, waiting {POLL_DELAY_SECONDS:.0f}s for generation")
    import time

    time.sleep(POLL_DELAY_SECONDS)

    try:
        statement = client.get(url, params={"t": token, "q": reference, "v": FLEX_VERSION})
    except httpx.HTTPError as exc:
        print(f"  GetStatement failed at the transport: {redact(str(exc), token)}")
        return

    text = _show("GetStatement", statement, token)
    stripped = text.strip()
    if any(stripped.startswith(f"<{root}") for root in STATEMENT_ROOTS):
        print("  -> looks like a real statement. This combination works.")
    else:
        print(f"  -> NOT a statement: {describe_unexpected_body(text, token)}")


def _between(text: str, start: str, end: str) -> str | None:
    if start not in text:
        return None
    rest = text.split(start, 1)[1]
    return rest.split(end, 1)[0].strip() if end in rest else None


def main() -> int:
    settings = get_settings()
    if not settings.flex_token or not settings.flex_query_id:
        print("Flex credentials are not configured (SETTLED_FLEX_TOKEN / SETTLED_FLEX_QUERY_ID).")
        return 1

    token = settings.flex_token
    query_id = settings.flex_query_id
    print(f"Query {query_id}, token length {len(token)} (not shown).")

    # The same broker day the sync runner uses, or request B stops being
    # "exactly what a sync sends" for the four evening hours in which UTC
    # and Eastern disagree — which is precisely the window where a date
    # bug shows up, and so precisely the run you would be diagnosing.
    today = broker_today()
    window_from = today - dt.timedelta(days=WINDOW_DAYS)

    with httpx.Client(headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=30.0) as client:
        # No date parameters: IBKR uses whatever period the query itself is
        # configured for. If this works and the next one does not, the date
        # override is the problem — and A is also the request the sync falls
        # back to, so this says whether that fallback has anything to land on.
        _attempt(client, token, query_id, {}, "A — the query's own dates (no override)")
        _attempt(
            client,
            token,
            query_id,
            {"fd": window_from.strftime("%Y%m%d"), "td": today.strftime("%Y%m%d")},
            "B — a 30-day fd/td override, exactly what a sync sends",
        )
        # `p` is the other override in §3.7 and a separate question from
        # `fd`/`td`: a query might take one and not the other, and if it
        # takes this one the sync can have its exact 30 days back instead of
        # whatever period the query happens to be set to.
        _attempt(
            client,
            token,
            query_id,
            {"p": str(WINDOW_DAYS)},
            f"C — a p={WINDOW_DAYS} trailing-period override",
        )

    print(
        "\nIf A works and B does not, the Flex query does not permit fd/td "
        "overrides. No Client Portal setting fixes that — the Period list offers "
        "only relative periods, with no custom date range — so the sync falls back "
        "to A automatically and covers the query's own period instead. Widen that "
        f"period if it reaches back less than the {WINDOW_DAYS} days §3.8 wants."
        "\nIf C also works, say so: the sync can send p instead and get its exact "
        f"{WINDOW_DAYS}-day window back."
        "\nIf both A and B fail the same way, the override is not the cause — read "
        "the bodies above."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
