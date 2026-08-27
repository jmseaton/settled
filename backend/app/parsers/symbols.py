"""§2.4 — symbol parsing for the three schemes the `.tlg` format uses."""

import datetime as dt
import re
from dataclasses import dataclass

from app.models.enums import AssetClass, Right

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_DESCRIPTION_RE = re.compile(r"^(\S+)\s+(\d{2})([A-Z]{3})(\d{2})\s+([\d.]+)\s+([CP])$")


@dataclass
class ParsedSymbol:
    asset_class: AssetClass
    root_symbol: str
    broker_symbol: str
    broker_local_symbol: str | None
    underlying_root: str | None
    expiry: dt.date | None
    strike: float | None
    right: Right | None
    parse_status: str
    warning: str | None = None
    # Flex-only. `underlying_symbol` is the specific contract (MESU1) while
    # `underlying_root` is the generic root (MES) — §2.4 wants both, so that
    # grouping never merges positions across futures months. `conid` is
    # IBKR's stable contract id and is the natural key on the Flex path.
    underlying_symbol: str | None = None
    conid: str | None = None
    #: §4.2 / §5.4 — the underlying contract's own conid. The reliable way to
    #: tie an assignment's option leg to the position it produced; symbol
    #: matching is only the `.tlg` fallback.
    underlying_conid: str | None = None


def _parse_description(description: str) -> tuple[str, dt.date, float, Right] | None:
    m = _DESCRIPTION_RE.match(description.strip())
    if not m:
        return None
    root, day, mon, yy, strike, right = m.groups()
    if mon not in _MONTHS:
        return None
    year = 2000 + int(yy)
    expiry = dt.date(year, _MONTHS[mon], int(day))
    return root, expiry, float(strike), Right(right)


def _occ_expiry_to_yyyymmdd(yymmdd: str) -> dt.date:
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    return dt.date(2000 + yy, mm, dd)


def parse_stock_symbol(symbol: str) -> ParsedSymbol:
    """Plain ticker — nothing to parse (§2.4)."""
    ticker = symbol.strip()
    return ParsedSymbol(
        asset_class=AssetClass.STK,
        root_symbol=ticker,
        broker_symbol=ticker,
        broker_local_symbol=None,
        underlying_root=ticker,
        expiry=None,
        strike=None,
        right=None,
        parse_status="ok",
    )


def parse_equity_option_symbol(symbol: str, description: str) -> ParsedSymbol:
    """OCC 21-char format, cross-checked against the description field."""
    raw = symbol  # keep original width/padding as the broker_symbol identity
    warning = None
    root = raw[0:6].strip()
    expiry = None
    strike = None
    right = None
    parse_status = "ok"

    if len(raw) == 21:
        try:
            expiry = _occ_expiry_to_yyyymmdd(raw[6:12])
            right = Right(raw[12])
            strike = int(raw[13:21]) / 1000.0
        except (ValueError, KeyError):
            parse_status = "unparsed"
    else:
        parse_status = "unparsed"

    desc_parsed = _parse_description(description)
    if desc_parsed:
        d_root, d_expiry, d_strike, d_right = desc_parsed
        if parse_status == "ok":
            if (root, expiry, strike, right) != (d_root, d_expiry, d_strike, d_right):
                warning = (
                    f"OCC symbol {raw!r} disagrees with description {description!r}: "
                    f"({root},{expiry},{strike},{right}) vs ({d_root},{d_expiry},{d_strike},{d_right})"
                )
        else:
            root, expiry, strike, right = d_root, d_expiry, d_strike, d_right
            parse_status = "ok"
            warning = f"OCC symbol {raw!r} unparseable; fell back to description {description!r}"

    return ParsedSymbol(
        asset_class=AssetClass.OPT,
        root_symbol=root,
        broker_symbol=raw,
        broker_local_symbol=None,
        underlying_root=root,
        expiry=expiry,
        strike=strike,
        right=right,
        parse_status=parse_status,
        warning=warning,
    )


def parse_futures_option_symbol(local_symbol: str, description: str) -> ParsedSymbol:
    """IB's local symbol (`EX1Q6 P7120`) is not reliably parseable (§2.4);
    the description field (`MES 07AUG26 7120 P`) is the `.tlg`-only fallback
    and is what this function actually decodes."""
    desc_parsed = _parse_description(description)
    if desc_parsed is None:
        return ParsedSymbol(
            asset_class=AssetClass.FOP,
            root_symbol=local_symbol.split()[0] if local_symbol.split() else local_symbol,
            broker_symbol=local_symbol,
            broker_local_symbol=local_symbol,
            underlying_root=None,
            expiry=None,
            strike=None,
            right=None,
            parse_status="unparsed",
            warning=f"Could not parse FOP description {description!r}",
        )
    root, expiry, strike, right = desc_parsed
    return ParsedSymbol(
        asset_class=AssetClass.FOP,
        root_symbol=root,
        broker_symbol=local_symbol,
        broker_local_symbol=local_symbol,
        underlying_root=root,
        expiry=expiry,
        strike=strike,
        right=right,
        parse_status="ok",
    )


def parse_future_symbol(symbol: str, description: str) -> ParsedSymbol:
    """Outright futures (§2.4) — not present in the fixture. Best-effort root
    parse; falls back to storing the raw string with parse_status='unparsed'."""
    parts = description.strip().split()
    root = parts[0] if parts else symbol.strip()
    return ParsedSymbol(
        asset_class=AssetClass.FUT,
        root_symbol=root,
        broker_symbol=symbol.strip(),
        broker_local_symbol=None,
        underlying_root=root,
        expiry=None,
        strike=None,
        right=None,
        parse_status="unparsed" if not parts else "ok",
    )
