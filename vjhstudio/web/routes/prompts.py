"""Prompt library: filterable list, save from the Generate form, favourite, tags,
duplicate and delete.

Every handler here only touches the DB through a plain ``session_scope`` block (no
``await``), so every one of them stays a sync ``def`` and Starlette runs it in its
threadpool instead of blocking the event loop -- matching the convention in
``routes/assets.py`` and ``routes/gallery.py``.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from ... import db
from ...models import Project
from ...schemas.image import PromptForm
from ...services import generate as generate_svc
from ...services import projects as projects_svc
from ...services import prompts as prompts_svc
from ...services import settings as settings_svc
from .. import deps
from .generate import PROMPT_FIELDS, _flag

router = APIRouter()

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def _bool(raw: str) -> bool | None:
    v = raw.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


def _int_or_none(raw: str) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None  # a malformed or stale bookmarked filter is just "no filter", not a 500


def _filters_from(request: Request) -> dict:
    q = request.query_params
    return {
        "q": q.get("q") or None,
        "kind": q.get("kind") or None,
        "favourite": _bool(q.get("favourite", "")),
        "project_id": _int_or_none(q.get("project_id", "")),
        "tag": q.get("tag") or None,
    }


def _project_names(session) -> dict[int, str]:
    return dict(session.execute(select(Project.id, Project.name)).all())


def _list_ctx(request: Request, filters: dict, page: int) -> dict:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        rows, total = prompts_svc.list_prompts(s, page=page, **filters)
        names = _project_names(s)
    has_more = page * prompts_svc.PER_PAGE < total
    return {
        "rows": rows,
        "project_names": names,
        "page": page,
        "has_more": has_more,
        "next_page": page + 1,
        "total": total,
    }


def _row_ctx(session, prompt, *, toast: str | None = None) -> dict:
    names = _project_names(session)
    return {"p": prompt, "project_name": names.get(prompt.project_id, ""), "toast": toast}


@router.get("/prompts")
def prompts_page(request: Request):
    filters = _filters_from(request)
    with db.session_scope(request.app.state.boot.session_factory) as s:
        proj = projects_svc.list_all(s)
    ctx = {"filters": filters, "projects": proj}
    ctx.update(_list_ctx(request, filters, 1))
    return deps.render(request, "pages/prompts.html", ctx)


@router.get("/hx/prompts")
def hx_prompts(request: Request, page: int = 1):
    filters = _filters_from(request)
    ctx = _list_ctx(request, filters, max(1, page))
    template = "prompts/_list.html" if ctx["page"] <= 1 else "prompts/_list_page.html"
    return deps.render(request, template, ctx)


def _parse_form(form, no_text_default: bool = True) -> PromptForm:
    """``no_text_default`` mirrors the two submit parsers: stills default to suppressing
    on-screen text, clips default the other way (``parse_video_request``), so a form that
    omits the box produces the same ``PromptForm`` -- and the same hash -- either way."""
    return PromptForm(
        **{k: str(form.get(k, "") or "") for k in PROMPT_FIELDS},
        use_default_negative=_flag(form, "use_default_negative", True),
        no_text=_flag(form, "no_text", no_text_default),
    )


class _TitleShim:
    """``generate.title_for`` only ever reads ``.title``/``.form`` off the request it is
    given; a Save-prompt post carries neither a full ``ImageRequest`` nor a
    ``VideoRequest``, so this stands in for one rather than duplicating the fallback
    chain (posted title -> composed prompt -> "Untitled") a second time."""

    def __init__(self, title: str | None, form: PromptForm):
        self.title = title
        self.form = form


@router.post("/prompts")
def save_prompt(request: Request, form: deps.Form):
    """Save (or find) a prompt straight from the Generate form's builder fields. A
    second identical submission dedupes onto the same row (``prompts.upsert``'s
    content-hash match) rather than creating a new one -- the response still carries
    the row and an ``HX-Trigger`` naming which happened, so Task 4/5's Save-prompt
    dialog can tell the two apart without a second round trip."""
    app = request.app
    project_id = _int_or_none(str(form.get("project_id", "") or ""))
    mode = str(form.get("mode", "") or "").strip().lower()
    kind = "video" if mode == "video" else "image"
    pf = _parse_form(form, no_text_default=kind != "video")
    final_raw = str(form.get("final_prompt", "") or "").strip()
    title = str(form.get("title", "") or "").strip()
    tags = str(form.get("tags", "") or "")
    polish_json = prompts_svc.parse_polish_json(str(form.get("polish_json", "") or ""))

    with db.session_scope(app.state.boot.session_factory) as s:
        if project_id is None:
            return deps.render(
                request,
                "partials/_toast.html",
                {"text": "Choose a project before saving.", "level": "error"},
                422,
            )
        if projects_svc.get(s, project_id) is None:
            return deps.render(
                request,
                "partials/_toast.html",
                {"text": f"Project {project_id} does not exist.", "level": "error"},
                422,
            )
        default_negative = settings_svc.get(s, "defaults.negative_prompt", app.state.env)
        # one helper, shared with generate.enqueue_image/enqueue_video: a prompt saved
        # here and the same prompt submitted there hash alike and dedupe onto one row
        _, final, negative = prompts_svc.saved_texts(kind, pf, final_raw, default_negative)
        if not title:
            title = generate_svc.title_for(_TitleShim(None, pf))
        prompt, created = prompts_svc.upsert(
            s,
            project_id=project_id,
            kind=kind,
            title=title,
            form=pf,
            final_prompt=final,
            negative_prompt=negative,
            tags=tags,
            polish_json=polish_json,
        )
        toast = "Prompt saved." if created else "Prompt already saved."
        ctx = _row_ctx(s, prompt, toast=toast)

    r = deps.render(request, "prompts/_row.html", ctx)
    r.headers["HX-Trigger"] = json.dumps({"prompt-saved": {"id": prompt.id, "created": created}})
    return r


@router.post("/prompts/{prompt_id}/favourite")
def favourite_prompt(request: Request, prompt_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            prompt = prompts_svc.set_favourite(s, prompt_id)
        except LookupError as e:
            raise HTTPException(status_code=404, detail="unknown prompt") from e
        ctx = _row_ctx(s, prompt)
    return deps.render(request, "prompts/_row.html", ctx)


@router.post("/prompts/{prompt_id}/duplicate")
def duplicate_prompt(request: Request, prompt_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            copy = prompts_svc.duplicate(s, prompt_id)
        except LookupError as e:
            raise HTTPException(status_code=404, detail="unknown prompt") from e
        ctx = _row_ctx(s, copy)
    return deps.render(request, "prompts/_row.html", ctx)


@router.post("/prompts/{prompt_id}/tags")
def set_prompt_tags(request: Request, prompt_id: int, form: deps.Form):
    tags = str(form.get("tags", "") or "")
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            prompt = prompts_svc.set_tags(s, prompt_id, tags)
        except LookupError as e:
            raise HTTPException(status_code=404, detail="unknown prompt") from e
        ctx = _row_ctx(s, prompt)
    return deps.render(request, "prompts/_row.html", ctx)


@router.delete("/prompts/{prompt_id}")
def delete_prompt(request: Request, prompt_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ok = prompts_svc.delete(s, prompt_id)
    if not ok:
        raise HTTPException(status_code=404, detail="unknown prompt")
    return Response(status_code=200)
