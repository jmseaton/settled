"""§11.3 — corporate actions, and the split adjustment they drive.

**Only splits are applied.** A split has an unambiguous arithmetic
consequence — quantities multiply, prices divide, proceeds are unchanged —
and getting it wrong corrupts every historical figure for that symbol.
Spin-offs, mergers and rights issues have no single correct treatment for a
performance tracker, so they are recorded, surfaced, and left alone. Half a
spin-off applied is worse than none: it looks like a P&L error weeks later
and there is nothing pointing at the cause.

**Raw rows are never mutated.** §3.2 rests on executions being exactly what
the broker sent, so the adjustment lives in `executions.split_factor` and
is recomputed from this table on every import. That makes it reversible
(delete the action, re-import, the factor returns to 1.0) and auditable
against the action that produced it.
"""

import datetime as dt
import re

from sqlalchemy.orm import Session

from app.importer.instruments import get_or_create_instrument
from app.models.execution import Execution
from app.models.option_event import CorporateAction

#: IB describes a split in the free-text description, e.g.
#: "NVDA(US67066G1040) SPLIT 10 FOR 1 (NVDA, NVIDIA CORP, 67066G104)".
_SPLIT_RE = re.compile(r"\bSPLIT\s+([\d.]+)\s+FOR\s+([\d.]+)\b", re.IGNORECASE)

#: IB's `type` codes that mean "split". `FS` is a forward split, `RS` a
#: reverse split; both are handled by the same ratio arithmetic, since a
#: reverse split simply yields a ratio below 1.
SPLIT_TYPES = frozenset({"FS", "RS", "SPLIT", "FORWARDSPLIT", "REVERSESPLIT"})


def parse_split_ratio(description: str) -> float | None:
    """`SPLIT 10 FOR 1` -> 10.0; `SPLIT 1 FOR 8` -> 0.125.

    Returns None rather than a guess when the text does not match. An
    unparsed split is reported and left unapplied, which shows up as a
    reconciliation warning — far better than a silently wrong ratio, which
    shows up as nothing at all.
    """
    match = _SPLIT_RE.search(description or "")
    if not match:
        return None
    try:
        new_shares, old_shares = float(match.group(1)), float(match.group(2))
    except ValueError:
        return None
    if old_shares == 0:
        return None
    return new_shares / old_shares


def replace_corporate_actions(db: Session, parsed, record, account_id: str, warnings: list[str]) -> int:
    if "CorporateActions" not in parsed.sections_seen:
        return 0

    existing = {
        (c.instrument_id, c.occurred_on, c.broker_transaction_id): c
        for c in db.query(CorporateAction)
        .filter(CorporateAction.account_id == account_id, CorporateAction.source == "FLEX")
        .all()
    }

    written = 0
    for action in parsed.corporate_actions:
        instrument = (
            get_or_create_instrument(db, action.symbol, 1.0, warnings) if action.symbol else None
        )
        instrument_id = instrument.id if instrument else None
        key = (instrument_id, action.occurred_on, action.broker_transaction_id)

        row = existing.get(key)
        if row is None:
            row = CorporateAction(
                account_id=account_id,
                instrument_id=instrument_id,
                occurred_on=action.occurred_on,
                broker_transaction_id=action.broker_transaction_id,
                source="FLEX",
            )
            db.add(row)
            written += 1

        row.import_file_id = record.id
        row.action_type = action.action_type
        row.description = action.description
        row.quantity = action.quantity
        row.proceeds = action.proceeds
        row.value = action.value

        ratio = parse_split_ratio(action.description)
        is_split = action.action_type.upper() in SPLIT_TYPES or ratio is not None
        row.split_ratio = ratio

        if is_split and ratio is None:
            warnings.append(
                f"Corporate action on {action.occurred_on}: looks like a split but no ratio "
                f"could be read from {action.description!r}. Historical quantities are "
                "left unadjusted — enter the ratio manually (§11.3)."
            )
        elif not is_split:
            warnings.append(
                f"Corporate action {action.action_type!r} on {action.occurred_on} is recorded "
                "but not applied: only splits have an unambiguous adjustment (§11.3)."
            )

    db.flush()
    return written


def apply_split_factors(db: Session, account_id: str) -> int:
    """Recompute `executions.split_factor` from the corporate-action table.

    A split applies to every execution in that instrument dated **before**
    the action, and splits compound: two 2-for-1s on the same symbol leave
    an execution older than both with a factor of 4. Recomputed from
    scratch each time rather than multiplied in place, so a re-import
    cannot double-apply one.
    """
    executions = db.query(Execution).filter(Execution.account_id == account_id).all()
    for execution in executions:
        execution.split_factor = 1

    splits = (
        db.query(CorporateAction)
        .filter(
            CorporateAction.account_id == account_id,
            CorporateAction.split_ratio.isnot(None),
            CorporateAction.instrument_id.isnot(None),
        )
        .order_by(CorporateAction.occurred_on)
        .all()
    )
    if not splits:
        db.flush()
        return 0

    by_instrument: dict[int, list[tuple[dt.date, float]]] = {}
    for split in splits:
        by_instrument.setdefault(split.instrument_id, []).append(
            (split.occurred_on, float(split.split_ratio))
        )
        split.applied = True

    adjusted = 0
    for execution in executions:
        applicable = by_instrument.get(execution.instrument_id)
        if not applicable:
            continue
        factor = 1.0
        for occurred_on, ratio in applicable:
            if execution.trading_day < occurred_on:
                factor *= ratio
        if factor != 1.0:
            execution.split_factor = factor
            adjusted += 1

    db.flush()
    return adjusted
