"""§3.9 — Activity Flex Query XML parser.

Deliberately shares **no arithmetic** with the `.tlg` parser (§3.9). The two
formats both ship a field called `proceeds` and they are negatives of each
other; the only safe design is to normalize at each parser's boundary and
derive everything downstream from the canonical form.

    canonical `proceeds`  ==  Flex tradeMoney  ==  quantity x multiplier x price
    Flex proceeds         == -tradeMoney            (cash-flow sign: sells positive)
    Flex netCash          ==  proceeds + ibCommission

Instrument metadata comes from `SecuritiesInfo`, so none of §2.4's symbol
parsing runs on this path — including the futures-option local symbols that
are not reliably parseable at all.
"""

import datetime as dt
import xml.etree.ElementTree as ET

from app.models.enums import AssetClass, Right
from app.parsers.base import (
    ParsedAccountInfo,
    ParsedCashTransaction,
    ParsedClosedLot,
    ParsedCorporateAction,
    ParsedExecution,
    ParsedNav,
    ParsedOptionEvent,
    ParsedPositionSnapshot,
    ParsedTransfer,
    ParseResult,
)
from app.parsers.symbols import ParsedSymbol

_ASSET_CATEGORIES = {"STK": AssetClass.STK, "OPT": AssetClass.OPT, "FUT": AssetClass.FUT, "FOP": AssetClass.FOP}

_BUYSELL_TO_ACTION = {
    ("BUY", "O"): "BUYTOOPEN",
    ("BUY", "C"): "BUYTOCLOSE",
    ("SELL", "O"): "SELLTOOPEN",
    ("SELL", "C"): "SELLTOCLOSE",
}


class FlexParseError(Exception):
    pass


def _f(value: str | None, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    return float(value)


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    head = value.split(";")[0].split(" ")[0]
    try:
        return dt.datetime.strptime(head, "%Y%m%d").date()
    except ValueError:
        return None


def _datetime(value: str | None) -> tuple[dt.date | None, dt.time | None]:
    """Flex `dateTime` is `yyyyMMdd;HHmmss` under the §3.9 format settings,
    but the separator is configurable, so accept a space too."""
    if not value:
        return None, None
    parts = value.replace(";", " ").split()
    date = _date(parts[0]) if parts else None
    time = None
    if len(parts) > 1:
        raw = parts[1].replace(":", "")
        if len(raw) == 6:
            time = dt.time(int(raw[0:2]), int(raw[2:4]), int(raw[4:6]))
    return date, time


def _build_instrument_index(statement: ET.Element, warnings: list[str]) -> dict[str, ParsedSymbol]:
    """§3.9 — `SecurityInfo` keyed on conid supplies every contract field
    directly, eliminating symbol parsing entirely."""
    index: dict[str, ParsedSymbol] = {}
    for si in statement.findall("./SecuritiesInfo/SecurityInfo"):
        conid = si.get("conid") or ""
        category = si.get("assetCategory") or ""
        asset_class = _ASSET_CATEGORIES.get(category)
        if asset_class is None:
            warnings.append(f"SecurityInfo conid={conid}: unknown assetCategory {category!r}, skipped")
            continue

        put_call = si.get("putCall") or ""
        underlying_symbol = si.get("underlyingSymbol") or None
        symbol = si.get("symbol") or ""

        # §2.4: store both the specific underlying contract (MESU1) and a
        # derived root (MES), so grouping never merges futures months.
        if asset_class in (AssetClass.FOP, AssetClass.FUT):
            underlying_root = _futures_root(underlying_symbol) or _description_root(si.get("description"))
        else:
            underlying_root = underlying_symbol or symbol.split()[0] if symbol else None

        index[conid] = ParsedSymbol(
            asset_class=asset_class,
            root_symbol=(underlying_root or symbol.split()[0] if symbol else symbol) or symbol,
            broker_symbol=symbol,
            broker_local_symbol=symbol if asset_class == AssetClass.FOP else None,
            underlying_root=underlying_root,
            expiry=_date(si.get("expiry")),
            strike=_f(si.get("strike")),
            right=Right(put_call) if put_call in ("C", "P") else None,
            parse_status="ok",
            underlying_symbol=underlying_symbol,
            conid=conid,
            underlying_conid=si.get("underlyingConid") or None,
        )
    return index


def _futures_root(underlying_symbol: str | None) -> str | None:
    """`MESU1` -> `MES`. Strips a trailing month-code + year-digit pair."""
    if not underlying_symbol:
        return None
    if len(underlying_symbol) >= 3 and underlying_symbol[-1].isdigit() and underlying_symbol[-2].isalpha():
        return underlying_symbol[:-2]
    return underlying_symbol


def _description_root(description: str | None) -> str | None:
    return description.split()[0] if description else None


def parse_flex(text: str) -> ParseResult:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FlexParseError(f"Malformed Flex XML: {exc}") from exc

    statement = root.find(".//FlexStatement")
    if statement is None:
        status = root.findtext(".//Status")
        if status and status.lower() == "fail":
            code = root.findtext(".//ErrorCode")
            message = root.findtext(".//ErrorMessage")
            raise FlexParseError(f"Flex request failed (code {code}): {message}")
        raise FlexParseError("No FlexStatement element found")

    result = ParseResult(account=None)
    warnings = result.warnings

    account_id = statement.get("accountId")
    if not account_id:
        raise FlexParseError("FlexStatement carries no accountId")
    result.account = ParsedAccountInfo(account_id=account_id)
    result.period_start = _date(statement.get("fromDate"))
    result.period_end = _date(statement.get("toDate"))

    instruments = _build_instrument_index(statement, warnings)

    def resolve(node: ET.Element, line: int) -> ParsedSymbol | None:
        conid = node.get("conid") or ""
        parsed = instruments.get(conid)
        if parsed is None:
            warnings.append(
                f"Line {line}: conid={conid!r} ({node.get('symbol')!r}) missing from SecuritiesInfo; "
                "row skipped. Add Financial Instrument Information to the Flex query (§3.9)."
            )
        return parsed

    _parse_trades(statement, result, resolve)
    _parse_open_positions(statement, result, resolve, account_id)
    _parse_transfers(statement, result, resolve)
    _parse_cash_transactions(statement, result)
    _parse_option_events(statement, result, resolve)
    _parse_corporate_actions(statement, result, resolve)

    nav = statement.find("ChangeInNAV")
    if nav is not None:
        result.sections_seen.add("ChangeInNAV")
        result.nav = ParsedNav(
            starting_value=_f(nav.get("startingValue")),
            ending_value=_f(nav.get("endingValue")),
            twr=_f(nav.get("twr")),
            realized=_f(nav.get("realized")),
            transferred_pnl_adjustments=_f(nav.get("transferredPnlAdjustments")),
        )

    return result


def _parse_trades(statement: ET.Element, result: ParseResult, resolve) -> None:
    trades = statement.find("Trades")
    if trades is None:
        return
    result.sections_seen.add("Trades")

    current_closing_id: str | None = None
    for line, node in enumerate(trades, start=1):
        if node.tag == "Trade":
            execution = _parse_trade_row(node, line, result, resolve)
            # Bind lots by transactionID, not ibOrderID: a Lot's parent is a
            # specific *execution*, and one order can produce several.
            current_closing_id = (
                (execution.transaction_id or execution.broker_trade_id) if execution else None
            )
            continue

        if node.tag != "Lot":
            continue

        result.sections_seen.add("Trades:CLOSED_LOT")
        parsed = resolve(node, line)
        if parsed is None:
            continue
        if current_closing_id is None:
            result.warnings.append(f"Line {line}: CLOSED_LOT row with no preceding Trade, skipped")
            continue

        result.closed_lots.append(
            ParsedClosedLot(
                source_line=line,
                closing_broker_trade_id=current_closing_id,
                opening_transaction_id=node.get("transactionID") or None,
                symbol=parsed,
                quantity=_f(node.get("quantity"), 0.0),
                trade_price=_f(node.get("tradePrice"), 0.0),
                cost=_f(node.get("cost"), 0.0),
                fifo_pnl_realized=_f(node.get("fifoPnlRealized"), 0.0),
                open_datetime=_date(node.get("openDateTime")),
            )
        )


def _parse_trade_row(node: ET.Element, line: int, result: ParseResult, resolve) -> ParsedExecution | None:
    if (node.get("levelOfDetail") or "EXECUTION") != "EXECUTION":
        return None

    parsed = resolve(node, line)
    if parsed is None:
        return None

    trade_date, trade_time = _datetime(node.get("dateTime"))
    if trade_date is None:
        trade_date = _date(node.get("tradeDate"))
    if trade_date is None:
        result.warnings.append(f"Line {line}: trade with no usable date, skipped")
        return None
    if trade_time is None:
        trade_time = dt.time(0, 0, 0)

    buy_sell = (node.get("buySell") or "").upper()
    open_close = (node.get("openCloseIndicator") or "").upper()
    action = _BUYSELL_TO_ACTION.get((buy_sell, open_close))
    if action is None:
        result.warnings.append(
            f"Line {line}: cannot map buySell={buy_sell!r} + openCloseIndicator={open_close!r} to an action, skipped"
        )
        return None

    # §3.9 notes carries the same flag vocabulary as the .tlg open_close_code.
    flags = [f for f in (node.get("notes") or "").split(";") if f]
    if open_close and open_close not in flags:
        flags.insert(0, open_close)

    quantity = _f(node.get("quantity"), 0.0)
    multiplier = _f(node.get("multiplier"), 1.0)
    price = _f(node.get("tradePrice"), 0.0)
    trade_money = _f(node.get("tradeMoney"))
    proceeds = trade_money if trade_money is not None else quantity * multiplier * price

    expected = quantity * multiplier * price
    if abs(expected - proceeds) > 0.01:
        result.warnings.append(
            f"Line {line}: tradeMoney {proceeds} != quantity*multiplier*price {expected:.2f}"
        )

    # Cross-check the documented sign relationship rather than assuming it.
    flex_proceeds = _f(node.get("proceeds"))
    if flex_proceeds is not None and abs(flex_proceeds + proceeds) > 0.01:
        result.warnings.append(
            f"Line {line}: expected Flex proceeds == -tradeMoney, got {flex_proceeds} vs {proceeds}"
        )

    execution = ParsedExecution(
        source_line=line,
        record_type=f"FLEX_{parsed.asset_class.value}",
        broker_trade_id=node.get("ibOrderID") or node.get("transactionID") or "",
        symbol=parsed,
        description=node.get("description") or "",
        exchange=node.get("exchange") or "--",
        action=action,
        flags=flags,
        trade_date=trade_date,
        trade_time=trade_time,
        currency=node.get("currency") or "USD",
        quantity=quantity,
        multiplier=multiplier,
        price=price,
        proceeds=proceeds,
        commission=_f(node.get("ibCommission"), 0.0),
        fx_rate=_f(node.get("fxRateToBase"), 1.0),
        transaction_id=node.get("transactionID") or None,
        ib_exec_id=node.get("ibExecID") or None,
        brokerage_order_id=node.get("brokerageOrderID") or None,
    )
    result.executions.append(execution)
    return execution


def _parse_open_positions(statement: ET.Element, result: ParseResult, resolve, account_id: str) -> None:
    positions = statement.find("OpenPositions")
    if positions is None:
        return
    result.sections_seen.add("OpenPositions")

    for line, node in enumerate(positions.findall("OpenPosition"), start=1):
        parsed = resolve(node, line)
        if parsed is None:
            continue
        quantity = _f(node.get("position"), 0.0)
        multiplier = _f(node.get("multiplier"), 1.0)
        cost_basis_money = _f(node.get("costBasisMoney"))
        cost_basis_price = _f(node.get("costBasisPrice"))
        if cost_basis_price is None and cost_basis_money is not None and quantity and multiplier:
            cost_basis_price = cost_basis_money / (quantity * multiplier)

        # §12 — `markPrice` is present on every row; `closePrice` is not
        # populated intraday. Prefer the mark and fall back to the close, so
        # a statement pulled mid-session still values the book.
        mark_price = _f(node.get("markPrice"))
        close_price = _f(node.get("closePrice"))

        result.positions.append(
            ParsedPositionSnapshot(
                source_line=line,
                record_type=f"FLEX_{parsed.asset_class.value}_LOT",
                account_id=account_id,
                symbol=parsed,
                description=node.get("description") or "",
                currency=node.get("currency") or "USD",
                quantity=quantity,
                multiplier=multiplier,
                cost_basis_price=cost_basis_price or 0.0,
                cost_basis_money=cost_basis_money or 0.0,
                fx_rate=_f(node.get("fxRateToBase"), 1.0),
                mark_price=mark_price if mark_price is not None else close_price,
                close_price=close_price,
                unrealized_pnl=_f(node.get("fifoPnlUnrealized")),
            )
        )


def _parse_transfers(statement: ET.Element, result: ParseResult, resolve) -> None:
    transfers = statement.find("Transfers")
    if transfers is None:
        return
    result.sections_seen.add("Transfers")

    for line, node in enumerate(transfers.findall("Transfer"), start=1):
        parsed = resolve(node, line)
        if parsed is None:
            continue
        transferred_on = _date(node.get("date")) or _date(node.get("dateTime"))
        if transferred_on is None:
            result.warnings.append(f"Line {line}: Transfer with no usable date, skipped")
            continue

        # §5.5 caveat, confirmed live: transferPrice is 0 and cannot be relied
        # on. Derive basis from cost/quantity and the mark from
        # positionAmount/quantity instead.
        transfer_price = _f(node.get("transferPrice"), 0.0)
        if transfer_price == 0:
            result.warnings.append(
                f"Line {line}: Transfer {node.get('symbol')!r} reports transferPrice=0; "
                "using positionAmount/quantity for the transfer mark (§5.5)"
            )

        result.transfers.append(
            ParsedTransfer(
                source_line=line,
                symbol=parsed,
                transferred_on=transferred_on,
                direction=(node.get("direction") or "").upper(),
                transfer_type=(node.get("type") or "OTHER").upper(),
                quantity=_f(node.get("quantity"), 0.0),
                transfer_price=transfer_price or 0.0,
                position_amount=_f(node.get("positionAmount"), 0.0),
                original_cost_basis=_f(node.get("cost")),
                broker_transaction_id=node.get("transactionID") or None,
            )
        )


def _parse_cash_transactions(statement: ET.Element, result: ParseResult) -> None:
    cash = statement.find("CashTransactions")
    if cash is None:
        return
    result.sections_seen.add("CashTransactions")

    for line, node in enumerate(cash.findall("CashTransaction"), start=1):
        occurred_on = _date(node.get("dateTime")) or _date(node.get("settleDate")) or _date(node.get("reportDate"))
        if occurred_on is None:
            result.warnings.append(f"Line {line}: CashTransaction with no usable date, skipped")
            continue
        result.cash_transactions.append(
            ParsedCashTransaction(
                source_line=line,
                occurred_on=occurred_on,
                type=node.get("type") or "ADJUSTMENT",
                amount=_f(node.get("amount"), 0.0),
                currency=node.get("currency") or "USD",
                description=node.get("description") or "",
                broker_transaction_id=node.get("transactionID") or None,
            )
        )


#: §5.4 — IB's `transactionType` values in the OptionEAE section, normalized.
#: Anything unrecognized is carried through verbatim rather than coerced:
#: mislabelling an assignment as an expiration would silently drop a futures
#: position from the chain.
_EAE_TYPES = {
    "assignment": "ASSIGNMENT",
    "assignments": "ASSIGNMENT",
    "exercise": "EXERCISE",
    "exercises": "EXERCISE",
    "expiration": "EXPIRATION",
    "expirations": "EXPIRATION",
    "expire": "EXPIRATION",
}


def normalize_eae_type(raw: str) -> str:
    return _EAE_TYPES.get(" ".join((raw or "").split()).lower(), (raw or "UNKNOWN").upper())


def _parse_option_events(statement: ET.Element, result: ParseResult, resolve) -> None:
    """§5.4 — "Options, Exercises, Assignments and Expirations".

    The authoritative source. A Trade row's `notes` flag says something
    happened to an option; this section says which of the three it was, what
    the underlying contract is, and what IB realized on it. Both are read,
    because the flag travels with the execution while this section can lag
    by a statement.
    """
    section = statement.find("OptionEAE")
    if section is None:
        return
    result.sections_seen.add("OptionEAE")

    for line, node in enumerate(section, start=1):
        parsed = resolve(node, line)
        if parsed is None:
            continue
        occurred_on = _date(node.get("date")) or _date(node.get("dateTime"))
        if occurred_on is None:
            result.warnings.append(f"Line {line}: OptionEAE row with no usable date, skipped")
            continue

        result.option_events.append(
            ParsedOptionEvent(
                source_line=line,
                symbol=parsed,
                occurred_on=occurred_on,
                transaction_type=normalize_eae_type(node.get("transactionType") or ""),
                quantity=_f(node.get("quantity"), 0.0),
                trade_price=_f(node.get("tradePrice"), 0.0),
                proceeds=_f(node.get("proceeds"), 0.0),
                # IB spells this attribute "commisionsAndTax" — one 's'.
                # Verified against the fixture; do not "correct" it.
                commission=_f(node.get("commisionsAndTax"), 0.0),
                cost_basis=_f(node.get("costBasis"), 0.0),
                realized_pnl=_f(node.get("realizedPnl"), 0.0),
                broker_trade_id=node.get("tradeID") or None,
                underlying_conid=node.get("underlyingConid") or None,
                underlying_symbol=node.get("underlyingSymbol") or None,
            )
        )


def _parse_corporate_actions(statement: ET.Element, result: ParseResult, resolve) -> None:
    """§11.3 — splits and symbol changes.

    The section is empty in the fixture, so this path is exercised only by
    synthetic data. It is parsed rather than skipped because a split that
    arrives unparsed silently corrupts every historical quantity for that
    symbol, and the first sign of it is a reconciliation failure weeks later.
    """
    section = statement.find("CorporateActions")
    if section is None:
        return
    result.sections_seen.add("CorporateActions")

    for line, node in enumerate(section, start=1):
        occurred_on = _date(node.get("dateTime")) or _date(node.get("reportDate"))
        if occurred_on is None:
            result.warnings.append(f"Line {line}: CorporateAction with no usable date, skipped")
            continue
        # A corporate action can reference a contract that never traded in
        # this account, so a missing instrument is not fatal here.
        parsed = resolve(node, line) if node.get("conid") else None

        result.corporate_actions.append(
            ParsedCorporateAction(
                source_line=line,
                symbol=parsed,
                occurred_on=occurred_on,
                action_type=(node.get("type") or "OTHER").upper(),
                description=node.get("description") or "",
                quantity=_f(node.get("quantity"), 0.0),
                proceeds=_f(node.get("proceeds"), 0.0),
                value=_f(node.get("value"), 0.0),
                broker_transaction_id=node.get("transactionID") or None,
            )
        )
