import datetime as dt
from dataclasses import dataclass, field

from app.parsers.symbols import ParsedSymbol


@dataclass
class ParsedAccountInfo:
    account_id: str
    # Name/address are intentionally not carried past the parser (§2.1 privacy note).


@dataclass
class ParsedExecution:
    source_line: int
    record_type: str  # STK_TRD | OPT_TRD | FUT_TRD | FOP_TRD
    broker_trade_id: str
    symbol: ParsedSymbol
    description: str
    exchange: str
    action: str
    flags: list[str]
    trade_date: dt.date
    trade_time: dt.time
    currency: str
    quantity: float
    multiplier: float
    price: float
    proceeds: float
    commission: float
    fx_rate: float
    # Flex-only identifiers (§3.2 dedupe ladder, §6.1 combo grouping). The
    # `.tlg` carries none of these — it exposes only the order ID.
    transaction_id: str | None = None
    ib_exec_id: str | None = None
    brokerage_order_id: str | None = None


@dataclass
class ParsedPositionSnapshot:
    source_line: int
    record_type: str  # STK_LOT | OPT_LOT
    account_id: str
    symbol: ParsedSymbol
    description: str
    currency: str
    quantity: float
    multiplier: float
    cost_basis_price: float
    cost_basis_money: float
    fx_rate: float
    # §12 — Flex already carries daily marks on Open Positions, so unrealized
    # P&L and a true account value need no external vendor. `mark_price` is
    # the live mark; `close_price` the official close, which is absent
    # intraday. IB's own `fifoPnlUnrealized` is carried alongside as the
    # cross-check rather than recomputed and trusted blindly.
    mark_price: float | None = None
    close_price: float | None = None
    unrealized_pnl: float | None = None


@dataclass
class ParsedClosedLot:
    """Flex `levelOfDetail=CLOSED_LOT` — IB's own FIFO lot matching (§3.4).

    `closing_broker_trade_id` is the parent closing execution's ID (a Lot
    follows its Trade in document order). `opening_transaction_id` points at
    the *opening* execution and is empty when that opening falls outside the
    query window — which is precisely the §5.5 shortcut: the lot still
    carries the cost basis even though the opening execution is absent.
    """

    source_line: int
    closing_broker_trade_id: str
    opening_transaction_id: str | None
    symbol: ParsedSymbol
    quantity: float
    trade_price: float
    cost: float  # opening notional + opening commission
    fifo_pnl_realized: float  # net of the closing commission
    open_datetime: dt.date | None


@dataclass
class ParsedTransfer:
    """Flex Transfers (ACATS, Internal) — §4.7b, §5.5."""

    source_line: int
    symbol: ParsedSymbol
    transferred_on: dt.date
    direction: str  # IN | OUT
    transfer_type: str  # ACATS | INTERNAL | ...
    quantity: float
    transfer_price: float  # unreliable; 0 in practice (§5.5 caveat)
    position_amount: float  # market value on the transfer date
    original_cost_basis: float | None  # delivering broker's carried-over basis
    broker_transaction_id: str | None


@dataclass
class ParsedCashTransaction:
    source_line: int
    occurred_on: dt.date
    type: str
    amount: float
    currency: str
    description: str
    broker_transaction_id: str | None


@dataclass
class ParsedOptionEvent:
    """Flex "Options, Exercises, Assignments and Expirations" — §5.4's
    **authoritative** source.

    The `notes` flags on a Trade row (`A`, `Ex`, `Ep`) say that something
    happened; this section says *what*, with IB's own realized figure and
    the underlying contract attached. Both are parsed, because the flags
    arrive with the execution and this section can lag by a statement —
    §5.4's "assignment appears in the *next* statement".
    """

    source_line: int
    symbol: ParsedSymbol
    occurred_on: dt.date
    #: Assignment | Exercise | Expiration, verbatim from `transactionType`.
    transaction_type: str
    quantity: float
    trade_price: float
    proceeds: float
    commission: float
    cost_basis: float
    realized_pnl: float
    broker_trade_id: str | None
    underlying_conid: str | None
    underlying_symbol: str | None


@dataclass
class ParsedCorporateAction:
    """§11.3 — splits and symbol changes.

    Only the split ratio is acted on: it rewrites historical quantities and
    prices for a symbol from a date, which every downstream figure depends
    on. Other action types are recorded and reported rather than applied,
    because silently mis-applying a spin-off is worse than not handling one.
    """

    source_line: int
    symbol: ParsedSymbol | None
    occurred_on: dt.date
    action_type: str
    description: str
    quantity: float
    proceeds: float
    value: float
    broker_transaction_id: str | None


@dataclass
class ParsedNav:
    """ChangeInNAV — validates §7.2-7.3 and backs the §0.3 cross-check."""

    starting_value: float | None
    ending_value: float | None
    twr: float | None
    realized: float | None
    transferred_pnl_adjustments: float | None


@dataclass
class ParseResult:
    account: ParsedAccountInfo | None
    executions: list[ParsedExecution] = field(default_factory=list)
    positions: list[ParsedPositionSnapshot] = field(default_factory=list)
    closed_lots: list[ParsedClosedLot] = field(default_factory=list)
    transfers: list[ParsedTransfer] = field(default_factory=list)
    cash_transactions: list[ParsedCashTransaction] = field(default_factory=list)
    option_events: list[ParsedOptionEvent] = field(default_factory=list)
    corporate_actions: list[ParsedCorporateAction] = field(default_factory=list)
    nav: ParsedNav | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    warnings: list[str] = field(default_factory=list)
    sections_seen: set[str] = field(default_factory=set)
