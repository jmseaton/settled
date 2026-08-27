import datetime as dt
import enum

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

#: §11.1 — the groups the spec names. Free-form: a user can invent others,
#: and these are only what the UI offers first.
DEFAULT_TAG_GROUPS = ("Setup", "Mistake", "Market Regime", "Emotion")

#: §5.4's journaling hook auto-applies this on an assignment, so
#: "how do my assigned positions actually resolve?" is a filter rather than
#: a memory exercise.
ASSIGNED_TAG = "assigned"
SYSTEM_TAG_GROUP = "System"


class Tag(Base):
    """§4.8 — `id, name, group_name, color`."""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_tag_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Applied by the app rather than the user (§5.4). Shown differently and
    #: protected from casual deletion, because deleting it would silently
    #: empty a filter the user believes is complete.
    system: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    def as_dict(self, usage: int | None = None) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "group_name": self.group_name,
            "color": self.color,
            "system": bool(self.system),
            "usage_count": usage,
        }


class TradeTag(Base):
    """§4.8 join table, keyed to survive a rebuild.

    `anchor_key` is the durable identity — a digest of the trade's opening
    broker execution ids (see `app/journal/anchors.py`). `trade_id` is a
    resolved cache, refreshed after every rebuild and null when the trade it
    pointed at no longer exists. Storing only `trade_id` would silently drop
    every tag on the next nightly sync.
    """

    __tablename__ = "trade_tags"
    __table_args__ = (UniqueConstraint("account_id", "anchor_key", "tag_id", name="uq_trade_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    anchor_key: Mapped[str] = mapped_column(String(64), index=True)
    trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("trades.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)

    tag = relationship("Tag")


class DayTag(Base):
    """§4.8 — tags on a calendar day rather than a trade.

    Survives a rebuild untouched: a trading day is not derived from
    anything, so there is nothing to re-anchor.
    """

    __tablename__ = "day_tags"
    __table_args__ = (
        UniqueConstraint("account_id", "trading_day", "tag_id", name="uq_day_tag"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    trading_day: Mapped[dt.date] = mapped_column(Date, index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)

    tag = relationship("Tag")


class NoteScope(str, enum.Enum):
    TRADE = "TRADE"
    DAY = "DAY"
    GROUP = "GROUP"
    GENERAL = "GENERAL"


class Note(Base):
    """§4.9 — `id, scope, scope_ref, body_md, created_at, updated_at`.

    `scope_ref` is a string rather than a foreign key because the four
    scopes point at different things: a trade id, a date, a group id, or
    nothing at all. A note on a trade is re-anchored on rebuild the same way
    a tag is — losing a written note to a routine resync would be the
    worst data loss the app is capable of.
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[NoteScope] = mapped_column(Enum(NoteScope, native_enum=False), index=True)
    scope_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: For TRADE-scoped notes, the durable anchor; `scope_ref` holds the
    #: currently-resolved trade id and is refreshed on every rebuild. Day,
    #: group and general notes need no anchor — a date is not derived from
    #: anything, and a group is re-derived deterministically.
    anchor_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    body_md: Mapped[str] = mapped_column(Text, default="")
    #: §5.4 auto-creates a draft note on an assignment. Drafts are listed
    #: separately so an untouched auto-note is never mistaken for something
    #: the user actually wrote.
    draft: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "scope": self.scope.value,
            "scope_ref": self.scope_ref,
            "body_md": self.body_md,
            "draft": bool(self.draft),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TradeAnnotation(Base):
    """§11.3 — per-trade manual fields that must outlive a rebuild.

    Stop, target and the free-form R-value inputs cannot live on `trades`:
    that table is deleted and re-derived from raw executions on every
    import, so anything stored there is lost on the next sync. Keyed on the
    trade's opening execution identifiers instead, which are the broker's
    and stable — the same reasoning §6.4 uses for group overrides.
    """

    __tablename__ = "trade_annotations"
    __table_args__ = (
        UniqueConstraint("account_id", "anchor_key", name="uq_trade_annotation_anchor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    #: Hash of the trade's opening broker execution identifiers.
    anchor_key: Mapped[str] = mapped_column(String(64), index=True)

    stop_loss: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    profit_target: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
