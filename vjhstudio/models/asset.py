from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    mime: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    tags: Mapped[str] = mapped_column(String(500), default=",", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    media_uuid: Mapped[str | None] = mapped_column(String(64))
    media_url: Mapped[str | None] = mapped_column(String(500))
    media_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        Index("ux_assets_sha256", "sha256", unique=True),
        Index("ux_assets_filename", "filename", unique=True),
        Index("ix_assets_kind", "kind"),
    )
