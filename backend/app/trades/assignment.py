"""§5.4 — assignment chains.

An assignment is not one event with one P&L. It is an episode: a spread was
opened, one leg got assigned, that produced a position in the underlying,
and eventually the underlying was disposed of. The only figure that means
anything is the result of the whole episode, and §5.4 models it by
extending §4.6 rather than inventing a new concept — a group with
`strategy_type = ASSIGNMENT_CHAIN` and `parent_group_id` pointing back at
the spread that produced it.

**Two things break the moment assignment happens**, and both are handled
here rather than left to whoever reads the number:

  * `max_risk` becomes wrong. A $2,000-wide put spread's defined risk
    converts into a stock position tying up far more capital. The at-open
    figure is frozen and a second `post_assignment_capital` is computed, and
    return-on-risk is reported against both, labelled.
  * Duration stops being one number. The option leg and the underlying
    holding are separate windows; a combined figure describes neither.

**The P&L convention is (a), separate and grouped** (§5.4). The option
closes at 0 with realized P&L equal to the full premium, and the underlying
opens at the strike. IB uses convention (b) and rolls the premium into the
stock's basis, which makes the premium invisible to every option statistic
in §8 — the opposite of what this app is for. The two agree at the *chain*
level, which is exactly what test 40 asserts.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.importer.closed_lots import ib_realized_by_trade
from app.models.enums import AssetClass, StrategyType
from app.models.execution import Execution
from app.models.instrument import Instrument
from app.models.option_event import OptionEvent, OptionEventType
from app.models.trade import Trade, TradeExecution
from app.models.trade_group import TradeGroup

#: Same tolerance as the CLOSED_LOT check (§3.4), for the same reason: a
#: widened tolerance hides real bugs everywhere else.
TOLERANCE = 0.01


@dataclass
class ChainReconciliation:
    """§5.4's carve-out from acceptance test 27, asserted explicitly."""

    chains_checked: int = 0
    chains_matched: int = 0
    computed_total: float = 0.0
    ib_total: float = 0.0
    issues: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict:
        return {
            "chains_checked": self.chains_checked,
            "chains_matched": self.chains_matched,
            "computed_total": round(self.computed_total, 6),
            "ib_total": round(self.ib_total, 6),
            "difference": round(self.computed_total - self.ib_total, 6),
            "passed": self.passed,
            "issues": self.issues,
            "method": (
                "Per-trade comparison against fifoPnlRealized is expected to disagree on an "
                "assignment chain: IBKR rolls the option premium into the underlying's basis "
                "(convention b) while this app keeps them separate (convention a). The two "
                "agree at the chain level, which is what is checked here (§5.4)."
            ),
        }


def rebuild_assignment_chains(db: Session, account_id: str) -> dict:
    """Build one ASSIGNMENT_CHAIN group per linked assignment or exercise.

    Runs after `rebuild_trade_groups`, so the spreads it links back to
    already exist. Chains are fully derived — deleted and rebuilt like every
    other group — because the events they are built from are themselves
    restated on each import.
    """
    _delete_existing_chains(db, account_id)

    events = (
        db.query(OptionEvent)
        .filter(
            OptionEvent.account_id == account_id,
            OptionEvent.event_type.in_(
                [OptionEventType.ASSIGNMENT, OptionEventType.EXERCISE]
            ),
            OptionEvent.underlying_execution_id.isnot(None),
        )
        .order_by(OptionEvent.occurred_on)
        .all()
    )

    built = 0
    for event in events:
        chain = _build_chain(db, account_id, event)
        if chain is not None:
            event.trade_group_id = chain.id
            built += 1

    db.flush()
    reconciliation = reconcile_chains(db, account_id)
    return {
        "chains_built": built,
        "events_considered": len(events),
        "reconciliation": reconciliation.as_dict(),
    }


def _delete_existing_chains(db: Session, account_id: str) -> None:
    chains = (
        db.query(TradeGroup)
        .filter(
            TradeGroup.account_id == account_id,
            TradeGroup.strategy_type == StrategyType.ASSIGNMENT_CHAIN,
        )
        .all()
    )
    if not chains:
        return
    chain_ids = [c.id for c in chains]
    db.query(OptionEvent).filter(OptionEvent.trade_group_id.in_(chain_ids)).update(
        {OptionEvent.trade_group_id: None}, synchronize_session=False
    )
    # The chain does not own its legs — they belong to their own spreads —
    # so nothing is unparented here; the chain is a second, overlapping
    # view of the same trades.
    for chain in chains:
        db.delete(chain)
    db.flush()


def _trade_for_execution(db: Session, execution_id: int) -> Trade | None:
    link = (
        db.query(TradeExecution)
        .filter(TradeExecution.execution_id == execution_id)
        .first()
    )
    return db.get(Trade, link.trade_id) if link else None


def _build_chain(db: Session, account_id: str, event: OptionEvent) -> TradeGroup | None:
    option_trade = (
        _trade_for_execution(db, event.option_execution_id)
        if event.option_execution_id
        else None
    )
    underlying_trade = _trade_for_execution(db, event.underlying_execution_id)
    if underlying_trade is None:
        return None

    parent = option_trade.group if option_trade is not None else None
    option_instrument = db.get(Instrument, event.instrument_id)

    option_pnl = float(option_trade.net_pnl) if option_trade is not None else 0.0
    underlying_pnl = float(underlying_trade.net_pnl or 0)
    option_commissions = float(option_trade.commissions) if option_trade is not None else 0.0
    option_gross = float(option_trade.gross_pnl) if option_trade is not None else 0.0

    opened_at = option_trade.opened_at if option_trade is not None else underlying_trade.opened_at
    closed_at = underlying_trade.closed_at

    chain = TradeGroup(
        account_id=account_id,
        strategy_type=StrategyType.ASSIGNMENT_CHAIN,
        book=(option_trade.book if option_trade is not None else underlying_trade.book),
        underlying_root=(
            option_instrument.underlying_root if option_instrument else None
        )
        or underlying_trade.instrument.root_symbol,
        opened_at=opened_at,
        closed_at=closed_at,
        net_debit_credit=0,
        gross_pnl=option_gross + float(underlying_trade.gross_pnl or 0),
        commissions=option_commissions + float(underlying_trade.commissions or 0),
        net_pnl=option_pnl + underlying_pnl,
        leg_count=(1 if option_trade is not None else 0) + 1,
        assigned=True,
        parent_group_id=parent.id if parent is not None else None,
        auto_detected=True,
        detection_source=f"OPTION_EAE:{event.event_type.value}",
    )

    # §5.4 — freeze the pre-assignment risk figure. The spread's defined
    # risk stopped applying the moment the leg was assigned, so this is
    # deliberately the parent's number and is not recomputed from the
    # chain's own legs.
    if parent is not None:
        chain.max_risk = parent.max_risk
        parent.assigned = True

    chain.post_assignment_capital = _post_assignment_capital(db, event, underlying_trade)

    if chain.max_risk:
        chain.return_on_risk = chain.net_pnl / abs(float(chain.max_risk))
    if chain.post_assignment_capital:
        chain.return_on_post_assignment_capital = chain.net_pnl / abs(
            float(chain.post_assignment_capital)
        )

    # §5.4 test 48 — two durations, no combined figure.
    if option_trade is not None and option_trade.duration_seconds is not None:
        chain.option_duration_seconds = float(option_trade.duration_seconds)
    if underlying_trade.duration_seconds is not None:
        chain.underlying_duration_seconds = float(underlying_trade.duration_seconds)

    db.add(chain)
    db.flush()
    return chain


def _post_assignment_capital(
    db: Session, event: OptionEvent, underlying_trade: Trade
) -> float | None:
    """Capital the resulting position actually tied up.

    Valued at the strike, which is the price the assignment transacted at —
    not at a later mark, which would make the figure move with the market
    and stop describing the commitment that was made.
    """
    execution = db.get(Execution, event.underlying_execution_id)
    if execution is None:
        return None
    quantity = abs(float(execution.quantity))
    multiplier = float(execution.multiplier or 1)
    price = float(execution.price or 0)
    if not quantity or not price:
        return None

    # A futures assignment commits margin, not notional, and this app has no
    # margin figure. Report the contract's notional and let §6.3's
    # unbounded-risk labelling carry the caveat rather than inventing a
    # margin number.
    del underlying_trade
    return quantity * multiplier * price


def reconcile_chains(db: Session, account_id: str) -> ChainReconciliation:
    """§16.2 test 40 — chain P&L under (a) equals IB's under (b).

    IB reports the whole episode's realized figure on the underlying's
    disposal, with the premium already rolled into its basis. This app
    reports the option's premium separately and the underlying's move
    separately. The sum must agree, and if it does not, one of the two legs
    is wrong in a way per-trade reconciliation cannot see.
    """
    result = ChainReconciliation()
    chains = (
        db.query(TradeGroup)
        .filter(
            TradeGroup.account_id == account_id,
            TradeGroup.strategy_type == StrategyType.ASSIGNMENT_CHAIN,
        )
        .all()
    )
    if not chains:
        return result

    events_by_chain = {
        e.trade_group_id: e
        for e in db.query(OptionEvent)
        .filter(OptionEvent.trade_group_id.isnot(None))
        .all()
    }
    # IB's side of the comparison must come from IB. Using this app's own
    # figure for the underlying and adding the option's premium to it would
    # compare convention (a) against itself plus a constant, and pass
    # whatever the basis arithmetic did.
    ib_by_trade = ib_realized_by_trade(db, account_id)

    for chain in chains:
        event = events_by_chain.get(chain.id)
        if event is None:
            continue
        underlying_trade = (
            _trade_for_execution(db, event.underlying_execution_id)
            if event.underlying_execution_id
            else None
        )
        # Only a closed chain is comparable: IB books its realized figure on
        # disposal, so an open underlying has nothing to compare against.
        if underlying_trade is None or underlying_trade.closed_at is None:
            continue
        if underlying_trade.id not in ib_by_trade:
            # No CLOSED_LOT row covers the disposal, so there is no IB
            # figure to check against. Skipped rather than compared against
            # a substitute, which is what the whole check exists to avoid.
            continue

        computed = float(chain.net_pnl)
        # Convention (b): the premium is inside the underlying's basis, so
        # IB's disposal figure already contains it. The option's own EAE
        # realized is 0 under (b) and is added only for the rare case where
        # IB does book something there.
        ib_figure = float(event.realized_pnl) + ib_by_trade[underlying_trade.id]

        result.chains_checked += 1
        result.computed_total += computed
        result.ib_total += ib_figure

        if abs(computed - ib_figure) <= TOLERANCE:
            result.chains_matched += 1
        else:
            result.issues.append(
                f"Chain {chain.id} ({chain.underlying_root}): computed {computed:.6f} under "
                f"convention (a) vs IB {ib_figure:.6f} under (b), difference "
                f"{computed - ib_figure:.6f}"
            )

    return result


def projected_underlying_quantity(option_quantity: float, instrument: Instrument) -> float:
    """§5.4 test 44 — how much underlying one option turns into.

    A futures option exercises into **one futures contract per option**; its
    multiplier prices the premium and is not a share count. An equity option
    delivers `multiplier` shares. Getting this backwards would project a
    500-contract futures position from 100 MES options.
    """
    if instrument.asset_class == AssetClass.FOP:
        return abs(option_quantity)
    return abs(option_quantity) * float(instrument.multiplier or 1)
