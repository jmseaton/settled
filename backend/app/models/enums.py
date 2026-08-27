import enum


class AssetClass(str, enum.Enum):
    STK = "STK"
    OPT = "OPT"
    FUT = "FUT"
    FOP = "FOP"


class Right(str, enum.Enum):
    CALL = "C"
    PUT = "P"


class ExerciseStyle(str, enum.Enum):
    AMERICAN = "AMERICAN"
    EUROPEAN = "EUROPEAN"
    UNKNOWN = "UNKNOWN"


class Action(str, enum.Enum):
    BUYTOOPEN = "BUYTOOPEN"
    SELLTOOPEN = "SELLTOOPEN"
    BUYTOCLOSE = "BUYTOCLOSE"
    SELLTOCLOSE = "SELLTOCLOSE"


class ImportSource(str, enum.Enum):
    FLEX_SYNC = "FLEX_SYNC"
    FILE_UPLOAD = "FILE_UPLOAD"


class SourceFormat(str, enum.Enum):
    TLG = "TLG"
    FLEX_XML = "FLEX_XML"


class ImportStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class TradeSide(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIAL = "PARTIAL"
    ORPHAN_CLOSE = "ORPHAN_CLOSE"


class Origin(str, enum.Enum):
    TRADED = "TRADED"
    TRANSFERRED = "TRANSFERRED"
    PRE_EPOCH = "PRE_EPOCH"
    MANUAL = "MANUAL"
    #: §5.5 case (a) — the opening leg was rebuilt from IB's closed-lot cost
    #: because the real fill falls outside the imported window. Distinct from
    #: TRANSFERRED, which it used to borrow: a transfer has *no* opening
    #: execution in this account and never will, while this one exists and
    #: is simply not imported yet, so more history fixes it. They also differ
    #: in whether IB's figures are comparable — see `check_closed_lots`.
    WINDOW_TRUNCATED = "WINDOW_TRUNCATED"


class LegRole(str, enum.Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class StrategyType(str, enum.Enum):
    """§6.2. v1 (Phase 1+2) has no spread grouping, so option legs are
    UNGROUPED — see the note in trades/constructor.py for why a leg is not
    labelled as a strategy. The naked/vertical members exist for §6 to
    populate once grouping lands."""

    UNGROUPED = "UNGROUPED"
    VERTICAL_PUT = "VERTICAL_PUT"
    VERTICAL_CALL = "VERTICAL_CALL"
    CALENDAR = "CALENDAR"
    DIAGONAL = "DIAGONAL"
    STRADDLE = "STRADDLE"
    STRANGLE = "STRANGLE"
    NAKED_SHORT_PUT = "NAKED_SHORT_PUT"
    NAKED_SHORT_CALL = "NAKED_SHORT_CALL"
    LONG_SINGLE = "LONG_SINGLE"
    LONG_STOCK = "LONG_STOCK"
    SHORT_STOCK = "SHORT_STOCK"
    #: §4.6 / §5.4 — the original spread, the assigned leg, the resulting
    #: underlying position and its disposal, as one analytical unit. Not a
    #: strategy anyone chose; a strategy that happened *to* them, which is
    #: why it needs its own label rather than being filed under CUSTOM.
    ASSIGNMENT_CHAIN = "ASSIGNMENT_CHAIN"
    CUSTOM = "CUSTOM"


class Book(str, enum.Enum):
    HOLDINGS = "Holdings"
    OPTIONS_INCOME = "Options Income"


class AnalysisMode(str, enum.Enum):
    """§6.4 — the global Analyze by Leg / Analyze by Strategy toggle.

    Totals are identical either way: the sum of legs equals the sum of
    groups. What changes is every *per-unit* figure — win rate, expectancy,
    average P&L, trade count, duration — because a vertical is one outcome
    as a strategy and one win plus one loss as legs.

    STRATEGY is the default: the spread is the decision that was actually
    made, which is the argument §6 opens with.
    """

    STRATEGY = "STRATEGY"
    LEG = "LEG"
