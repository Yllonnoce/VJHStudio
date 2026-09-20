from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CatalogModel(TimestampMixin, Base):
    __tablename__ = "catalog_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    air: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # image | video | text
    creator: Mapped[str | None] = mapped_column(String(80))
    architecture: Mapped[str | None] = mapped_column(String(80))
    capabilities_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    default_width: Mapped[int | None] = mapped_column(Integer)
    default_height: Mapped[int | None] = mapped_column(Integer)
    default_steps: Mapped[int | None] = mapped_column(Integer)
    default_cfg: Mapped[float | None] = mapped_column(Float)
    hero_image_url: Mapped[str | None] = mapped_column(String(500))
    price_unit: Mapped[str | None] = mapped_column(
        String(16)
    )  # per_image | per_second | per_1m_tokens
    price_primary: Mapped[float | None] = mapped_column(Float)
    price_in: Mapped[float | None] = mapped_column(Float)
    price_out: Mapped[float | None] = mapped_column(Float)
    price_tiers_json: Mapped[dict | None] = mapped_column(JSON)
    price_source: Mapped[str | None] = mapped_column(String(16))
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    provider_settings_schema: Mapped[list | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(10), default="curated", nullable=False)
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ux_catalog_air", "air", unique=True), Index("ix_catalog_kind", "kind"))
