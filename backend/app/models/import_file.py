import datetime as dt

from sqlalchemy import JSON, Date, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import ImportSource, ImportStatus, SourceFormat


class ImportFile(Base):
    __tablename__ = "import_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[ImportSource] = mapped_column(Enum(ImportSource, native_enum=False))
    source_format: Mapped[SourceFormat] = mapped_column(Enum(SourceFormat, native_enum=False))
    filename: Mapped[str] = mapped_column(String(256))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    retrieved_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    account_id: Mapped[str] = mapped_column(String(32))
    period_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ImportStatus] = mapped_column(Enum(ImportStatus, native_enum=False))
    # §3.2 — where the original payload was archived, so a full rebuild is
    # possible. None means the store was unwritable at import time.
    stored_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
