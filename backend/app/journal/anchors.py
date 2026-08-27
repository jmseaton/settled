"""Keying journal data to something that survives a rebuild (§11).

**The problem.** `trades` is deleted and re-derived from raw executions on
every import (§13's "full rebuild… eliminates an entire category of
incremental-update bugs"). No trade id survives. So a tag, a note or a stop
stored against `trade_id` would silently detach on the next nightly sync —
and a written note lost to a routine resync is the worst data loss this app
is capable of.

**The fix**, and it is the same one §6.4 uses for group overrides: key on
the broker's own execution identifiers, which are stable across every
rebuild, and resolve back to whatever trade now owns them.

A trade whose opening leg is synthetic (§5.5, §0.3) has no broker
identifier to anchor on. Those cannot carry journal data, and the API says
so rather than accepting a tag that will vanish.
"""

import hashlib

from sqlalchemy.orm import Session

from app.models.enums import LegRole
from app.models.execution import Execution
from app.models.trade import Trade, TradeExecution


def anchor_key_from_identifiers(identifiers: list[str]) -> str:
    """Order-independent digest of a trade's opening execution ids."""
    joined = "|".join(sorted(identifiers))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def opening_identifiers(db: Session, trade: Trade) -> list[str]:
    identifiers: list[str] = []
    for leg in trade.legs:
        if leg.leg_role != LegRole.OPEN or leg.execution_id is None:
            continue
        execution = db.get(Execution, leg.execution_id)
        if execution is not None:
            identifiers.append(execution.transaction_id or execution.broker_trade_id)
    return identifiers


def anchor_key_for_trade(db: Session, trade: Trade) -> str | None:
    identifiers = opening_identifiers(db, trade)
    return anchor_key_from_identifiers(identifiers) if identifiers else None


def build_anchor_index(db: Session, account_id: str) -> dict[str, int]:
    """`anchor_key -> trade_id` for every anchorable trade in the account.

    Built in two queries rather than per-trade lookups: a rebuild touches
    every trade, and the naive version turns one import into thousands of
    round trips.
    """
    trades = db.query(Trade).filter(Trade.account_id == account_id).all()
    if not trades:
        return {}

    trade_ids = [t.id for t in trades]
    legs = (
        db.query(TradeExecution.trade_id, Execution.transaction_id, Execution.broker_trade_id)
        .join(Execution, TradeExecution.execution_id == Execution.id)
        .filter(
            TradeExecution.trade_id.in_(trade_ids),
            TradeExecution.leg_role == LegRole.OPEN,
        )
        .all()
    )

    by_trade: dict[int, list[str]] = {}
    for trade_id, transaction_id, broker_trade_id in legs:
        by_trade.setdefault(trade_id, []).append(transaction_id or broker_trade_id)

    return {
        anchor_key_from_identifiers(identifiers): trade_id
        for trade_id, identifiers in by_trade.items()
        if identifiers
    }
