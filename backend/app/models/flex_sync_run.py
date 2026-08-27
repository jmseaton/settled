import datetime as dt
import enum

from sqlalchemy import Date, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SyncTrigger(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    BACKFILL = "BACKFILL"


class SyncStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    TRANSIENT_FAIL = "TRANSIENT_FAIL"
    CREDENTIAL_FAIL = "CREDENTIAL_FAIL"
    CONFIG_FAIL = "CONFIG_FAIL"
    REQUEST_FAIL = "REQUEST_FAIL"


class FlexSyncRun(Base):
    """§4.1b — every sync attempt, successful or not.

    Backs the staleness banner (§3.8): the failure mode that matters is a
    silently dead sync producing a stale-but-plausible dashboard, so an
    attempt that fails must leave a record behind.
    """

    __tablename__ = "flex_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger: Mapped[SyncTrigger] = mapped_column(Enum(SyncTrigger, native_enum=False))
    window_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    window_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, native_enum=False))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Redacted at the boundary — the token must never reach a log or the UI (§3.10).
    error_message_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rows_ingested: Mapped[int] = mapped_column(Integer, default=0)
    rows_new: Mapped[int] = mapped_column(Integer, default=0)
