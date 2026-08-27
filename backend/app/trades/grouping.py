"""§6 — spread / strategy grouping.

Per-leg statistics on a spread are actively misleading, and this is the
account's dominant structure: `ACME 310813P00172000` shows −61.37 and
`ACME 310813P00180600` shows +103.75; neither means anything alone. The
spread made **+42.37**.

**Detection (§6.1).** `brokerageOrderID` is the primary key — legs
submitted as one combo order share it, and `ibOrderID` does *not* (it is
per leg). Verified against the fixture: 22 two-leg groups, every one a
correctly paired vertical, zero false pairings.

The fallback for legs with no `brokerageOrderID` requires **all** of: same
underlying, *exact* matching timestamp, asset class in {OPT, FOP}, and
opposing directions. Relaxing the timestamp to a date match fabricates a
spread out of the two unrelated MEMC naked puts opened on 2031-07-14 at
10:21:44 and 12:40:43 (test 56), so it is deliberately strict.

`.tlg`-sourced data carries no order identifier of either kind and uses
the timestamp fallback throughout — one more reason Flex is the primary
path.

**Single-leg positions are first-class, not leftovers** (§6.2). A lone
short put is the primary strategy here; it classifies as
`NAKED_SHORT_PUT`, never falling through to `CUSTOM` and never being
force-paired with an unrelated position.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.enums import AssetClass, LegRole, Origin, Right, StrategyType, TradeSide, TradeStatus
from app.models.execution import Execution
from app.models.trade import Trade, TradeExecution
from app.models.group_override import GroupOverride
from app.models.trade_group import TradeGroup

DETECTION_BROKERAGE_ORDER_ID = "BROKERAGE_ORDER_ID"
DETECTION_TIMESTAMP = "TIMESTAMP"
DETECTION_SINGLE = "SINGLE"
DETECTION_MANUAL = "MANUAL"


@dataclass
class _Candidate:
    """A trade plus the opening facts grouping decisions are made on."""

    trade: Trade
    opened_at: dt.datetime
    brokerage_order_id: str | None
    underlying: str | None
    asset_class: AssetClass
    is_long: bool


def rebuild_trade_groups(db: Session, account_id: str) -> list[TradeGroup]:
    _clear_existing(db, account_id)

    trades = (
        db.query(Trade)
        .filter(Trade.account_id == account_id)
        .order_by(Trade.opened_at, Trade.id)
        .all()
    )
    if not trades:
        return []

    candidates = [c for c in (_describe(db, t) for t in trades) if c is not None]

    combos, remaining = _detect_by_brokerage_order_id(candidates)
    timestamp_combos, singles = _detect_by_timestamp(remaining)
    combos += timestamp_combos

    # §6.4 — a manual decision outranks auto-detection and must survive the
    # rebuild that just discarded every trade id it could have referenced.
    combos, singles, overridden = _apply_overrides(db, account_id, combos, singles)

    groups: list[TradeGroup] = []
    for members, source in combos:
        groups.append(
            _build_group(db, account_id, members, source, override=overridden.get(id(members[0])))
        )
    for candidate in singles:
        groups.append(
            _build_group(
                db, account_id, [candidate], DETECTION_SINGLE, override=overridden.get(id(candidate))
            )
        )

    db.flush()
    return groups


def _clear_existing(db: Session, account_id: str) -> None:
    db.query(Trade).filter(Trade.account_id == account_id).update(
        {Trade.trade_group_id: None}, synchronize_session=False
    )
    db.flush()
    db.query(TradeGroup).filter(TradeGroup.account_id == account_id).delete(synchronize_session=False)
    db.flush()


def _describe(db: Session, trade: Trade) -> _Candidate | None:
    opening_legs = [leg for leg in trade.legs if leg.leg_role.value == "OPEN"]
    brokerage_order_id = None
    for leg in opening_legs:
        if leg.execution_id is not None:
            execution = db.get(Execution, leg.execution_id)
            if execution is not None and execution.brokerage_order_id:
                brokerage_order_id = execution.brokerage_order_id
                break

    return _Candidate(
        trade=trade,
        opened_at=trade.opened_at,
        brokerage_order_id=brokerage_order_id,
        underlying=trade.instrument.underlying_root,
        asset_class=trade.instrument.asset_class,
        is_long=trade.side == TradeSide.LONG,
    )


def _detect_by_brokerage_order_id(
    candidates: list[_Candidate],
) -> tuple[list[tuple[list[_Candidate], str]], list[_Candidate]]:
    by_order: dict[str, list[_Candidate]] = defaultdict(list)
    unkeyed: list[_Candidate] = []
    for candidate in candidates:
        if candidate.brokerage_order_id:
            by_order[candidate.brokerage_order_id].append(candidate)
        else:
            unkeyed.append(candidate)

    combos: list[tuple[list[_Candidate], str]] = []
    for members in by_order.values():
        if len(members) > 1:
            combos.append((members, DETECTION_BROKERAGE_ORDER_ID))
        else:
            # A combo key with one leg is just a single-leg order. Fall
            # through so it still gets classified rather than grouped alone
            # under a misleading "combo" provenance.
            unkeyed.extend(members)
    return combos, unkeyed


def _detect_by_timestamp(
    candidates: list[_Candidate],
) -> tuple[list[tuple[list[_Candidate], str]], list[_Candidate]]:
    buckets: dict[tuple, list[_Candidate]] = defaultdict(list)
    singles: list[_Candidate] = []

    for candidate in candidates:
        if candidate.asset_class not in (AssetClass.OPT, AssetClass.FOP):
            singles.append(candidate)
            continue
        if candidate.trade.origin != Origin.TRADED:
            # A synthesized opening has no real fill time to match on.
            singles.append(candidate)
            continue
        buckets[(candidate.underlying, candidate.opened_at, candidate.asset_class)].append(candidate)

    combos: list[tuple[list[_Candidate], str]] = []
    for members in buckets.values():
        # Opposing directions are required: two shorts at the same instant
        # are two positions, not a spread.
        longs = [m for m in members if m.is_long]
        shorts = [m for m in members if not m.is_long]
        if len(members) > 1 and longs and shorts:
            combos.append((members, DETECTION_TIMESTAMP))
        else:
            singles.extend(members)
    return combos, singles


def _member_key(candidate: _Candidate, db: Session) -> str:
    """The stable identity of a trade: the broker's own ids for its opening
    executions. Trade ids cannot be used — none survive a rebuild."""
    identifiers = []
    for leg in candidate.trade.legs:
        if leg.leg_role != LegRole.OPEN or leg.execution_id is None:
            continue
        execution = db.get(Execution, leg.execution_id)
        if execution is not None:
            identifiers.append(execution.transaction_id or execution.broker_trade_id)
    return GroupOverride.build_member_key(identifiers)


def _apply_overrides(db: Session, account_id: str, combos: list, singles: list):
    """Re-shape auto-detected groups to match recorded manual decisions."""
    overrides = db.query(GroupOverride).filter(GroupOverride.account_id == account_id).all()
    if not overrides:
        return combos, singles, {}

    everyone = [c for members, _ in combos for c in members] + list(singles)
    keys = {id(c): _member_key(c, db) for c in everyone}

    claimed: set[int] = set()
    new_combos: list = []
    overridden: dict = {}

    for override in overrides:
        wanted = set(override.member_key.split(","))
        members = [c for c in everyone if keys[id(c)] and keys[id(c)] in wanted and id(c) not in claimed]
        # Only honour an override whose legs are all present; a partial match
        # would silently group something the user did not choose.
        if not members or GroupOverride.build_member_key([keys[id(c)] for c in members]) != override.member_key:
            continue
        claimed.update(id(c) for c in members)
        if override.ungroup:
            for member in members:
                overridden[id(member)] = override
            new_combos.extend(([m], DETECTION_MANUAL) for m in members)
        else:
            overridden[id(members[0])] = override
            new_combos.append((members, DETECTION_MANUAL))

    remaining_combos = [
        (members, source) for members, source in combos if not any(id(m) in claimed for m in members)
    ]
    remaining_singles = [c for c in singles if id(c) not in claimed]
    return remaining_combos + new_combos, remaining_singles, overridden


def classify(members: list[_Candidate]) -> StrategyType:
    """§6.2. Only the shapes reachable from this account's data are
    distinguished; anything else is `CUSTOM` rather than a wrong guess."""
    if len(members) == 1:
        member = members[0]
        instrument = member.trade.instrument
        if instrument.asset_class == AssetClass.STK:
            return StrategyType.LONG_STOCK if member.is_long else StrategyType.SHORT_STOCK
        if instrument.asset_class not in (AssetClass.OPT, AssetClass.FOP):
            return StrategyType.CUSTOM
        if member.is_long:
            return StrategyType.LONG_SINGLE
        if instrument.right == Right.PUT:
            return StrategyType.NAKED_SHORT_PUT
        if instrument.right == Right.CALL:
            return StrategyType.NAKED_SHORT_CALL
        return StrategyType.CUSTOM

    if len(members) == 2:
        a, b = (m.trade.instrument for m in members)
        same_expiry = a.expiry == b.expiry
        same_right = a.right == b.right
        same_strike = (
            a.strike is not None and b.strike is not None and float(a.strike) == float(b.strike)
        )
        opposing = members[0].is_long != members[1].is_long

        if opposing and same_expiry and same_right and not same_strike:
            return StrategyType.VERTICAL_CALL if a.right == Right.CALL else StrategyType.VERTICAL_PUT
        if opposing and same_right and same_strike and not same_expiry:
            return StrategyType.CALENDAR
        if opposing and same_right and not same_expiry and not same_strike:
            return StrategyType.DIAGONAL
        if same_expiry and not same_right and members[0].is_long == members[1].is_long:
            return StrategyType.STRADDLE if same_strike else StrategyType.STRANGLE

    return StrategyType.CUSTOM


def _max_risk(strategy: StrategyType, members: list[_Candidate], net_debit_credit: float) -> float | None:
    """§6.3. Returns None for genuinely unbounded risk — never a finite
    placeholder, which would understate exposure in every average."""
    multiplier = float(members[0].trade.instrument.multiplier)

    if strategy in (StrategyType.VERTICAL_PUT, StrategyType.VERTICAL_CALL):
        strikes = [float(m.trade.instrument.strike) for m in members if m.trade.instrument.strike is not None]
        if len(strikes) != 2:
            return None
        width = abs(strikes[0] - strikes[1]) * multiplier
        if net_debit_credit > 0:  # credit received
            return width - net_debit_credit
        return abs(net_debit_credit)  # debit paid

    if strategy == StrategyType.NAKED_SHORT_PUT:
        strike = members[0].trade.instrument.strike
        if strike is None:
            return None
        # The cash-secured equivalent: the loss if the underlying goes to zero.
        return float(strike) * multiplier - net_debit_credit

    if strategy == StrategyType.NAKED_SHORT_CALL:
        return None  # unbounded

    if strategy == StrategyType.LONG_SINGLE:
        return abs(net_debit_credit)

    return None


def _build_group(
    db: Session,
    account_id: str,
    members: list[_Candidate],
    detection_source: str,
    override: GroupOverride | None = None,
) -> TradeGroup:
    strategy = override.strategy_type if override and not override.ungroup else classify(members)
    trades = [m.trade for m in members]

    # Net credit is positive, net debit negative — the cash effect of
    # opening the position, before any closing flow.
    net_debit_credit = 0.0
    for member in members:
        for leg in member.trade.legs:
            if leg.leg_role.value != "OPEN" or leg.execution_id is None:
                continue
            execution = db.get(Execution, leg.execution_id)
            if execution is not None:
                net_debit_credit += -float(execution.proceeds)

    gross = sum(float(t.gross_pnl or 0) for t in trades)
    commissions = sum(float(t.commissions or 0) for t in trades)
    net = sum(float(t.net_pnl or 0) for t in trades)

    max_risk = _max_risk(strategy, members, net_debit_credit)
    closed_at = None
    if all(t.closed_at is not None for t in trades):
        closed_at = max(t.closed_at for t in trades)

    opened_at = min(t.opened_at for t in trades)
    expiry = members[0].trade.instrument.expiry
    dte = (expiry - opened_at.date()).days if expiry else None

    group = TradeGroup(
        account_id=account_id,
        strategy_type=strategy,
        book=trades[0].book,
        underlying_root=members[0].underlying,
        opened_at=opened_at,
        closed_at=closed_at,
        net_debit_credit=net_debit_credit,
        max_risk=max_risk,
        gross_pnl=gross,
        commissions=commissions,
        net_pnl=net,
        return_on_risk=(net / max_risk) if max_risk else None,
        leg_count=len(members),
        dte_at_open=dte,
        auto_detected=override is None,
        user_confirmed=override is not None,
        detection_source=detection_source,
    )
    db.add(group)
    db.flush()

    for trade in trades:
        trade.trade_group_id = group.id
    db.flush()
    return group
