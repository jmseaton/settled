"""Appendix D — static contract reference for MES (and, later, ES).

Confidence levels from the spec are preserved in comments. Only what §8.4
(points/ticks) and §5.4/§9.5 (exercise style) need is modeled; nothing here
reads live market data.
"""

from dataclasses import dataclass

from app.models.enums import ExerciseStyle


@dataclass(frozen=True)
class FuturesSpec:
    root: str
    multiplier: float  # $ per index point
    tick_size: float  # index points
    tick_value: float  # $ per tick


@dataclass(frozen=True)
class FuturesOptionTickSpec:
    root: str
    tick_size_low: float  # premium <= threshold
    tick_value_low: float
    tick_size_high: float  # premium > threshold
    tick_value_high: float
    threshold: float


# D.1 — Confirmed.
FUTURES_SPECS: dict[str, FuturesSpec] = {
    "MES": FuturesSpec(root="MES", multiplier=5.0, tick_size=0.25, tick_value=1.25),
    "ES": FuturesSpec(root="ES", multiplier=50.0, tick_size=0.25, tick_value=12.50),
}

# D.3 — MES tick value is inferred by 1/10 scaling from ES; flagged for
# confirmation in the spec before it drives a number that matters. Cosmetic
# only (points/ticks display), never affects dollar P&L.
FUTURES_OPTION_TICK_SPECS: dict[str, FuturesOptionTickSpec] = {
    "MES": FuturesOptionTickSpec(
        root="MES", tick_size_low=0.05, tick_value_low=0.25, tick_size_high=0.25, tick_value_high=1.25, threshold=5.0
    ),
    "ES": FuturesOptionTickSpec(
        root="ES", tick_size_low=0.05, tick_value_low=2.50, tick_size_high=0.25, tick_value_high=12.50, threshold=5.0
    ),
}

# Quarterly AM-expiry contract month codes (Mar/Jun/Sep/Dec): H, M, U, Z.
_QUARTERLY_MONTH_CODES = {"H", "M", "U", "Z"}


def resolve_fop_exercise_style(local_symbol: str | None, root: str) -> tuple[ExerciseStyle, bool]:
    """§5.4 / Appendix D.2 — MES/ES weekly & EOM options are European with no
    early assignment; quarterly AM-expiry options are American.

    The `.tlg` local symbol (e.g. `EX1Q6 P7120`, `X3DN6 P7410`, `EXM6 P7170`)
    encodes an internal weekly/EOM series code per §2.4 and is not fully
    documented, so this is a heuristic, not a lookup table sourced from CME:
    a local symbol whose *root token* (before the strike) matches the bare
    `<ROOT><MonthCode><YearDigit>` quarterly form (e.g. `MESU1`) is treated
    as the American quarterly; every other pattern seen in practice for
    MES/ES is a weekly or EOM series and is European.

    Returns (style, matched) — `matched=False` means the pattern was not
    recognized at all and the caller should surface an import warning
    (§16.5 test 67) rather than silently guessing.
    """
    if root not in FUTURES_SPECS:
        return ExerciseStyle.UNKNOWN, False
    if not local_symbol:
        return ExerciseStyle.UNKNOWN, False

    series_token = local_symbol.split()[0] if local_symbol.split() else local_symbol

    # Bare quarterly form: root + one month-code letter + one digit, nothing else.
    if (
        series_token.startswith(root)
        and len(series_token) == len(root) + 2
        and series_token[len(root)] in _QUARTERLY_MONTH_CODES
        and series_token[len(root) + 1].isdigit()
    ):
        return ExerciseStyle.AMERICAN, True

    # Weekly/EOM series codes observed in practice: EX<n><letter><digit>,
    # EX<letter><digit> (EOM), or <letter><digit><letters><digit> (e.g. X1BN6).
    if series_token.startswith("EX") or (series_token and series_token[0].isalpha() and any(c.isdigit() for c in series_token)):
        return ExerciseStyle.EUROPEAN, True

    return ExerciseStyle.UNKNOWN, False


# §5.4 — US equity options traded in the fixture are American-style.
EQUITY_OPTION_EXERCISE_STYLE = ExerciseStyle.AMERICAN


#: §4.2 settlement values.
SETTLEMENT_PHYSICAL = "PHYSICAL"
SETTLEMENT_CASH = "CASH"
SETTLEMENT_INTO_FUTURE = "INTO_FUTURE"

#: Cash-settled index options. An assignment on one of these produces a cash
#: adjustment at intrinsic value and **no position** (§5.4, test 45), so the
#: distinction has to be made before the linker goes looking for a holding
#: that was never created. European-style index products only; single-name
#: equity options are physically settled.
CASH_SETTLED_INDEX_ROOTS = frozenset(
    {"SPX", "SPXW", "XSP", "NDX", "NDXP", "RUT", "VIX", "VIXW", "DJX", "MRUT", "XND"}
)


def resolve_settlement(parsed) -> str | None:
    """§4.2 / Appendix D.4 — how this contract settles.

    Futures options settle **into a futures position**, not to cash: CME
    calls MES options "financially settled" while also stating that exercise
    results in a position in the underlying futures contract, and D.4
    reconciles the two — the option exercises into a future, and that future
    is itself cash-settled later. So a naked short MES put finishing ITM
    produces a long MES futures position, which is exactly what §5.4's
    projection depends on.

    The exception D.4 names is an option expiring on the same date as its
    underlying future, where the two collapse and the outcome is effectively
    cash. Not reachable in the fixture — every MES option there has a
    September underlying with June-to-August expiries — but cheap to encode
    while the reasoning is written down.
    """
    from app.models.enums import AssetClass

    if parsed.asset_class == AssetClass.FOP:
        return SETTLEMENT_INTO_FUTURE
    if parsed.asset_class == AssetClass.OPT:
        root = (parsed.underlying_root or parsed.root_symbol or "").strip().upper()
        return SETTLEMENT_CASH if root in CASH_SETTLED_INDEX_ROOTS else SETTLEMENT_PHYSICAL
    return None
