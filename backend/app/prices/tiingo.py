"""§7.5 — Tiingo adapter, the recommended source.

Chosen over the alternatives for one reason that matters more than the free
tier: Tiingo returns `adjClose` and `divCash` as separate fields, so the
total-return question is unambiguous rather than something to be inferred
from a field named "adjusted". The free tier is far more than one symbol
needs, and the terms of service are explicit.

The API key is read from the environment, never the database — same
reasoning as the Flex token (§3.10), and for the same reason it is redacted
from any error that could reach a log or the UI.
"""

import datetime as dt

import httpx

from app.prices.base import DailyBar, PriceFetchError

BASE_URL = "https://api.tiingo.com/tiingo/daily"
DEFAULT_TIMEOUT = 20.0


class TiingoSource:
    name = "tiingo"

    def __init__(self, api_key: str, *, timeout: float = DEFAULT_TIMEOUT, client: httpx.Client | None = None):
        if not api_key:
            raise PriceFetchError("Tiingo API key is not configured (SETTLED_TIINGO_API_KEY)")
        self._api_key = api_key
        self._timeout = timeout
        self._client = client

    def daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> list[DailyBar]:
        params = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "format": "json",
        }
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = client.get(f"{BASE_URL}/{symbol}/prices", params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise PriceFetchError(self._redact(f"Tiingo request failed: {exc}")) from None
        except ValueError as exc:
            raise PriceFetchError(f"Tiingo returned a non-JSON body: {exc}") from None
        finally:
            if self._client is None:
                client.close()

        if not isinstance(payload, list):
            raise PriceFetchError(f"Tiingo returned an unexpected payload shape: {type(payload).__name__}")

        bars: list[DailyBar] = []
        for row in payload:
            day = _parse_date(row.get("date"))
            close = row.get("close")
            adj_close = row.get("adjClose")
            if day is None or close is None or adj_close is None:
                # A partial row is dropped rather than defaulted: an
                # invented close would flow straight into the shadow
                # portfolio and be indistinguishable from a real one.
                continue
            bars.append(DailyBar(date=day, close=float(close), adj_close=float(adj_close)))
        bars.sort(key=lambda b: b.date)
        return bars

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***REDACTED***") if self._api_key else text


def _parse_date(raw) -> dt.date | None:
    if not raw:
        return None
    text = str(raw)
    # Tiingo returns full ISO timestamps ("2026-08-07T00:00:00.000Z").
    return dt.date.fromisoformat(text[:10]) if len(text) >= 10 else None
