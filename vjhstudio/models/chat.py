from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, utcnow


class Chat(TimestampMixin, Base):
    """One saved conversation on the Chat page."""

    __tablename__ = "chats"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # the model last used in it; each reply records its own
    model_air: Mapped[str] = mapped_column(String(120), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="chat", order_by="ChatMessage.id", cascade="all, delete-orphan"
    )
    __table_args__ = (Index("ix_chats_updated", "updated_at"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # pictures attached to a user message (asset ids; an asset may be deleted later)
    asset_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # the rest describe a reply
    model_air: Mapped[str | None] = mapped_column(String(120))
    cost: Mapped[float | None] = mapped_column(Float)  # None: not reported (failed, stopped)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(String(20))  # RunWare's, or "stopped"
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    chat: Mapped[Chat] = relationship(back_populates="messages")
    __table_args__ = (Index("ix_chat_messages_chat", "chat_id"),)
