"""§2 — IBKR TradeLog (`.tlg`) parser.

Pipe-delimited, section-oriented. Sections are omitted entirely when empty
(§2.1) — this parser never assumes a section is present. Non-fatal issues
(unknown flags, unparseable symbols) become warnings, not failures (§3.2);
only a structurally broken file raises.
"""

import datetime as dt

from app.models.enums import AssetClass
from app.parsers.base import ParseResult, ParsedAccountInfo, ParsedExecution, ParsedPositionSnapshot
from app.parsers.symbols import (
    parse_equity_option_symbol,
    parse_future_symbol,
    parse_futures_option_symbol,
    parse_stock_symbol,
)

KNOWN_SECTIONS = {
    "ACCOUNT_INFORMATION",
    "STOCK_TRANSACTIONS",
    "OPTION_TRANSACTIONS",
    "FUTURE_TRANSACTIONS",
    "FUTURE_OPTION_TRANSACTIONS",
    "STOCK_POSITIONS",
    "OPTION_POSITIONS",
}

_TRANSACTION_TYPES = {"STK_TRD", "OPT_TRD", "FUT_TRD", "FOP_TRD"}
_POSITION_TYPES = {"STK_LOT", "OPT_LOT"}


class TlgParseError(Exception):
    pass


def _parse_symbol(record_type: str, symbol: str, description: str):
    if record_type == "STK_TRD" or record_type == "STK_LOT":
        return parse_stock_symbol(symbol)
    if record_type == "OPT_TRD" or record_type == "OPT_LOT":
        return parse_equity_option_symbol(symbol, description)
    if record_type == "FUT_TRD":
        return parse_future_symbol(symbol, description)
    if record_type == "FOP_TRD":
        return parse_futures_option_symbol(symbol, description)
    raise TlgParseError(f"Unknown record type for symbol parsing: {record_type}")


def _parse_flags(open_close_code: str) -> list[str]:
    return [f for f in open_close_code.split(";") if f]


_KNOWN_FLAGS = {"O", "C", "Ep", "A", "Ex", "P", "L"}


def parse_tlg(text: str) -> ParseResult:
    result = ParseResult(account=None)
    section = None

    lines = text.splitlines()
    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line == "EOF":
            break

        if "|" not in line:
            if line in KNOWN_SECTIONS:
                section = line
                result.sections_seen.add(section)
            else:
                result.warnings.append(f"Line {lineno}: unrecognized section header {line!r}, ignoring")
                section = None
            continue

        fields = line.split("|")
        record_type = fields[0]

        try:
            if record_type == "ACT_INF":
                result.account = ParsedAccountInfo(account_id=fields[1])
            elif record_type in _TRANSACTION_TYPES:
                result.executions.append(_parse_transaction_row(fields, lineno, result))
            elif record_type in _POSITION_TYPES:
                result.positions.append(_parse_position_row(fields, lineno, result))
            else:
                result.warnings.append(f"Line {lineno}: unknown record type {record_type!r}, skipped")
        except (IndexError, ValueError) as exc:
            raise TlgParseError(f"Line {lineno}: malformed {record_type} row: {exc}") from exc

    return result


def _parse_transaction_row(fields: list[str], lineno: int, result: ParseResult) -> ParsedExecution:
    if len(fields) != 16:
        raise ValueError(f"expected 16 fields, got {len(fields)}: {fields}")

    record_type = fields[0]
    order_id = fields[1]
    symbol_raw = fields[2]
    description = fields[3]
    exchange = fields[4]
    action = fields[5]
    open_close_code = fields[6]
    trade_date = dt.datetime.strptime(fields[7], "%Y%m%d").date()
    trade_time = dt.datetime.strptime(fields[8], "%H:%M:%S").time()
    currency = fields[9]
    quantity = float(fields[10])
    multiplier = float(fields[11])
    price = float(fields[12])
    proceeds = float(fields[13])
    commission = float(fields[14])
    fx_rate = float(fields[15])

    parsed_symbol = _parse_symbol(record_type, symbol_raw, description)
    if parsed_symbol.warning:
        result.warnings.append(f"Line {lineno}: {parsed_symbol.warning}")
    if parsed_symbol.parse_status != "ok":
        result.warnings.append(
            f"Line {lineno}: symbol {symbol_raw!r} ({record_type}) parse_status={parsed_symbol.parse_status}"
        )

    flags = _parse_flags(open_close_code)
    for flag in flags:
        if flag not in _KNOWN_FLAGS:
            result.warnings.append(f"Line {lineno}: unknown open_close_code flag {flag!r}")

    expected_proceeds = quantity * multiplier * price
    if abs(expected_proceeds - proceeds) > 0.01:
        result.warnings.append(
            f"Line {lineno}: proceeds {proceeds} != quantity*multiplier*price {expected_proceeds:.2f}"
        )

    return ParsedExecution(
        source_line=lineno,
        record_type=record_type,
        broker_trade_id=order_id,
        symbol=parsed_symbol,
        description=description,
        exchange=exchange,
        action=action,
        flags=flags,
        trade_date=trade_date,
        trade_time=trade_time,
        currency=currency,
        quantity=quantity,
        multiplier=multiplier,
        price=price,
        proceeds=proceeds,
        commission=commission,
        fx_rate=fx_rate,
    )


def _parse_position_row(fields: list[str], lineno: int, result: ParseResult) -> ParsedPositionSnapshot:
    if len(fields) != 12:
        raise ValueError(f"expected 12 fields, got {len(fields)}: {fields}")

    record_type = fields[0]
    account_id = fields[1]
    symbol_raw = fields[2]
    description = fields[3]
    currency = fields[4]
    # fields[5] open_date, fields[6] open_time — unused (empty in sample, §2.5)
    quantity = float(fields[7])
    multiplier = float(fields[8])
    cost_basis_price = float(fields[9])
    cost_basis_money = float(fields[10])
    fx_rate = float(fields[11])

    parsed_symbol = _parse_symbol(record_type, symbol_raw, description)
    if parsed_symbol.parse_status != "ok":
        result.warnings.append(
            f"Line {lineno}: LOT symbol {symbol_raw!r} ({record_type}) parse_status={parsed_symbol.parse_status}"
        )

    return ParsedPositionSnapshot(
        source_line=lineno,
        record_type=record_type,
        account_id=account_id,
        symbol=parsed_symbol,
        description=description,
        currency=currency,
        quantity=quantity,
        multiplier=multiplier,
        cost_basis_price=cost_basis_price,
        cost_basis_money=cost_basis_money,
        fx_rate=fx_rate,
    )
