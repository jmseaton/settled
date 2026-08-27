"""§7.5 — Stooq adapter, the keyless fallback.

Zero friction: free daily history, no key, a plain CSV endpoint. It exists
so a missing Tiingo key degrades the benchmark to "works, verify the basis"
rather than "does not work at all".

**The caveat is load-bearing.** Stooq's US equity series is split-adjusted
and its dividend treatment is not documented, so this adapter reports
`close` and `adj_close` as the same value and says so, rather than
labelling a price series as adjusted. Benchmarking against price-only
understates SPY by roughly its 1.2% yield per year — small over ten weeks,
material over three years — so `basis_note` travels with the data and the
UI states it beside any number derived from these bars.
"""

import csv
import datetime as dt
import io

import httpx

from app.prices.base import DailyBar, PriceFetchError

BASE_URL = "https://stooq.com/q/d/l/"
DEFAULT_TIMEOUT = 20.0

BASIS_NOTE = (
    "Stooq bars are split-adjusted; dividend adjustment is undocumented, so adj_close "
    "mirrors close. Benchmark returns from this source are price-only and understate SPY "
    "by roughly its dividend yield per year (§7.5)."
)


class StooqSource:
    name = "stooq"
    basis_note = BASIS_NOTE

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT, client: httpx.Client | None = None):
        self._timeout = timeout
        self._client = client

    def daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> list[DailyBar]:
        params = {
            # Stooq wants US tickers suffixed and lowercased.
            "s": f"{symbol.lower()}.us",
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = client.get(BASE_URL, params=params)
            response.raise_for_status()
            text = response.text
        except httpx.HTTPError as exc:
            raise PriceFetchError(f"Stooq request failed: {exc}") from None
        finally:
            if self._client is None:
                client.close()

        # Stooq answers an unknown symbol or an exhausted quota with a 200
        # and a one-line body, not an error status.
        if not text.lstrip().lower().startswith("date"):
            raise PriceFetchError(f"Stooq returned no data: {text.strip()[:120]!r}")

        bars: list[DailyBar] = []
        for row in csv.DictReader(io.StringIO(text)):
            try:
                day = dt.date.fromisoformat(row["Date"])
                close = float(row["Close"])
            except (KeyError, TypeError, ValueError):
                continue
            bars.append(DailyBar(date=day, close=close, adj_close=close))
        bars.sort(key=lambda b: b.date)
        return bars
