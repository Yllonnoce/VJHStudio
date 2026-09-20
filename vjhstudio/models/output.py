from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class Output(Base):
    __tablename__ = "outputs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    filename: Mapped[str] = mapped_column(String(80), nullable=False)
    rel_path: Mapped[str] = mapped_column(String(300), nullable=False)
    sidecar_rel_path: Mapped[str] = mapped_column(String(300), nullable=False)
    model_air: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    params_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    cost: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(String(600))
    file_size: Mapped[int | None] = mapped_column(Integer)
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (Index("ix_outputs_project_created", "project_id", "created_at"),
                      Index("ix_outputs_model", "model_air"),
                      Index("ix_outputs_fav", "is_favourite"),
                      Index("ix_outputs_job", "job_id"),
                      Index("ux_outputs_project_filename", "project_id", "filename", unique=True))
