"""§11 — tags and notes, and the §5.4 hook that writes them automatically.

Tag and note *writes* go through here rather than through the route, so the
anchor discipline in `anchors.py` is applied in exactly one place. A tag
written straight against `trade_id` would look fine until the next import
deleted the trade.
"""

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.journal.anchors import anchor_key_for_trade
from app.models.journal import (
    ASSIGNED_TAG,
    SYSTEM_TAG_GROUP,
    DayTag,
    Note,
    NoteScope,
    Tag,
    TradeTag,
)
from app.models.trade import Trade


class JournalError(ValueError):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def get_or_create_tag(
    db: Session,
    account_id: str,
    name: str,
    group_name: str | None = None,
    color: str | None = None,
    system: bool = False,
) -> Tag:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise JournalError("A tag needs a name.")

    tag = (
        db.query(Tag)
        .filter(Tag.account_id == account_id, func.lower(Tag.name) == cleaned.lower())
        .one_or_none()
    )
    if tag is None:
        tag = Tag(
            account_id=account_id,
            name=cleaned,
            group_name=group_name,
            color=color,
            system=1 if system else 0,
            created_at=_now(),
        )
        db.add(tag)
        db.flush()
        return tag

    # An existing tag keeps its group and colour unless the caller supplies
    # new ones: renaming a group out from under a user's other tags is not
    # what "add this tag" means.
    if group_name and not tag.group_name:
        tag.group_name = group_name
    if color and not tag.color:
        tag.color = color
    return tag


def tag_trade(db: Session, account_id: str, trade: Trade, tag: Tag) -> TradeTag:
    anchor = anchor_key_for_trade(db, trade)
    if anchor is None:
        raise JournalError(
            "This trade's opening leg is synthetic (§5.5/§0.3), so it carries no broker "
            "identifier to anchor a tag to — the tag would vanish on the next import. Tag "
            "the day or the strategy group instead."
        )

    existing = (
        db.query(TradeTag)
        .filter(
            TradeTag.account_id == account_id,
            TradeTag.anchor_key == anchor,
            TradeTag.tag_id == tag.id,
        )
        .one_or_none()
    )
    if existing is not None:
        existing.trade_id = trade.id
        return existing

    link = TradeTag(account_id=account_id, anchor_key=anchor, trade_id=trade.id, tag_id=tag.id)
    db.add(link)
    db.flush()
    return link


def untag_trade(db: Session, account_id: str, trade: Trade, tag_id: int) -> bool:
    anchor = anchor_key_for_trade(db, trade)
    if anchor is None:
        return False
    deleted = (
        db.query(TradeTag)
        .filter(
            TradeTag.account_id == account_id,
            TradeTag.anchor_key == anchor,
            TradeTag.tag_id == tag_id,
        )
        .delete(synchronize_session=False)
    )
    db.flush()
    return bool(deleted)


def tag_day(db: Session, account_id: str, trading_day: dt.date, tag: Tag) -> DayTag:
    existing = (
        db.query(DayTag)
        .filter(
            DayTag.account_id == account_id,
            DayTag.trading_day == trading_day,
            DayTag.tag_id == tag.id,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing
    link = DayTag(account_id=account_id, trading_day=trading_day, tag_id=tag.id)
    db.add(link)
    db.flush()
    return link


def write_note(
    db: Session,
    account_id: str,
    scope: NoteScope,
    scope_ref: str | None,
    body_md: str,
    *,
    anchor_key: str | None = None,
    draft: bool = False,
) -> Note:
    note = Note(
        account_id=account_id,
        scope=scope,
        scope_ref=scope_ref,
        anchor_key=anchor_key,
        body_md=body_md,
        draft=1 if draft else 0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(note)
    db.flush()
    return note


def tag_usage(db: Session, account_id: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    for tag_id, count in (
        db.query(TradeTag.tag_id, func.count(TradeTag.id))
        .filter(TradeTag.account_id == account_id)
        .group_by(TradeTag.tag_id)
        .all()
    ):
        counts[tag_id] = counts.get(tag_id, 0) + count
    for tag_id, count in (
        db.query(DayTag.tag_id, func.count(DayTag.id))
        .filter(DayTag.account_id == account_id)
        .group_by(DayTag.tag_id)
        .all()
    ):
        counts[tag_id] = counts.get(tag_id, 0) + count
    return counts


# --- §5.4's journaling hook -------------------------------------------------


def record_assignment_journal(db: Session, account_id: str) -> dict:
    """Auto-tag and auto-draft on every linked assignment chain.

    §5.4: "Assignment is a high-information event worth capturing while it's
    fresh." Both halves matter — the tag makes `origin`/tag filtering answer
    "how do my assigned positions actually resolve?", and the draft note
    gives the reasoning somewhere to go before it is forgotten.

    Idempotent: a resync re-runs this and must not stack a second draft note
    on the same chain.
    """
    from app.models.enums import StrategyType
    from app.models.option_event import OptionEvent
    from app.models.trade_group import TradeGroup

    chains = (
        db.query(TradeGroup)
        .filter(
            TradeGroup.account_id == account_id,
            TradeGroup.strategy_type == StrategyType.ASSIGNMENT_CHAIN,
        )
        .all()
    )
    if not chains:
        return {"tagged": 0, "notes": 0}

    tag = get_or_create_tag(
        db, account_id, ASSIGNED_TAG, group_name=SYSTEM_TAG_GROUP, system=True
    )
    events = {
        e.trade_group_id: e
        for e in db.query(OptionEvent).filter(OptionEvent.trade_group_id.isnot(None)).all()
    }

    tagged = notes = 0
    for chain in chains:
        for leg in chain.legs:
            try:
                tag_trade(db, account_id, leg, tag)
                tagged += 1
            except JournalError:
                # A synthetic opening leg cannot be anchored. The chain-level
                # note still records the episode.
                continue

        # The chain's own legs belong to their spreads, so tag the trades
        # the event actually touched too.
        event = events.get(chain.id)
        if event is not None:
            for execution_id in (event.option_execution_id, event.underlying_execution_id):
                trade = _trade_for_execution(db, execution_id)
                if trade is None:
                    continue
                try:
                    tag_trade(db, account_id, trade, tag)
                    tagged += 1
                except JournalError:
                    continue

        existing = (
            db.query(Note)
            .filter(
                Note.account_id == account_id,
                Note.scope == NoteScope.GROUP,
                Note.scope_ref == str(chain.id),
            )
            .first()
        )
        if existing is not None:
            continue

        event = events.get(chain.id)
        event_type = event.event_type.value.lower() if event else "assignment"
        write_note(
            db,
            account_id,
            NoteScope.GROUP,
            str(chain.id),
            body_md=_draft_body(chain, event_type),
            draft=True,
        )
        notes += 1

    db.flush()
    return {"tagged": tagged, "notes": notes}


def _trade_for_execution(db: Session, execution_id: int | None) -> Trade | None:
    if execution_id is None:
        return None
    from app.models.trade import TradeExecution

    link = (
        db.query(TradeExecution).filter(TradeExecution.execution_id == execution_id).first()
    )
    return db.get(Trade, link.trade_id) if link else None


def _draft_body(chain, event_type: str) -> str:
    """§5.4's journaling hook, carrying §14.6.2's re-read with it.

    §14.6 leaves one item that code cannot close: *"Assignment conventions
    in practice — the design is complete but unexercised. Re-read it the
    first time an assignment actually happens."* No amount of building
    resolves that; it needs a real event.

    What can be arranged is that the re-read happens **at** that moment
    rather than depending on someone remembering a line in a spec appendix
    months later. So the conventions this app chose — and the two places it
    knowingly diverges from IBKR — are written into the draft note the
    event creates, next to the figures they produced. The prompt to check
    them arrives with the thing being checked.
    """
    capital = (
        f"{float(chain.post_assignment_capital):,.2f}"
        if chain.post_assignment_capital is not None
        else "not computed (no underlying execution linked yet)"
    )
    risk = (
        f"{float(chain.max_risk):,.2f}"
        if chain.max_risk is not None
        else "unbounded — the position had no defined risk"
    )

    return (
        f"**Auto-draft — {event_type} on {chain.underlying_root or 'position'}.**\n\n"
        f"Net result across the chain: {float(chain.net_pnl):,.2f}. "
        f"Risk at open (frozen): {risk}. Capital committed after: {capital}.\n\n"
        "- What was the position meant to do?\n"
        "- Was this the expected outcome, or a surprise?\n"
        "- What was done with the resulting position, and why?\n"
        "\n"
        "---\n"
        "\n"
        "**Check the conventions against what actually happened (§14.6.2).** "
        "This is the first real test of a design that was complete but unexercised, "
        "and these are the choices worth confirming now rather than trusting:\n"
        "\n"
        "- **P&L is split, not rolled.** The option closed at 0 with its full premium "
        "realized, and the underlying opened at the strike. IBKR rolls the premium into "
        "the underlying's basis instead, so its per-trade figures will not match — the two "
        "agree only when the whole chain is summed.\n"
        "- **Risk at open is frozen** at the pre-assignment figure, because a spread's "
        "defined risk stopped applying the moment the leg was assigned. Both it and the "
        "capital actually committed are reported, and never averaged.\n"
        "- **Durations are split.** The option leg and the position it became were held "
        "over different windows; no combined figure is emitted.\n"
        "- **Nothing here was inferred.** In-the-money status settles on a price fixing "
        "this app cannot observe, so every figure above came from the broker's own "
        "records.\n"
        "\n"
        "If any of that reads wrong against the real event, §5.4 is the section to revisit.\n"
    )
