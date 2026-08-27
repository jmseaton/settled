"""Synthetic Flex XML for the §5.4 paths the real fixtures cannot reach.

§16.2 says it outright: "Build these against synthetic fixtures now;
validate against a real assignment when one occurs." The account has never
had an assignment, so every one of tests 41-49 needs data that does not
exist yet.

**Built in code rather than checked in as a file.** A hand-written
`fixture-assigned.xml` sitting next to three real broker exports is a
document that looks like a broker export and is not one — and the fixture
README's whole point is that what lives in `fixtures/` came from IBKR and
was scrubbed. Generating it here keeps the distinction sharp, keeps the
scrubbing guard meaningful, and makes each test's scenario readable at the
point it is asserted rather than buried in 200 lines of XML.
"""

import datetime as dt
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

ACCOUNT = "U0000000"


def _attrs(pairs: dict) -> str:
    return " ".join(f"{k}={quoteattr(str(v))}" for k, v in pairs.items() if v is not None)


@dataclass
class Contract:
    """One row of SecuritiesInfo, and the fields every reference to it needs."""

    conid: str
    symbol: str
    asset_category: str  # STK | OPT | FUT | FOP
    description: str = ""
    multiplier: float = 1
    strike: float | None = None
    expiry: dt.date | None = None
    put_call: str | None = None
    underlying_conid: str | None = None
    underlying_symbol: str | None = None

    def security_info(self) -> str:
        return "<SecurityInfo " + _attrs(
            {
                "conid": self.conid,
                "symbol": self.symbol,
                "assetCategory": self.asset_category,
                "description": self.description or self.symbol,
                "multiplier": self.multiplier,
                "strike": "" if self.strike is None else self.strike,
                "expiry": self.expiry.strftime("%Y%m%d") if self.expiry else "",
                "putCall": self.put_call or "",
                "underlyingConid": self.underlying_conid or "",
                "underlyingSymbol": self.underlying_symbol or "",
            }
        ) + " />"


@dataclass
class Fill:
    contract: Contract
    quantity: float
    price: float
    when: dt.datetime
    open_close: str = "O"
    commission: float = 0.0
    transaction_id: str = ""
    order_id: str = ""
    notes: str = ""
    #: CLOSED_LOT rows IB matched against this fill. Emitted immediately
    #: after it, which is how the parser binds them.
    lots: list["Lot"] = field(default_factory=list)

    def xml(self) -> str:
        trade_money = self.quantity * self.contract.multiplier * self.price
        return "<Trade " + _attrs(
            {
                "conid": self.contract.conid,
                "symbol": self.contract.symbol,
                "assetCategory": self.contract.asset_category,
                "description": self.contract.description or self.contract.symbol,
                "multiplier": self.contract.multiplier,
                "tradeID": self.transaction_id,
                "transactionID": self.transaction_id,
                "ibExecID": f"exec-{self.transaction_id}",
                "ibOrderID": self.order_id or self.transaction_id,
                "brokerageOrderID": self.order_id or self.transaction_id,
                "dateTime": self.when.strftime("%Y%m%d;%H%M%S"),
                "tradeDate": self.when.strftime("%Y%m%d"),
                "buySell": "BUY" if self.quantity > 0 else "SELL",
                "openCloseIndicator": self.open_close,
                "quantity": self.quantity,
                "tradePrice": self.price,
                "tradeMoney": trade_money,
                "proceeds": -trade_money,
                "ibCommission": self.commission,
                "netCash": -trade_money + self.commission,
                "currency": "USD",
                "fxRateToBase": 1,
                "exchange": "TEST",
                "notes": self.notes,
                "levelOfDetail": "EXECUTION",
            }
        ) + " />" + "".join("\n" + lot.xml() for lot in self.lots)


@dataclass
class Lot:
    """A CLOSED_LOT row — IB's own FIFO match for a closing fill.

    Attached to the `Fill` it closes, and emitted straight after it, because
    the parser binds a Lot to the Trade that precedes it in document order.

    For an assignment chain this is where IB's convention (b) figure lives:
    the premium is already inside the basis, so `fifo_pnl_realized` on the
    stock's disposal is the whole episode's result.
    """

    contract: Contract
    quantity: float
    cost: float
    fifo_pnl_realized: float
    open_date: dt.date

    def xml(self) -> str:
        return "<Lot " + _attrs(
            {
                "conid": self.contract.conid,
                "symbol": self.contract.symbol,
                "assetCategory": self.contract.asset_category,
                "multiplier": self.contract.multiplier,
                "quantity": self.quantity,
                "tradePrice": 0,
                "cost": self.cost,
                "fifoPnlRealized": self.fifo_pnl_realized,
                "openDateTime": self.open_date.strftime("%Y%m%d;120000"),
                "levelOfDetail": "CLOSED_LOT",
            }
        ) + " />"


@dataclass
class OptionEaeRow:
    contract: Contract
    transaction_type: str  # Assignment | Exercise | Expiration
    when: dt.date
    quantity: float
    realized_pnl: float = 0.0
    cost_basis: float = 0.0
    trade_id: str = ""

    def xml(self) -> str:
        return "<OptionEAE " + _attrs(
            {
                "conid": self.contract.conid,
                "symbol": self.contract.symbol,
                "assetCategory": self.contract.asset_category,
                "description": self.contract.description or self.contract.symbol,
                "multiplier": self.contract.multiplier,
                "strike": self.contract.strike,
                "expiry": self.contract.expiry.strftime("%Y%m%d") if self.contract.expiry else "",
                "putCall": self.contract.put_call or "",
                "underlyingConid": self.contract.underlying_conid or "",
                "underlyingSymbol": self.contract.underlying_symbol or "",
                "date": self.when.strftime("%Y%m%d"),
                "transactionType": self.transaction_type,
                "quantity": self.quantity,
                "tradePrice": 0,
                "markPrice": 0,
                "proceeds": 0,
                "commisionsAndTax": 0,
                "costBasis": self.cost_basis,
                "realizedPnl": self.realized_pnl,
                "tradeID": self.trade_id,
            }
        ) + " />"


@dataclass
class OpenPositionRow:
    contract: Contract
    quantity: float
    cost_basis_price: float
    mark_price: float

    def xml(self) -> str:
        cost_money = self.quantity * self.contract.multiplier * self.cost_basis_price
        mark_money = self.quantity * self.contract.multiplier * self.mark_price
        return "<OpenPosition " + _attrs(
            {
                "conid": self.contract.conid,
                "symbol": self.contract.symbol,
                "assetCategory": self.contract.asset_category,
                "description": self.contract.description or self.contract.symbol,
                "multiplier": self.contract.multiplier,
                "position": self.quantity,
                "costBasisPrice": self.cost_basis_price,
                "costBasisMoney": cost_money,
                "markPrice": self.mark_price,
                "fifoPnlUnrealized": mark_money - cost_money,
                "currency": "USD",
                "fxRateToBase": 1,
                "levelOfDetail": "LOT",
            }
        ) + " />"


@dataclass
class CorporateActionRow:
    contract: Contract | None
    when: dt.date
    action_type: str
    description: str
    transaction_id: str = ""

    def xml(self) -> str:
        return "<CorporateAction " + _attrs(
            {
                "conid": self.contract.conid if self.contract else "",
                "symbol": self.contract.symbol if self.contract else "",
                "assetCategory": self.contract.asset_category if self.contract else "",
                "dateTime": self.when.strftime("%Y%m%d;120000"),
                "reportDate": self.when.strftime("%Y%m%d"),
                "type": self.action_type,
                "description": self.description,
                "quantity": 0,
                "proceeds": 0,
                "value": 0,
                "transactionID": self.transaction_id,
            }
        ) + " />"


@dataclass
class Statement:
    from_date: dt.date
    to_date: dt.date
    contracts: list[Contract] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    option_events: list[OptionEaeRow] = field(default_factory=list)
    positions: list[OpenPositionRow] = field(default_factory=list)
    corporate_actions: list[CorporateActionRow] = field(default_factory=list)
    account_id: str = ACCOUNT

    def xml(self) -> str:
        contracts = {c.conid: c for c in self.contracts}
        for source in (self.fills, self.option_events, self.positions):
            for row in source:
                contracts.setdefault(row.contract.conid, row.contract)
        for action in self.corporate_actions:
            if action.contract:
                contracts.setdefault(action.contract.conid, action.contract)

        return f"""<FlexQueryResponse queryName="synthetic" type="AF">
<FlexStatements count="1">
<FlexStatement accountId="{self.account_id}" fromDate="{self.from_date:%Y%m%d}" toDate="{self.to_date:%Y%m%d}" period="LastNCalendarDays" whenGenerated="{self.to_date:%Y%m%d};120000">
<AccountInformation accountId="{self.account_id}" currency="USD" />
<SecuritiesInfo>
{chr(10).join(c.security_info() for c in contracts.values())}
</SecuritiesInfo>
<Trades>
{chr(10).join(f.xml() for f in self.fills)}
</Trades>
<OptionEAE>
{chr(10).join(e.xml() for e in self.option_events)}
</OptionEAE>
<OpenPositions>
{chr(10).join(p.xml() for p in self.positions)}
</OpenPositions>
<CorporateActions>
{chr(10).join(a.xml() for a in self.corporate_actions)}
</CorporateActions>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""

    def as_bytes(self) -> bytes:
        return self.xml().encode("utf-8")


# --- Contracts the assignment scenarios reuse -------------------------------

ACME_STOCK = Contract(conid="700000901", symbol="ACME", asset_category="STK", description="ACME SEMICONDUCTOR CORP")
ACME_PUT_100 = Contract(
    conid="700100",
    symbol="ACME  310919P00100000",
    asset_category="OPT",
    description="ACME 19SEP31 100 P",
    multiplier=100,
    strike=100.0,
    expiry=dt.date(2031, 9, 19),
    put_call="P",
    underlying_conid="700000901",
    underlying_symbol="ACME",
)
ACME_CALL_120 = Contract(
    conid="700120",
    symbol="ACME  310919C00120000",
    asset_category="OPT",
    description="ACME 19SEP31 120 C",
    multiplier=100,
    strike=120.0,
    expiry=dt.date(2031, 9, 19),
    put_call="C",
    underlying_conid="700000901",
    underlying_symbol="ACME",
)
MES_FUTURE = Contract(
    conid="700793356",
    symbol="MESU1",
    asset_category="FUT",
    description="MICRO E-MINI S&P 500 SEP31",
    multiplier=5,
    underlying_symbol="MESU1",
)
MES_PUT_7000 = Contract(
    conid="800700",
    symbol="EXU1 P7000",
    asset_category="FOP",
    description="MES 01SEP31 7000 P",
    multiplier=5,
    strike=7000.0,
    expiry=dt.date(2031, 9, 1),
    put_call="P",
    underlying_conid="700793356",
    underlying_symbol="MESU1",
)
SPX_PUT_5000 = Contract(
    conid="900500",
    symbol="SPXW  310919P05000000",
    asset_category="OPT",
    description="SPX 19SEP31 5000 P",
    multiplier=100,
    strike=5000.0,
    expiry=dt.date(2031, 9, 19),
    put_call="P",
    underlying_conid="700416904",
    underlying_symbol="SPX",
)
