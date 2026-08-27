"""§0.3 — the performance epoch.

The account opened on one date; the strategy being measured started on
another. Measuring from account funding mixes early experimentation into
every statistic with no way to un-mix it later, so both the start date and
the account value on that date are user-configurable and everything in §7
and §8 is anchored on them.

**Without the anchor, percentage returns are meaningless.** That is not a
stylistic point: a TWR computed against a starting value of zero is not a
small number, it is undefined, and an app that prints one anyway is exactly
the failure this design exists to prevent. When no epoch is configured the
resolver says so and the return figures are withheld rather than
approximated.
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cash_transaction import CashTransaction
from app.models.enums import Origin, TradeStatus
from app.models.execution import Execution
from app.models.settings_row import get_settings_row
from app.models.trade import Trade


@dataclass(frozen=True)
class Epoch:
    start_date: dt.date | None
    start_value: float
    #: True when the user set both a date and a value. Only then do
    #: percentage returns mean anything.
    configured: bool
    #: IBKR's own `ChangeInNAV.startingValue` for the same date, when a
    #: date-overridden Flex request has supplied one (§0.3). Displayed
    #: beside the stored value; never silently substituted for it.
    ibkr_start_value: float | None = None
    note: str = ""

    @property
    def divergence(self) -> float | None:
        """Stored value minus IBKR's. A wrong starting value produces
        returns that look plausible and are wrong, so the gap is surfaced
        rather than resolved in either direction."""
        if self.ibkr_start_value is None or not self.configured:
            return None
        return self.start_value - self.ibkr_start_value

    def as_dict(self) -> dict:
        return {
            "tracking_start_date": self.start_date.isoformat() if self.start_date else None,
            "tracking_start_value": self.start_value if self.configured else None,
            "tracking_start_value_ibkr": self.ibkr_start_value,
            "divergence": self.divergence,
            "configured": self.configured,
            "note": self.note,
        }


UNSET_NOTE = (
    "No performance epoch is set. Percentage returns, TWR, XIRR and the benchmark "
    "comparison all need a starting date and a starting account value to mean anything, "
    "so they are withheld rather than computed against an assumed zero. Set them in "
    "Settings — the value can be fetched from IBKR rather than typed (§0.3)."
)

NO_VALUE_NOTE = (
    "A tracking start date is set but no starting account value. Absolute P&L is unaffected; "
    "every percentage measure is withheld until a value is supplied (§0.3)."
)


def resolve_epoch(db: Session, account_id: str) -> Epoch:
    row = get_settings_row(db)
    start_date = row.tracking_start_date
    start_value = float(row.tracking_start_value) if row.tracking_start_value is not None else 0.0
    ibkr = (
        float(row.tracking_start_value_ibkr)
        if row.tracking_start_value_ibkr is not None
        else None
    )

    if start_date is None:
        # Fall back to the first day of activity so the curve still has an
        # x-axis, but say plainly that no anchor exists.
        return Epoch(
            start_date=first_activity_day(db, account_id),
            start_value=0.0,
            configured=False,
            ibkr_start_value=ibkr,
            note=UNSET_NOTE,
        )

    if start_value <= 0:
        return Epoch(
            start_date=start_date,
            start_value=0.0,
            configured=False,
            ibkr_start_value=ibkr,
            note=NO_VALUE_NOTE,
        )

    return Epoch(
        start_date=start_date,
        start_value=start_value,
        configured=True,
        ibkr_start_value=ibkr,
        note="",
    )


def first_activity_day(db: Session, account_id: str) -> dt.date | None:
    """Earliest trading day the account did anything — an execution or a
    cash movement, whichever came first."""
    first_exec = db.scalar(
        select(func.min(Execution.trading_day)).where(Execution.account_id == account_id)
    )
    first_cash = db.scalar(
        select(func.min(CashTransaction.occurred_on)).where(
            CashTransaction.account_id == account_id
        )
    )
    candidates = [d for d in (first_exec, first_cash) if d is not None]
    return min(candidates) if candidates else None


def apply_epoch_to_trades(db: Session, account_id: str, epoch: Epoch) -> dict:
    """Classify every trade against the epoch (§0.3).

    Three outcomes, and only the first two change any number:

      * **Closed before the epoch** — `origin = PRE_EPOCH` and excluded from
        the win rate and from the equity curve. The executions stay: they
        supply the cost basis of positions that were open on day one, and
        the data is not re-fetchable indefinitely.
      * **Straddling the epoch** — opened before, closed after. Included,
        flagged `straddles_epoch`, and excluded from duration statistics: a
        hold time that began before you were measuring is not a hold time
        you can learn from.
      * **Wholly after** — untouched.

    One thing this deliberately does *not* do: rebase a straddling trade's
    opening basis to the epoch-date mark. §0.3 asks for it and it is the
    right treatment, but it needs a market price on the epoch date, and the
    app has no price feed until §12. Reporting the un-rebased figure and
    flagging the trade is honest; inventing a mark would not be, and the
    flag is what stops the number being read as if it had been rebased.
    """
    trades = db.query(Trade).filter(Trade.account_id == account_id).all()
    pre_epoch = straddling = 0

    for trade in trades:
        # Reset first: trades are rebuilt from scratch, but the epoch can
        # also move without an import, and a stale flag is worse than none.
        if trade.origin == Origin.PRE_EPOCH:
            trade.origin = Origin.TRADED
        trade.straddles_epoch = False

        if epoch.start_date is None:
            continue

        opened_before = trade.opened_at is not None and trade.opened_at.date() < epoch.start_date
        if not opened_before:
            continue

        closed_before = trade.closed_at is not None and trade.closed_at.date() < epoch.start_date
        if closed_before:
            trade.origin = Origin.PRE_EPOCH
            trade.excluded_from_win_rate = True
            trade.excluded_from_duration = True
            pre_epoch += 1
        elif trade.status != TradeStatus.OPEN or trade.closed_at is not None:
            trade.straddles_epoch = True
            trade.excluded_from_duration = True
            straddling += 1
        else:
            # Still open: it was open at the epoch, so it is a position
            # inherited by the measured period rather than a decision made
            # inside it.
            trade.straddles_epoch = True
            trade.excluded_from_duration = True
            straddling += 1

    db.flush()
    return {"pre_epoch_trades": pre_epoch, "straddling_trades": straddling}
