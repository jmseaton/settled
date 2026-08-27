"""§6.3 / §8.4 — group-level, risk-normalized statistics.

The rule this module exists to enforce: **do not pool return-on-risk across
strategy types.** An MES 7120 naked put carries roughly 7120 x 5 = $35,600
of cash-secured risk; an ACME 172/180.6 vertical carries $860 minus credit.
Averaging them produces a number that means nothing and looks plausible.

So `aggregate_return_on_risk` returns a *segmented* result and refuses to
emit a blended figure. And `max_risk = None` (a naked short call, whose
risk is genuinely unbounded) propagates as None — never zero, never
silently dropped from a denominator, since either would inflate the
average by removing the position that should have dominated it.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.enums import StrategyType
from app.models.trade_group import TradeGroup


@dataclass
class StrategySegment:
    strategy_type: str
    group_count: int
    net_pnl: float
    #: Groups whose risk is unbounded. Counted, never averaged over.
    unbounded_risk_count: int = 0
    total_max_risk: float | None = None
    return_on_risk: float | None = None
    win_count: int = 0
    loss_count: int = 0

    @property
    def win_rate(self) -> float | None:
        decided = self.win_count + self.loss_count
        return self.win_count / decided if decided else None


@dataclass
class SegmentedRiskStats:
    segments: list[StrategySegment] = field(default_factory=list)
    #: Deliberately absent. Present as an explicit refusal so a caller that
    #: wanted one number gets an explanation rather than a missing key.
    pooled_return_on_risk: None = None
    refusal_reason: str = (
        "Return on risk is not pooled across strategy types. A naked short put and a "
        "vertical carry risk of different orders of magnitude, so a blended figure would "
        "be meaningless and would look plausible. Read the segments instead (§6.3)."
    )

    def as_dict(self) -> dict:
        return {
            "segments": [
                {
                    "strategy_type": s.strategy_type,
                    "group_count": s.group_count,
                    "net_pnl": round(s.net_pnl, 2),
                    "total_max_risk": round(s.total_max_risk, 2) if s.total_max_risk is not None else None,
                    "return_on_risk": round(s.return_on_risk, 6) if s.return_on_risk is not None else None,
                    "unbounded_risk_count": s.unbounded_risk_count,
                    "win_count": s.win_count,
                    "loss_count": s.loss_count,
                    "win_rate": round(s.win_rate, 4) if s.win_rate is not None else None,
                }
                for s in self.segments
            ],
            "pooled_return_on_risk": None,
            "refusal_reason": self.refusal_reason,
        }


def compute_segmented_risk_stats(
    db: Session, account_id: str, book: str | None = None
) -> SegmentedRiskStats:
    query = db.query(TradeGroup).filter(TradeGroup.account_id == account_id)
    if book:
        query = query.filter(TradeGroup.book == book)

    by_strategy: dict[StrategyType, list[TradeGroup]] = {}
    for group in query.all():
        if group.closed_at is None:
            continue
        # §5.4 — an assignment chain spans trades that already belong to
        # their own spreads, so it is a *second view* over them rather than
        # another strategy. Segmenting it alongside would count the option
        # leg's P&L twice: once under NAKED_SHORT_PUT and again under
        # ASSIGNMENT_CHAIN. Chains have their own presentation, with the
        # frozen-risk and post-assignment-capital figures a pooled segment
        # cannot express anyway (§6.3).
        if group.strategy_type == StrategyType.ASSIGNMENT_CHAIN:
            continue
        by_strategy.setdefault(group.strategy_type, []).append(group)

    stats = SegmentedRiskStats()
    for strategy, groups in sorted(by_strategy.items(), key=lambda kv: kv[0].value):
        net = sum(float(g.net_pnl) for g in groups)
        bounded = [g for g in groups if g.max_risk is not None]
        unbounded = [g for g in groups if g.max_risk is None]

        total_risk = sum(float(g.max_risk) for g in bounded) if bounded else None
        # Return on risk is computed only over the groups whose risk is
        # actually known, and the count of the rest travels with it so the
        # figure is never read as covering the whole segment.
        ror = (sum(float(g.net_pnl) for g in bounded) / total_risk) if total_risk else None

        stats.segments.append(
            StrategySegment(
                strategy_type=strategy.value,
                group_count=len(groups),
                net_pnl=net,
                unbounded_risk_count=len(unbounded),
                total_max_risk=total_risk,
                return_on_risk=ror,
                win_count=sum(1 for g in groups if float(g.net_pnl) > 0),
                loss_count=sum(1 for g in groups if float(g.net_pnl) < 0),
            )
        )
    return stats
