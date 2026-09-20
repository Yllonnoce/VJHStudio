from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class UsageEntry(Base):
    __tablename__ = "usage_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    task_type: Mapped[str] = mapped_column(String(30), nullable=False)
    model_air: Mapped[str | None] = mapped_column(String(120))
    cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_usage_project_day", "project_id", "day"),
        Index("ix_usage_day", "day"),
    )
