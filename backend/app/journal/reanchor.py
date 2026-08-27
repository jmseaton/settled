"""Reconnect journal data to trades after a rebuild (§11).

Runs once per import, immediately after `rebuild_trades` has deleted and
re-derived every trade. Everything keyed by `anchor_key` finds its new
trade id here; anything whose trade no longer exists is left detached
rather than deleted, because the executions behind it may simply be outside
the current sync window and will come back.
"""

from sqlalchemy.orm import Session

from app.journal.anchors import build_anchor_index
from app.models.journal import Note, NoteScope, TradeTag


def reanchor_journal(db: Session, account_id: str) -> dict:
    index = build_anchor_index(db, account_id)

    tags = db.query(TradeTag).filter(TradeTag.account_id == account_id).all()
    detached_tags = 0
    for link in tags:
        resolved = index.get(link.anchor_key)
        link.trade_id = resolved
        if resolved is None:
            detached_tags += 1

    notes = (
        db.query(Note)
        .filter(
            Note.account_id == account_id,
            Note.scope == NoteScope.TRADE,
            Note.anchor_key.isnot(None),
        )
        .all()
    )
    detached_notes = 0
    for note in notes:
        resolved = index.get(note.anchor_key)
        note.scope_ref = str(resolved) if resolved is not None else None
        if resolved is None:
            detached_notes += 1

    db.flush()
    return {
        "trade_tags": len(tags),
        "notes": len(notes),
        "detached_tags": detached_tags,
        "detached_notes": detached_notes,
        # Detached is a state, not an error: the trade's executions are
        # probably just outside the current window. Nothing is deleted, so
        # a wider re-import reattaches them.
        "detached_note": (
            "Detached journal entries keep their anchor and reattach when the trade's "
            "executions are next imported."
            if detached_tags or detached_notes
            else None
        ),
    }
