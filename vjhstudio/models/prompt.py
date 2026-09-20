from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Prompt(TimestampMixin, Base):
    __tablename__ = "prompts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), default="image", nullable=False)
    form_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    composed_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    final_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    polish_json: Mapped[dict | None] = mapped_column(JSON)
    final_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_prompts_project", "project_id"), Index("ix_prompts_fav", "is_favourite"))
