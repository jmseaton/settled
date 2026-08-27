"""Which `PriceSource` this deployment uses (§7.5).

Configuration picks the adapter; nothing else in the app knows which one
was chosen. `auto` prefers Tiingo when a key is present and falls back to
Stooq when it is not, so an install with no key still draws a benchmark
line — with the price-only caveat attached — rather than showing an empty
chart and a configuration error.
"""

from app.config import get_settings
from app.prices.base import PriceSource
from app.prices.stooq import StooqSource
from app.prices.tiingo import TiingoSource


def build_price_source(provider: str | None = None, api_key: str | None = None) -> PriceSource:
    settings = get_settings()
    choice = (provider or settings.price_source).strip().lower()
    key = api_key if api_key is not None else settings.tiingo_api_key

    if choice == "auto":
        choice = "tiingo" if key else "stooq"

    if choice == "tiingo":
        return TiingoSource(key)
    if choice == "stooq":
        return StooqSource()
    raise ValueError(
        f"Unknown price source {choice!r}; expected 'auto', 'tiingo' or 'stooq' (§7.5)"
    )


def source_basis_note(source: PriceSource) -> str | None:
    """The dividend-adjustment caveat, if the adapter carries one.

    Surfaced next to any benchmark number rather than buried in a docstring:
    a price-only series and a total-return series answer the same question
    differently, and which one produced a figure is part of the figure.
    """
    return getattr(source, "basis_note", None)
