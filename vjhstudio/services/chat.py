"""The Chat page: saved conversations with RunWare's text models.

A reply is produced by ``run_reply``, which is independent of any HTTP response: it
streams from RunWare, hands each piece to ``emit`` and saves the reply at the end. The
route only relays what it emits, and sets ``cancel`` when the browser goes away."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from runware import StreamOptions
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..config import Paths
from ..models import CatalogModel, Chat, ChatMessage, utcnow
from ..runware import tasks_chat
from ..runware.errors import classify
from . import assets, catalog, costs

TITLE_MAX = 60
NEW_TITLE = "New chat"
LIST_LIMIT = 200
REPLY_TIMEOUT_MS = 300_000
STOPPED = "stopped"
EMPTY_REPLY = (
    "The model ran out of room before it wrote an answer. Try again, or pick another model."
)


# ---- which models -------------------------------------------------------------------
def is_chat_model(m: CatalogModel) -> bool:
    """A text model that holds a conversation. The Text kind also has captioners, age
    detectors and the prompt enhancer: those carry an ``op:`` tag, or cannot start from
    text. A row with no tags at all (added from search) is let through."""
    caps = m.capabilities_json or []
    if m.kind != "text":
        return False
    if not caps:
        return True
    return "io:text-to-text" in caps and not any(str(c).startswith("op:") for c in caps)


def sees_pictures(m: CatalogModel | None) -> bool:
    return m is not None and "io:image-to-text" in (m.capabilities_json or [])


def chat_models(session: Session) -> list[dict]:
    return [
        {"air": m.air, "name": m.name, "label": catalog.label(m), "vision": sees_pictures(m)}
        for m in catalog.list_models(session, "text")
        if is_chat_model(m)
    ]


def default_air(models: list[dict], *wanted: str | None) -> str:
    """The first of ``wanted`` that is on the list, else the list's first model."""
    airs = [m["air"] for m in models]
    for air in wanted:
        if air and air in airs:
            return air
    return airs[0] if airs else ""


# ---- conversations ------------------------------------------------------------------
def title_from(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:TITLE_MAX]
    return NEW_TITLE


def create(session: Session, *, title: str, model_air: str, system_prompt: str = "") -> Chat:
    chat = Chat(title=title or NEW_TITLE, model_air=model_air, system_prompt=system_prompt or "")
    session.add(chat)
    session.flush()
    return chat


def get(session: Session, chat_id: int) -> Chat | None:
    return session.get(Chat, chat_id)


def list_chats(session: Session) -> list[Chat]:
    q = select(Chat).order_by(Chat.updated_at.desc(), Chat.id.desc()).limit(LIST_LIMIT)
    return list(session.execute(q).scalars())


def rename(session: Session, chat_id: int, title: str) -> Chat:
    chat = get(session, chat_id)
    if chat is None:
        raise LookupError(chat_id)
    if (title or "").strip():
        chat.title = title.strip()[:200]
        session.flush()
    return chat


def delete(session: Session, chat_id: int) -> bool:
    chat = get(session, chat_id)
    if chat is None:
        return False
    session.delete(chat)
    session.flush()
    return True


def add_user_message(
    session: Session, chat: Chat, text: str, asset_ids: Iterable[int] = ()
) -> ChatMessage:
    msg = ChatMessage(
        chat_id=chat.id, role="user", content=text.strip(), asset_ids_json=list(asset_ids)
    )
    session.add(msg)
    chat.updated_at = utcnow()
    session.flush()
    session.refresh(chat)
    return msg


def total_cost(session: Session, chat_id: int) -> float:
    q = select(func.sum(ChatMessage.cost)).where(ChatMessage.chat_id == chat_id)
    return float(session.execute(q).scalar() or 0.0)


# ---- what is sent -------------------------------------------------------------------
def history(messages: Iterable[ChatMessage]) -> list[dict]:
    """The conversation as RunWare wants it. A reply that failed or is empty is left
    out, and user messages that end up next to each other are joined: a message whose
    reply failed and was typed again is one turn, and the list still ends on ``user``."""
    out: list[dict] = []
    for m in messages:
        if m.role == "assistant" and (m.error or not (m.content or "").strip()):
            continue
        if m.role == "user" and out and out[-1]["role"] == "user":
            out[-1]["content"] += "\n\n" + m.content
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def picture_ids(messages: Iterable[ChatMessage]) -> list[int]:
    """Every picture attached anywhere in the conversation, once, in order: RunWare
    forgets pictures between turns, so each send carries them all."""
    seen: list[int] = []
    for m in messages:
        for asset_id in m.asset_ids_json or []:
            if asset_id not in seen:
                seen.append(asset_id)
    return seen


def needs_retry(messages: list[ChatMessage]) -> bool:
    """Whether the conversation is waiting on a reply it never got: it ends on the
    user's message, or on a reply that failed or has nothing in it."""
    if not messages:
        return False
    last = messages[-1]
    return last.role == "user" or bool(last.error) or not (last.content or "").strip()


def drop_failed_tail(session: Session, chat: Chat) -> None:
    """Before trying again: remove the failed or empty replies at the end."""
    while chat.messages and chat.messages[-1].role == "assistant" and needs_retry(chat.messages):
        session.delete(chat.messages.pop())
    session.flush()


# ---- the reply ----------------------------------------------------------------------
@dataclass(frozen=True)
class ReplyDeps:
    session_factory: sessionmaker[Session]
    paths: Paths
    client_factory: Any
    api_key: str
    transport: str


def _error_text(exc: Exception) -> str:
    if isinstance(exc, assets.MediaUploadError):
        return exc.message
    return classify(exc).message


def _tokens(usage: Any, key: str) -> int | None:
    value = usage.get(key) if isinstance(usage, dict) else None
    return int(value) if isinstance(value, (int, float)) else None


async def run_reply(
    deps: ReplyDeps, chat_id: int, emit: Callable[[str, dict], None], cancel: asyncio.Event
) -> int:
    """Answer the conversation's last message. Emits ``("delta", {"text"})`` for each
    piece and, always last, ``("done", {"message_id"})``; returns the saved reply's id.
    ``cancel`` being set (the browser left, or Stop) ends it with what was written so
    far kept and marked ``stopped``. Nothing is raised for a RunWare failure: it is
    saved on the reply, where the page shows it with Try again."""
    with db.session_scope(deps.session_factory) as s:
        chat = get(s, chat_id)
        if chat is None:
            raise LookupError(chat_id)
        model_air, system_prompt = chat.model_air, chat.system_prompt
        messages = history(chat.messages)
        wanted = picture_ids(chat.messages)
        # a model that cannot see refuses `inputs.images` outright; the pictures stay in
        # the conversation for when the model is switched back
        pictures = wanted if sees_pictures(catalog.get_by_air(s, model_air)) else []

    parts: list[str] = []
    result = None
    error: str | None = None
    try:
        async with deps.client_factory(deps.api_key, deps.transport) as client:
            images: list[str] = []
            if pictures:
                uuids = await assets.media_map(client, deps.session_factory, deps.paths, pictures)
                images = [uuids[i] for i in pictures if i in uuids]  # deleted ones are skipped
            task = tasks_chat.build_chat_task(
                model_air, messages, str(uuid.uuid4()), system_prompt=system_prompt, images=images
            )
            stream = await client.stream(
                task, StreamOptions(cancel_event=cancel, timeout=REPLY_TIMEOUT_MS)
            )
            async for piece in stream.text_stream:
                parts.append(piece)
                emit("delta", {"text": piece})
            result = await stream.result()
    except asyncio.CancelledError:
        cancel.set()  # the app is shutting down: keep what was written
        raise
    except Exception as e:  # noqa: BLE001 - shown on the reply
        if not cancel.is_set():
            error = _error_text(e)
    finally:
        message_id = _save_reply(deps, chat_id, model_air, "".join(parts), result, error, cancel)
        emit("done", {"message_id": message_id})
    return message_id


def _save_reply(deps, chat_id, model_air, text, result, error, cancel) -> int:
    stopped = cancel.is_set() and result is None
    if not stopped and not error and not text.strip():
        error = EMPTY_REPLY
    cost = getattr(result, "cost", None)
    usage = getattr(result, "usage", None)
    with db.session_scope(deps.session_factory) as s:
        msg = ChatMessage(
            chat_id=chat_id,
            role="assistant",
            content=text,
            model_air=model_air,
            cost=float(cost) if cost is not None else None,
            prompt_tokens=_tokens(usage, "promptTokens"),
            completion_tokens=_tokens(usage, "completionTokens"),
            finish_reason=STOPPED if stopped else getattr(result, "finish_reason", None),
            error=error,
        )
        s.add(msg)
        chat = get(s, chat_id)
        if chat is not None:
            chat.updated_at = utcnow()
        if cost is not None:
            costs.record_usage(
                s, job=None, task_type="textInference", cost=float(cost), model_air=model_air
            )
        s.flush()
        return msg.id
