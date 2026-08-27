from sqlalchemy.orm import Session

from app.contracts.reference import (
    EQUITY_OPTION_EXERCISE_STYLE,
    FUTURES_OPTION_TICK_SPECS,
    FUTURES_SPECS,
    resolve_fop_exercise_style,
    resolve_settlement,
)
from app.models.enums import AssetClass, ExerciseStyle
from app.models.instrument import Instrument
from app.parsers.symbols import ParsedSymbol


def get_or_create_instrument(
    db: Session, parsed: ParsedSymbol, multiplier: float, warnings: list[str]
) -> Instrument:
    existing = (
        db.query(Instrument)
        .filter(Instrument.broker_symbol == parsed.broker_symbol, Instrument.asset_class == parsed.asset_class)
        .one_or_none()
    )
    if existing:
        # A Flex import enriches an instrument the `.tlg` path created with
        # only what it could parse from a symbol string (§3.9).
        if parsed.conid and not existing.conid:
            existing.conid = parsed.conid
        if parsed.underlying_symbol and not existing.underlying_symbol:
            existing.underlying_symbol = parsed.underlying_symbol
        if parsed.underlying_conid and not existing.underlying_conid:
            existing.underlying_conid = parsed.underlying_conid
        if existing.settlement is None:
            existing.settlement = resolve_settlement(parsed)
        if parsed.expiry and not existing.expiry:
            existing.expiry = parsed.expiry
        if parsed.strike is not None and existing.strike is None:
            existing.strike = parsed.strike
        return existing

    exercise_style = ExerciseStyle.UNKNOWN
    tick_size = tick_value = tick_size_high = tick_value_high = tick_threshold = None

    if parsed.asset_class == AssetClass.OPT:
        exercise_style = EQUITY_OPTION_EXERCISE_STYLE
    elif parsed.asset_class == AssetClass.FOP:
        style, matched = resolve_fop_exercise_style(parsed.broker_local_symbol, parsed.root_symbol or "")
        exercise_style = style
        if not matched:
            warnings.append(
                f"Exercise style unresolved for FOP {parsed.broker_symbol!r} (root {parsed.root_symbol!r}); "
                "defaulted to UNKNOWN — verify against CME before it drives a decision (§5.4)"
            )
        tick_spec = FUTURES_OPTION_TICK_SPECS.get(parsed.root_symbol or "")
        if tick_spec:
            tick_size, tick_value = tick_spec.tick_size_low, tick_spec.tick_value_low
            tick_size_high, tick_value_high = tick_spec.tick_size_high, tick_spec.tick_value_high
            tick_threshold = tick_spec.threshold
    elif parsed.asset_class == AssetClass.FUT:
        fut_spec = FUTURES_SPECS.get(parsed.root_symbol or "")
        if fut_spec:
            tick_size, tick_value = fut_spec.tick_size, fut_spec.tick_value

    instrument = Instrument(
        conid=parsed.conid,
        asset_class=parsed.asset_class,
        root_symbol=parsed.root_symbol or parsed.broker_symbol,
        broker_symbol=parsed.broker_symbol,
        broker_local_symbol=parsed.broker_local_symbol,
        description="",
        multiplier=multiplier,
        tick_size=tick_size,
        tick_value=tick_value,
        tick_size_high=tick_size_high,
        tick_value_high=tick_value_high,
        tick_threshold=tick_threshold,
        expiry=parsed.expiry,
        strike=parsed.strike,
        right=parsed.right,
        exercise_style=exercise_style,
        underlying_root=parsed.underlying_root,
        underlying_symbol=parsed.underlying_symbol,
        underlying_conid=parsed.underlying_conid,
        settlement=resolve_settlement(parsed),
        parse_status=parsed.parse_status,
    )
    db.add(instrument)
    db.flush()
    return instrument
