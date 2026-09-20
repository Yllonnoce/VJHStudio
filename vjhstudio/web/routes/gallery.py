"""Gallery: filterable grid of outputs, lightbox detail, favourite, delete, download."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ... import db
from ...models import Output, Project
from ...services import catalog, projects
from ...services import outputs as outputs_svc
from .. import deps
from ..urls import output_url, thumb_url

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


def _safe_date(raw: str) -> str | None:
    if not raw:
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None  # a malformed date is just "no filter", not a 500
    return raw


def _filters_from(request: Request) -> dict:
    q = request.query_params
    return {
        "project_id": _int_or_none(q.get("project_id", "")),
        "kind": q.get("kind") or None,
        "model": q.get("model") or None,
        "favourite": _bool(q.get("favourite", "")),
        "q": q.get("q") or None,
        "date_from": _safe_date(q.get("date_from", "")),
        "date_to": _safe_date(q.get("date_to", "")),
    }


def _slugs(session) -> dict[int, str]:
    return dict(session.execute(select(Project.id, Project.slug)).all())


def _card_ctx(output: Output, slug: str) -> dict:
    return {
        "o": output,
        "url": output_url(slug, output.filename),
        "thumb": thumb_url(output.thumb_rel_path),
    }


def _grid_ctx(request: Request, filters: dict, page: int) -> dict:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        rows, total = outputs_svc.gallery(s, page=page, **filters)
        slugs = _slugs(s)
        cards = [_card_ctx(o, slugs.get(o.project_id, "")) for o in rows]
    has_more = page * outputs_svc.PER_PAGE < total
    return {
        "cards": cards,
        "page": page,
        "has_more": has_more,
        "next_page": page + 1,
        "total": total,
    }


@router.get("/gallery")
def gallery_page(request: Request):
    filters = _filters_from(request)
    with db.session_scope(request.app.state.boot.session_factory) as s:
        proj = projects.list_all(s)
        models = catalog.list_models(s, "image") + catalog.list_models(s, "video")
        labels = {m.air: catalog.label(m) for m in models}
    ctx = {"filters": filters, "projects": proj, "models": models, "labels": labels}
    ctx.update(_grid_ctx(request, filters, 1))
    return deps.render(request, "pages/gallery.html", ctx)


@router.get("/hx/gallery")
def hx_gallery(request: Request, page: int = 1):
    filters = _filters_from(request)
    ctx = _grid_ctx(request, filters, max(1, page))
    template = "gallery/_grid.html" if page <= 1 else "gallery/_grid_page.html"
    return deps.render(request, template, ctx)


@router.get("/hx/outputs/{output_id}")
def hx_output_detail(request: Request, output_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        o = outputs_svc.get(s, output_id)
        if o is None:
            raise HTTPException(status_code=404, detail="unknown output")
        project = s.get(Project, o.project_id)
        slug = project.slug if project else ""
        model = catalog.get_by_air(s, o.model_air)
        ctx = {
            "o": o,
            "url": output_url(slug, o.filename),
            "thumb": thumb_url(o.thumb_rel_path),
            "model_name": catalog.label(model) if model else o.model_air,
        }
        return deps.render(request, "gallery/_detail.html", ctx)


@router.post("/outputs/{output_id}/favourite")
def favourite_output(request: Request, output_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            o = outputs_svc.toggle_favourite(s, output_id)
        except LookupError as e:
            raise HTTPException(status_code=404, detail="unknown output") from e
        project = s.get(Project, o.project_id)
        ctx = _card_ctx(o, project.slug if project else "")
        return deps.render(request, "gallery/_card.html", ctx)


@router.delete("/outputs/{output_id}")
def delete_output(request: Request, output_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ok = outputs_svc.delete(s, request.app.state.paths, output_id)
    if not ok:
        raise HTTPException(status_code=404, detail="unknown output")
    r = Response(status_code=200)
    r.headers["HX-Trigger"] = "close-lightbox"
    return r


@router.get("/outputs/{output_id}/download")
def download_output(request: Request, output_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        o = outputs_svc.get(s, output_id)
        if o is None:
            raise HTTPException(status_code=404, detail="unknown output")
        project = s.get(Project, o.project_id)
        slug = project.slug if project else ""
    return RedirectResponse(f"{output_url(slug, o.filename)}?download=1", status_code=302)
