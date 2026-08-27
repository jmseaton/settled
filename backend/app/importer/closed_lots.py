"""§3.4 — CLOSED_LOT reconciliation, acceptance test 27.

The position snapshot proves the *ending state* is right. This proves the
*path* was right: IB publishes its own FIFO matching with a realized P&L per
lot, so computed per-trade P&L can be checked against the broker's own books.
That validates the entire §5.2 matching algorithm, and it is free.

Semantics, verified against the fixture rather than assumed:

    Lot.cost              = opening notional + opening commission
    Lot.fifoPnlRealized   = proceeds - cost + closing commission   (fully net)

so `fifoPnlRealized` is directly comparable to `Trade.net_pnl`.

**Assignment carve-out (§5.4).** IBKR rolls the option premium into the
underlying's basis (convention b); this app keeps them separate (convention
a) so option performance stays visible. Per-trade comparison therefore
*will* disagree on assignment chains by design. That is an explicit
exception with its own test (test 40), not a widened tolerance — widening
would hide real bugs everywhere else. No assignment has occurred yet, so
the carve-out is recorded and unexercised.
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.closed_lot import ClosedLot
from app.models.enums import LegRole, Origin
from app.models.settings_row import TransferBasisPolicy
from app.models.execution import Execution
from app.models.trade import Trade, TradeExecution

TOLERANCE = 0.01


@dataclass
class ClosedLotIssue:
    severity: str  # "error" | "warning"
    message: str


@dataclass
class ClosedLotCheckResult:
    issues: list[ClosedLotIssue] = field(default_factory=list)
    trades_checked: int = 0
    trades_matched: int = 0
    lots_total: int = 0
    ib_total: float = 0.0
    computed_total: float = 0.0
    ib_total_checked: float = 0.0
    skipped_assignment_chains: int = 0
    skipped_transfers: int = 0
    #: §5.5 case (a) — disagreements explained by missing history rather than
    #: by a matching error. Counted apart from `trades_checked` because they
    #: were not checked: the comparison was not meaningful.
    truncated_trades: int = 0
    transfer_notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "trades_checked": self.trades_checked,
            "trades_matched": self.trades_matched,
            "lots_total": self.lots_total,
            "ib_total_realized": round(self.ib_total, 6),
            "computed_total_checked": round(self.computed_total, 6),
            "ib_total_checked": round(self.ib_total_checked, 6),
            "aggregate_difference": round(self.computed_total - self.ib_total_checked, 6),
            "skipped_assignment_chains": self.skipped_assignment_chains,
            "skipped_transfers": self.skipped_transfers,
            "truncated_trades": self.truncated_trades,
            "transfer_notes": self.transfer_notes,
            "issues": [f"[{i.severity}] {i.message}" for i in self.issues],
        }


def _is_assignment_chain(trade: Trade, executions: list[Execution]) -> bool:
    """The §5.4 carve-out: assignment/exercise flags mean IB's rolled-basis
    figure is not comparable to ours per trade."""
    return any(flag in ("A", "Ex") for e in executions for flag in (e.flags or []))


def ib_realized_by_trade(db: Session, account_id: str) -> dict[int, float]:
    """IB's own `fifoPnlRealized`, aggregated per computed trade.

    Shared by the per-trade check below and by §5.4's chain reconciliation,
    which needs the same mapping over a different set of trades. Extracted
    so the two can never drift into disagreeing about what IB said.
    """
    return _map_lots_to_trades(db, account_id)[0]


def _map_lots_to_trades(db: Session, account_id: str) -> tuple[dict[int, float], int]:
    lots = db.query(ClosedLot).filter(ClosedLot.account_id == account_id).all()
    if not lots:
        return {}, 0

    lots_by_closing_id: dict[str, list[ClosedLot]] = {}
    for lot in lots:
        lots_by_closing_id.setdefault(lot.closing_broker_trade_id, []).append(lot)

    executions: dict[str, Execution] = {}
    for e in db.query(Execution).filter(Execution.account_id == account_id).all():
        for identifier in (e.transaction_id, e.ib_exec_id, e.broker_trade_id):
            if identifier:
                executions.setdefault(identifier, e)

    trade_by_execution: dict[int, int] = {}
    for te in db.query(TradeExecution).join(Trade, TradeExecution.trade_id == Trade.id).filter(
        Trade.account_id == account_id, TradeExecution.leg_role == LegRole.CLOSE
    ).all():
        if te.execution_id is not None:
            trade_by_execution[te.execution_id] = te.trade_id

    by_trade: dict[int, float] = {}
    unmatched = 0
    for closing_id, group in lots_by_closing_id.items():
        execution = executions.get(closing_id)
        trade_id = trade_by_execution.get(execution.id) if execution else None
        if trade_id is None:
            unmatched += len(group)
            continue
        by_trade[trade_id] = by_trade.get(trade_id, 0.0) + sum(
            float(lot.fifo_pnl_realized) for lot in group
        )
    return by_trade, unmatched


def check_closed_lots(
    db: Session, account_id: str, policy: TransferBasisPolicy = TransferBasisPolicy.TRANSFER_MARK
) -> ClosedLotCheckResult:
    result = ClosedLotCheckResult()

    lots = db.query(ClosedLot).filter(ClosedLot.account_id == account_id).all()
    result.lots_total = len(lots)
    if not lots:
        return result

    result.ib_total = sum(float(lot.fifo_pnl_realized) for lot in lots)

    # Each lot names the closing execution it belongs to; map that execution
    # to the trade that owns it, so lots aggregate per trade the same way our
    # own realized P&L does.
    lots_by_closing_id: dict[str, list[ClosedLot]] = {}
    for lot in lots:
        lots_by_closing_id.setdefault(lot.closing_broker_trade_id, []).append(lot)

    # Index by every identifier a lot might reference, so the linkage holds
    # regardless of which key won the §3.2 dedupe ladder for a given row.
    executions: dict[str, Execution] = {}
    for e in db.query(Execution).filter(Execution.account_id == account_id).all():
        for identifier in (e.transaction_id, e.ib_exec_id, e.broker_trade_id):
            if identifier:
                executions.setdefault(identifier, e)
    trades = db.query(Trade).filter(Trade.account_id == account_id).all()

    trade_by_execution: dict[int, Trade] = {}
    trade_executions = (
        db.query(TradeExecution)
        .filter(TradeExecution.trade_id.in_([t.id for t in trades]))
        .all()
    )
    trades_by_id = {t.id: t for t in trades}
    for te in trade_executions:
        if te.execution_id is not None and te.leg_role == LegRole.CLOSE:
            trade_by_execution[te.execution_id] = trades_by_id[te.trade_id]

    ib_by_trade: dict[int, float] = {}
    lots_by_trade: dict[int, list[ClosedLot]] = {}
    unmatched_lots = 0
    for closing_id, lot_group in lots_by_closing_id.items():
        execution = executions.get(closing_id)
        if execution is None:
            unmatched_lots += len(lot_group)
            continue
        trade = trade_by_execution.get(execution.id)
        if trade is None:
            unmatched_lots += len(lot_group)
            continue
        ib_by_trade[trade.id] = ib_by_trade.get(trade.id, 0.0) + sum(
            float(lot.fifo_pnl_realized) for lot in lot_group
        )
        lots_by_trade.setdefault(trade.id, []).extend(lot_group)

    if unmatched_lots:
        result.issues.append(
            ClosedLotIssue(
                "warning",
                f"{unmatched_lots} closed lot(s) could not be tied to a computed trade — "
                "usually a lot whose closing execution falls outside the imported window",
            )
        )

    # The oldest fill held per instrument. A lot IB opened before this is one
    # whose opening execution simply is not in the database, which is what
    # separates a data gap from a matching error below. Per instrument rather
    # than per account: an account-wide earliest date says nothing about a
    # symbol first traded later, and would miss every truncation but the
    # oldest one.
    earliest_execution_by_instrument: dict[int, dt.date] = {}
    for execution in executions.values():
        current = earliest_execution_by_instrument.get(execution.instrument_id)
        executed_on = execution.executed_at.date()
        if current is None or executed_on < current:
            earliest_execution_by_instrument[execution.instrument_id] = executed_on

    executions_by_trade: dict[int, list[Execution]] = {}
    for te in trade_executions:
        if te.execution_id is not None:
            executions_by_trade.setdefault(te.trade_id, []).append(
                next(e for e in executions.values() if e.id == te.execution_id)
            )

    for trade_id, ib_pnl in ib_by_trade.items():
        trade = trades_by_id[trade_id]
        trade_execs = executions_by_trade.get(trade_id, [])

        if _is_assignment_chain(trade, trade_execs):
            result.skipped_assignment_chains += 1
            continue

        # §5.5 carve-out, the same shape of exception as assignment: a
        # transferred position under TRANSFER_MARK is *expected* to disagree
        # with IB, which uses acquisition cost. That divergence is the whole
        # point of the default policy, so record it rather than flag a bug.
        # Under ORIGINAL_COST the two agree and the check does apply.
        if trade.origin == Origin.TRANSFERRED and policy == TransferBasisPolicy.TRANSFER_MARK:
            result.skipped_transfers += 1
            result.transfer_notes.append(
                f"Trade {trade.id} ({trade.instrument.broker_symbol.strip()}): computed "
                f"{float(trade.net_pnl):.2f} under TRANSFER_MARK vs IB's {ib_pnl:.2f} under "
                "acquisition cost. Expected divergence (§5.5), not an error."
            )
            continue

        computed = float(trade.net_pnl)
        difference = computed - ib_pnl

        # §5.5 case (a), and the reason it needs its own branch rather than
        # the tolerance: IB matched this close against a lot it holds and we
        # do not, so our FIFO necessarily picked a different one and the two
        # figures are not describing the same pair of fills. Reporting that
        # as a P&L mismatch accuses the §5.2 matching algorithm of a bug it
        # did not commit, and the suggested fix — check the arithmetic — is
        # the wrong one. It is a data gap, it is repairable, and the message
        # should say which.
        #
        # Checked only when the numbers actually disagree, so a truncated
        # trade that happens to reconcile anyway still counts as a match
        # rather than being quietly excused.
        earliest_held = earliest_execution_by_instrument.get(trade.instrument_id)
        opened_before_our_data = [
            lot
            for lot in lots_by_trade.get(trade_id, [])
            if lot.open_date and earliest_held and lot.open_date < earliest_held
        ]
        # Never a transfer. A transferred position's lots predate our
        # earliest execution too — necessarily, since the opening happened at
        # the delivering broker — but that is §5.5 case (b), where no opening
        # execution will ever exist and importing more history changes
        # nothing. Case (b) must not be able to masquerade as case (a) and
        # collect a remedy that cannot work; under ORIGINAL_COST it is a
        # genuine comparison whose failures are real.
        if (
            abs(difference) > TOLERANCE
            and opened_before_our_data
            and trade.origin is not Origin.TRANSFERRED
        ):
            oldest = min(lot.open_date for lot in opened_before_our_data)
            result.truncated_trades += 1
            result.issues.append(
                ClosedLotIssue(
                    "warning",
                    f"Trade {trade.id} ({trade.instrument.broker_symbol.strip()}): IB matched this "
                    f"close against a lot opened {oldest}, before the earliest {trade.instrument.broker_symbol.strip()} "
                    f"execution held ({earliest_held}), so the two are not matching the same fills "
                    f"(computed {computed:.6f} vs IB {ib_pnl:.6f}). Window truncation, not a "
                    "matching error (§5.5 case a): import an earlier period — widen the Flex "
                    "query's own period if it rejects date overrides (§3.9) — and re-sync",
                )
            )
            continue

        result.trades_checked += 1
        result.computed_total += computed
        result.ib_total_checked += ib_pnl

        if abs(difference) <= TOLERANCE:
            result.trades_matched += 1
        else:
            result.issues.append(
                ClosedLotIssue(
                    "error",
                    f"Trade {trade.id} ({trade.instrument.broker_symbol.strip()}): computed net P&L "
                    f"{computed:.6f} vs IB fifoPnlRealized {ib_pnl:.6f} (difference {difference:.6f})",
                )
            )

    # Aggregate check over the trades actually compared. Worth running even
    # when every trade matched individually, because a compensating pair of
    # errors would pass per-trade and only show up in the total.
    aggregate_difference = result.computed_total - result.ib_total_checked
    if abs(aggregate_difference) > TOLERANCE:
        result.issues.append(
            ClosedLotIssue(
                "error",
                f"Aggregate realized P&L differs from IB across checked trades: computed "
                f"{result.computed_total:.6f} vs {result.ib_total_checked:.6f} "
                f"(difference {aggregate_difference:.6f})",
            )
        )

    return result
