"""§9.5 — Expiry Watch, the one deliberately forward-looking view.

Reports mechanical consequences only: what a position *becomes* if it
finishes ITM. Emits no probability, no signal, no suggested action (§1.3,
§9.5 closing note). Renders fully without any price feed — expiry, DTE,
strike, quantity, and the projection are the operationally important
fields and none of them need a price (test 71).
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.contracts.reference import FUTURES_SPECS
from app.importer.reconciliation import compute_open_positions
from app.models.enums import AssetClass, ExerciseStyle, Right
from app.market_calendar import trading_days_between
from app.models.instrument import Instrument
from app.models.price_bar import PriceBar


@dataclass
class ExpiryWatchRow:
    instrument_id: int
    symbol: str
    underlying: str | None
    asset_class: str
    book: str
    quantity: float
    strike: float | None
    right: str | None
    expiry: dt.date | None
    dte_calendar: int | None
    dte_trading: int | None
    exercise_style: str
    projection: str
    projection_notional: float | None
    projection_cash_required: float | None
    reference_price: float | None = None
    reference_price_as_of: dt.date | None = None
    #: `position_mark` (the broker's own valuation of a contract we hold) or
    #: `price_bars:<provider>`. Which one produced a figure is part of the
    #: figure.
    reference_price_source: str | None = None
    #: §9.5 — how far the underlying sits from the strike, and which side.
    #: Positive means out of the money for this position, negative in.
    #: Both are **forecasts**: §5.4 is explicit that ITM is settled on a
    #: 3 p.m. CT fixing this app cannot observe, so a position can look
    #: comfortably out of the money here and still exercise.
    distance_to_strike: float | None = None
    distance_to_strike_pct: float | None = None
    moneyness: str | None = None
    warnings: list[str] = field(default_factory=list)


def _project(instrument: Instrument, quantity: float) -> tuple[str, float | None, float | None]:
    """The 'if assigned' projection table from §9.5."""
    if instrument.strike is None or instrument.right is None:
        return "Unknown — contract details unavailable", None, None

    strike = float(instrument.strike)
    multiplier = float(instrument.multiplier)
    contracts = abs(quantity)
    is_short = quantity < 0
    is_put = instrument.right == Right.PUT

    if instrument.asset_class == AssetClass.FOP:
        spec = FUTURES_SPECS.get(instrument.underlying_root or "")
        point_value = spec.multiplier if spec else multiplier
        notional = strike * point_value * contracts
        if is_short:
            direction = "Long" if is_put else "Short"
        else:
            direction = "Short" if is_put else "Long"
        return (
            f"{direction} {contracts:g} {instrument.underlying_root or ''} future(s) at {strike:g}".strip(),
            notional,
            None,
        )

    shares = contracts * multiplier
    if is_short:
        direction = "Long" if is_put else "Short"
    else:
        direction = "Short" if is_put else "Long"
    cash_required = strike * shares if (is_short and is_put) or (not is_short and not is_put) else None
    notional = strike * shares
    return (
        f"{direction} {shares:g} shares of {instrument.underlying_root or instrument.root_symbol} at {strike:g}",
        notional,
        cash_required,
    )


def _latest_underlying_marks(db: Session, account_id: str) -> tuple[dict[str, float], dt.date | None]:
    """§12 — the newest mark per underlying we actually hold.

    **Only stock and futures positions are indexed.** An option's own mark
    is the option's premium, and an option shares its root symbol with its
    underlying — so indexing every position would file a $1.40 put premium
    under "MEMC" and report the stock as trading at $1.40. Restricting to
    instruments that can *be* an underlying is what keeps that from
    happening silently.

    Only the newest snapshot is used: an older mark yields a
    distance-to-strike that was true last week and is presented as though
    it were true now, which is worse than showing nothing.
    """
    from app.models.position_snapshot import BrokerPositionSnapshot

    latest_day = (
        db.query(func.max(BrokerPositionSnapshot.snapshot_on))
        .filter(BrokerPositionSnapshot.account_id == account_id)
        .scalar()
    )
    if latest_day is None:
        return {}, None

    rows = (
        db.query(BrokerPositionSnapshot)
        .filter(
            BrokerPositionSnapshot.account_id == account_id,
            BrokerPositionSnapshot.snapshot_on == latest_day,
            BrokerPositionSnapshot.mark_price.isnot(None),
            BrokerPositionSnapshot.asset_class.in_([AssetClass.STK, AssetClass.FUT]),
        )
        .all()
    )

    marks: dict[str, float] = {}
    for row in rows:
        instrument = row.instrument
        if instrument is None:
            continue
        price = float(row.mark_price)
        for key in (instrument.conid, instrument.broker_symbol, instrument.root_symbol):
            if key:
                marks.setdefault(str(key), price)
    return marks, latest_day


def _underlying_mark(
    db: Session, instrument: Instrument, marks: dict[str, float]
) -> tuple[float | None, dt.date | None, str | None]:
    """The underlying's price, from the position book or the price cache.

    Two sources, in order of authority:

      1. **A mark on a position we hold.** The broker's own valuation of the
         same contract, as of the statement date.
      2. **`price_bars`** (§4.10) — the cache the benchmark already reads
         from. Most option underlyings are not held as positions (you can be
         short a MEMC put without owning MEMC), so without this the feature
         would only work for covered positions.

    Returns the price, the date it was true, and which source it came from,
    because a distance-to-strike whose vintage is unknown is not actionable.
    """
    for key in (
        instrument.underlying_conid,
        instrument.underlying_symbol,
        instrument.underlying_root,
    ):
        if key and str(key) in marks:
            return marks[str(key)], None, "position_mark"

    symbol = (instrument.underlying_root or instrument.underlying_symbol or "").strip().upper()
    if not symbol:
        return None, None, None

    bar = (
        db.query(PriceBar)
        .filter(PriceBar.symbol == symbol)
        .order_by(PriceBar.date.desc())
        .first()
    )
    if bar is None:
        return None, None, None
    return float(bar.close), bar.date, f"price_bars:{bar.source}"


def build_expiry_watch(db: Session, account_id: str, as_of: dt.date) -> list[ExpiryWatchRow]:
    positions = compute_open_positions(db, account_id)
    marks, marks_as_of = _latest_underlying_marks(db, account_id)
    rows: list[ExpiryWatchRow] = []

    for instrument_id, pos in positions.items():
        if abs(pos.quantity) < 1e-9:
            continue
        instrument = db.get(Instrument, instrument_id)
        if instrument.asset_class not in (AssetClass.OPT, AssetClass.FOP):
            continue

        warnings: list[str] = []
        if instrument.exercise_style == ExerciseStyle.UNKNOWN:
            warnings.append("Exercise style UNKNOWN — verify contract specs before relying on this row (§5.4)")
        if pos.quantity < 0:
            warnings.append(
                "Shown per leg: spread grouping (§6) is not built yet, so this projection does not "
                "account for an offsetting long leg. If this short is part of a vertical, the real "
                "exposure is capped at the group's max risk."
            )

        dte_calendar = (instrument.expiry - as_of).days if instrument.expiry else None
        dte_trading = trading_days_between(as_of, instrument.expiry) if instrument.expiry else None

        projection, notional, cash_required = _project(instrument, pos.quantity)

        reference_price, bar_date, price_source = _underlying_mark(db, instrument, marks)
        distance = distance_pct = None
        moneyness = None
        if reference_price is not None and instrument.strike is not None:
            strike = float(instrument.strike)
            # Signed toward safety: positive means the underlying is on the
            # harmless side of the strike for this contract.
            distance = (
                reference_price - strike
                if instrument.right == Right.PUT
                else strike - reference_price
            )
            distance_pct = distance / strike if strike else None
            moneyness = "OTM" if distance > 0 else ("ATM" if distance == 0 else "ITM")
            if moneyness == "ITM":
                warnings.append(
                    "In the money at the last mark. This is a forecast, not an outcome: ITM is "
                    "settled on a price fixing this app cannot observe, and the broker's "
                    "records are the only authority (§5.4)."
                )

        if reference_price is None:
            warnings.append(
                "No price for the underlying, so no distance to strike. Held underlyings are "
                "marked from the broker statement; others need a cached daily close (§4.10)."
            )

        rows.append(
            ExpiryWatchRow(
                reference_price=reference_price,
                reference_price_as_of=bar_date or (marks_as_of if reference_price is not None else None),
                reference_price_source=price_source,
                distance_to_strike=distance,
                distance_to_strike_pct=distance_pct,
                moneyness=moneyness,
                instrument_id=instrument_id,
                symbol=instrument.broker_symbol,
                underlying=instrument.underlying_root,
                asset_class=instrument.asset_class.value,
                book="Options Income",
                quantity=pos.quantity,
                strike=float(instrument.strike) if instrument.strike is not None else None,
                right=instrument.right.value if instrument.right else None,
                expiry=instrument.expiry,
                dte_calendar=dte_calendar,
                dte_trading=dte_trading,
                exercise_style=instrument.exercise_style.value,
                projection=projection,
                projection_notional=notional,
                projection_cash_required=cash_required,
                warnings=warnings,
            )
        )

    rows.sort(key=lambda r: (r.expiry is None, r.expiry or dt.date.max))
    return rows
