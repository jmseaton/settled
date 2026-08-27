import datetime as dt

from sqlalchemy import DateTime, Enum, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import StrategyType


class GroupOverride(Base):
    """§6.4 — a manual grouping that must survive a rebuild.

    The obvious implementation stores trade ids and breaks on the very next
    import: trades are fully re-derived on every import, so none of their
    ids survive. Executions do — they are upserted and never mutated (§3.2)
    — so an override is keyed on the *broker's own identifiers* for the
    opening executions involved.

    That key is stable across rebuilds, re-imports, and even a teardown and
    reload from stored payloads, because it belongs to the broker rather
    than to this database.
    """

    __tablename__ = "group_overrides"
    __table_args__ = (UniqueConstraint("account_id", "member_key", name="uq_group_override_members"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)

    #: Sorted, comma-joined opening execution identifiers. Sorted so the
    #: same set of legs produces the same key regardless of selection order.
    member_key: Mapped[str] = mapped_column(Text)

    strategy_type: Mapped[StrategyType] = mapped_column(Enum(StrategyType, native_enum=False))
    #: An explicit "these are NOT one strategy" decision, which has to be
    #: recorded separately — otherwise auto-detection would just re-group
    #: them on the next rebuild.
    ungroup: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    @staticmethod
    def build_member_key(identifiers: list[str]) -> str:
        return ",".join(sorted(i for i in identifiers if i))
