"""The Chat page: saved conversations with RunWare's text models.

``POST /chat/send`` answers with an event stream (``start``, ``delta``…, ``done``) that
``chat.js`` reads with ``fetch``. It is deliberately not an ``EventSource``: that would
reconnect by itself after a dropped connection and silently re-send a billed request.
The reply itself runs as its own task (``services.chat.run_reply``); this response only
relays it, and tells it to stop when the browser goes away."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ... import db
from ...models import Asset, Chat, ChatMessage
from ...services import assets as assets_svc
from ...services import chat as chat_svc
from ...services import markdown, projects
from ...services import outputs as outputs_svc
from .. import deps
from ..urls import asset_thumb_url, thumb_url

router = APIRouter()
_TRUTHY = ("1", "true", "yes", "on")
PICKER_OUTPUTS = 24
NO_KEY = "Add your RunWare API key in Settings to chat."


def cost_label(cost: float | None) -> str:
    if cost is None:
        return ""
    return "under $0.0001" if 0 < cost < 0.0001 else f"${cost:.4f}"


def _pictures(session, messages: list[ChatMessage]) -> dict[int, dict]:
    ids = chat_svc.picture_ids(messages)
    rows = session.query(Asset).filter(Asset.id.in_(ids)).all() if ids else []
    return {
        a.id: {"name": a.original_name, "thumb_url": asset_thumb_url(assets_svc.thumb_rel(a))}
        for a in rows
    }


def _message_view(m: ChatMessage, pictures: dict, names: dict, retry: bool = False) -> dict:
    """What ``chat/_message.html`` draws. A picture whose asset was deleted since is
    still listed (with no thumbnail), so the conversation reads the same."""
    view = {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "error": m.error,
        "retry": retry,
    }
    if m.role == "user":
        view["pictures"] = [
            pictures.get(i, {"name": "", "thumb_url": None}) for i in m.asset_ids_json or []
        ]
    else:
        view |= {
            "html": markdown.render(m.content),
            "model_name": names.get(m.model_air, m.model_air or ""),
            "cost_label": cost_label(m.cost),
            "stopped": m.finish_reason == chat_svc.STOPPED,
        }
    return view


def _thread_ctx(session, chat: Chat | None) -> dict:
    messages = list(chat.messages) if chat is not None else []
    pictures = _pictures(session, messages)
    names = {m["air"]: m["name"] for m in chat_svc.chat_models(session)}
    waiting = chat_svc.needs_retry(messages)
    last = messages[-1] if messages else None
    views = [
        _message_view(m, pictures, names, retry=waiting and m is last and m.role == "assistant")
        for m in messages
    ]
    return {
        "messages": views,
        # the last message is the user's and nothing answered it
        "unanswered": waiting and last is not None and last.role == "user",
        "has_pictures": bool(pictures) or any(m.asset_ids_json for m in messages),
    }


def _list_ctx(session, current_id: int | None) -> dict:
    return {"chats": chat_svc.list_chats(session), "current_id": current_id}


def _page(request: Request, chat_id: int | None):
    app = request.app
    with db.session_scope(app.state.boot.session_factory) as s:
        chat = chat_svc.get(s, chat_id) if chat_id is not None else None
        if chat_id is not None and chat is None:
            raise HTTPException(status_code=404, detail="unknown chat")
        models = chat_svc.chat_models(s)
        ctx = _thread_ctx(s, chat) | _list_ctx(s, chat_id)
        ctx |= {
            "chat": chat,
            "models": models,
            "model": chat_svc.default_air(
                models, chat.model_air if chat else None, app.state.setting("defaults.chat_model")
            ),
            "total": cost_label(chat_svc.total_cost(s, chat.id)) if chat else "",
        }
        return deps.render(request, "pages/chat.html", ctx)


@router.get("/chat")
def chat_new(request: Request):
    return _page(request, None)


@router.get("/chat/{chat_id}")
def chat_page(request: Request, chat_id: int):
    return _page(request, chat_id)


@router.get("/hx/chat/list")
def hx_chat_list(request: Request, current: int | None = None):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        return deps.render(request, "chat/_list.html", _list_ctx(s, current))


@router.get("/hx/chat/{chat_id}/thread")
def hx_chat_thread(request: Request, chat_id: int):
    """The conversation as it now stands: the page reloads it after Stop, where the
    reply was saved by the server after the browser had stopped listening."""
    with db.session_scope(request.app.state.boot.session_factory) as s:
        chat = chat_svc.get(s, chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="unknown chat")
        return deps.render(request, "chat/_thread.html", _thread_ctx(s, chat))


@router.post("/chat/{chat_id}/rename")
def chat_rename(request: Request, chat_id: int, form: deps.Form):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            chat_svc.rename(s, chat_id, str(form.get("title", "")))
        except LookupError:
            raise HTTPException(status_code=404, detail="unknown chat") from None
        current = form.get("current")
        current_id = int(current) if str(current or "").isdigit() else chat_id
        return deps.render(request, "chat/_list.html", _list_ctx(s, current_id))


@router.post("/chat/{chat_id}/delete")
def chat_delete(request: Request, chat_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        if not chat_svc.delete(s, chat_id):
            raise HTTPException(status_code=404, detail="unknown chat")
    return JSONResponse({"deleted": chat_id})


# ---- pictures -----------------------------------------------------------------------
@router.get("/hx/chat/picker")
def hx_chat_picker(request: Request):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        rows, _total = assets_svc.list_assets(s, kind="image", page=1)
        picks = [
            {
                "id": a.id,
                "name": a.original_name,
                "thumb_url": asset_thumb_url(assets_svc.thumb_rel(a)),
            }
            for a in rows
        ]
        recent, _total = outputs_svc.gallery(s, kind="image", per_page=PICKER_OUTPUTS)
        outputs = [
            {"id": o.id, "name": o.filename, "thumb_url": thumb_url(o.thumb_rel_path)}
            for o in recent
        ]
    return deps.render(request, "chat/_picker.html", {"assets": picks, "outputs": outputs})


@router.post("/chat/attach-output/{output_id}")
def chat_attach_output(request: Request, output_id: int):
    """A Gallery picture, made attachable: imported into Assets (deduplicated by
    content, as "Use as reference" does) and described the way an upload is."""
    app = request.app
    with db.session_scope(app.state.boot.session_factory) as s:
        o = outputs_svc.get(s, output_id)
        if o is None:
            raise HTTPException(status_code=404, detail="unknown output")
        try:
            asset = assets_svc.import_output(s, app.state.paths, o, projects.root_override(s))
        except assets_svc.UploadError as e:
            raise HTTPException(status_code=e.status, detail=str(e)) from e
        return JSONResponse(
            {
                "id": asset.id,
                "name": asset.original_name,
                "thumb_url": asset_thumb_url(assets_svc.thumb_rel(asset)),
            }
        )


# ---- sending ------------------------------------------------------------------------
def _refuse(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _render(name: str, ctx: dict) -> str:
    return deps.templates.get_template(name).render(ctx)


def _reply_event(session_factory, chat_id: int, message_id: int) -> dict:
    with db.session_scope(session_factory) as s:
        chat = chat_svc.get(s, chat_id)
        msg = s.get(ChatMessage, message_id)
        if chat is None or msg is None:
            return {"html": "", "total": "", "retry": False}
        retry = chat_svc.needs_retry(list(chat.messages))
        names = {m["air"]: m["name"] for m in chat_svc.chat_models(s)}
        view = _message_view(msg, {}, names, retry=retry)
        return {
            "html": _render("chat/_message.html", {"m": view}),
            "total": cost_label(chat_svc.total_cost(s, chat_id)),
            "retry": retry,
        }


@router.post("/chat/send")
async def chat_send(request: Request):
    """Form: ``message``, ``model``, ``system_prompt``, ``asset_ids`` (repeated),
    ``chat_id`` (empty for a new conversation), or ``retry=1`` with a ``chat_id`` to
    answer a message whose reply failed. Refusals are JSON ``{"error"}`` with a 4xx and
    happen before anything is saved or sent."""
    app = request.app
    form = await request.form()
    key = app.state.api_key()
    if not key:
        return _refuse(422, NO_KEY)
    text = str(form.get("message", "") or "").strip()
    model = str(form.get("model", "") or "")
    retry = str(form.get("retry", "")).lower() in _TRUTHY
    raw_id = str(form.get("chat_id", "") or "").strip()
    asset_ids = [int(v) for v in form.getlist("asset_ids") if str(v).isdigit()]
    session_factory = app.state.boot.session_factory

    with db.session_scope(session_factory) as s:
        if model not in [m["air"] for m in chat_svc.chat_models(s)]:
            return _refuse(422, "Choose a model to chat with.")
        chat = chat_svc.get(s, int(raw_id)) if raw_id.isdigit() else None
        if (raw_id or retry) and chat is None:
            return _refuse(404, "This conversation no longer exists.")
        user_html, new = "", False
        if retry:
            if not chat_svc.needs_retry(list(chat.messages)):
                return _refuse(409, "There is nothing to try again.")
            chat_svc.drop_failed_tail(s, chat)
        else:
            if not text:
                return _refuse(422, "Write a message first.")
            if chat is None:
                chat, new = (
                    chat_svc.create(s, title=chat_svc.title_from(text), model_air=model),
                    True,
                )
            msg = chat_svc.add_user_message(s, chat, text, asset_ids)
            view = _message_view(msg, _pictures(s, [msg]), {})
            user_html = _render("chat/_message.html", {"m": view})
        chat.model_air = model
        if "system_prompt" in form:
            chat.system_prompt = str(form.get("system_prompt") or "").strip()
        chat_id = chat.id
        start = {
            "chat_id": chat_id,
            "url": f"/chat/{chat_id}",
            "title": chat.title,
            "new": new,
            "user_html": user_html,
        }

    queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
    cancel = asyncio.Event()
    reply_deps = chat_svc.ReplyDeps(
        session_factory,
        app.state.paths,
        app.state.client_factory,
        key,
        app.state.setting("runware.transport"),
    )
    task = asyncio.create_task(
        chat_svc.run_reply(reply_deps, chat_id, lambda k, p: queue.put_nowait((k, p)), cancel)
    )
    app.state.chat_tasks.add(task)  # a task nobody holds can be collected mid-flight

    def _finished(t: asyncio.Task) -> None:
        app.state.chat_tasks.discard(t)
        if t.cancelled() or t.exception() is not None:
            queue.put_nowait(("gone", {}))  # never leave the response waiting

    task.add_done_callback(_finished)

    async def body():
        try:
            yield _sse("start", start)
            while True:
                kind, payload = await queue.get()
                if kind == "delta":
                    yield _sse("delta", payload)
                    continue
                if kind == "done":
                    yield _sse(
                        "done", _reply_event(session_factory, chat_id, payload["message_id"])
                    )
                else:
                    yield _sse("done", {"html": "", "total": "", "retry": True})
                return
        finally:
            # the browser went away (Stop, or the page was closed): the task keeps what
            # it has written and ends by itself
            if not task.done():
                cancel.set()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
