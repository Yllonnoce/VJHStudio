"""Asset library: filterable grid, drag-and-drop upload, tags/notes, delete, and a
compact "reference picker" grid the generate form (Task 5) embeds.

Only ``upload_assets`` and ``push_asset`` are ``async def``: they are the only two
handlers that ``await`` anything (reading multipart file bytes off the wire, and
calling RunWare's media_storage endpoint). Every other handler here only touches the
DB through a plain ``session_scope`` block and stays a sync ``def`` so Starlette runs
it in its threadpool instead of blocking the event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from runware import RunwareError
from starlette.datastructures import UploadFile

from ... import db
from ...models import Asset
from ...runware.errors import classify
from ...services import assets as assets_svc
from .. import deps
from ..urls import asset_thumb_url, asset_url

router = APIRouter()

REFERENCED_MESSAGE = "This asset is used by a queued or running job and can't be deleted yet."


def _filters_from(request: Request) -> dict:
    q = request.query_params
    return {
        "kind": q.get("kind") or None,
        "tag": q.get("tag") or None,
        "q": q.get("q") or None,
    }


def _card_ctx(asset: Asset, *, error: str | None = None) -> dict:
    return {
        "a": asset,
        "url": asset_url(asset.filename),
        "thumb_url": asset_thumb_url(assets_svc.thumb_rel(asset)),
        "error": error,
    }


def _grid_ctx(request: Request, filters: dict, page: int) -> dict:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        rows, total = assets_svc.list_assets(s, page=page, **filters)
        cards = [_card_ctx(a) for a in rows]
    has_more = page * assets_svc.PER_PAGE < total
    return {
        "cards": cards,
        "page": page,
        "has_more": has_more,
        "next_page": page + 1,
        "total": total,
    }


@router.get("/assets")
def assets_page(request: Request):
    filters = _filters_from(request)
    ctx = {"filters": filters, "max_mb": request.app.state.setting("uploads.max_mb")}
    ctx.update(_grid_ctx(request, filters, 1))
    return deps.render(request, "pages/assets.html", ctx)


@router.get("/hx/assets")
def hx_assets(request: Request, page: int = 1):
    filters = _filters_from(request)
    ctx = _grid_ctx(request, filters, max(1, page))
    return deps.render(request, "assets/_grid.html", ctx)


@router.post("/assets/upload")
async def upload_assets(request: Request):
    """Multipart ``files`` (one or more) + a shared ``tags`` field. Each file is stored
    independently: a 413/415 from one bad file is collected into a toast rather than
    failing the whole batch. The grid always reflects the current DB state afterwards;
    only the status code says whether anything actually got through (200) or every
    file was rejected (422)."""
    app = request.app
    form = await request.form()
    tags = str(form.get("tags", "") or "")
    uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
    max_mb = app.state.setting("uploads.max_mb")

    errors: list[str] = []
    duplicates: list[str] = []
    stored = 0
    if not uploads:
        errors.append("No files were selected.")

    with db.session_scope(app.state.boot.session_factory) as s:
        for f in uploads:
            name = f.filename or "upload"
            content = await f.read()
            mime = f.content_type or "application/octet-stream"
            try:
                _asset, created = assets_svc.store_upload(
                    s,
                    app.state.paths,
                    original_name=name,
                    content=content,
                    mime=mime,
                    tags=tags,
                    max_mb=max_mb,
                )
            except assets_svc.UploadError as e:
                errors.append(f"{name}: {e}")
                continue
            stored += 1
            if not created:
                duplicates.append(name)

    filters = _filters_from(request)
    ctx = _grid_ctx(request, filters, 1)
    ctx["toasts"] = [
        {"text": f"{name} was already in your library.", "level": "info"} for name in duplicates
    ] + [{"text": msg, "level": "error"} for msg in errors]
    status = 200 if stored else 422
    return deps.render(request, "assets/_grid.html", ctx, status)


@router.post("/assets/{asset_id}/tags")
def set_asset_tags(request: Request, asset_id: int, form: deps.Form):
    tags = str(form.get("tags", "") or "")
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            a = assets_svc.set_tags(s, asset_id, tags)
        except LookupError as e:
            raise HTTPException(status_code=404, detail="unknown asset") from e
        ctx = _card_ctx(a)
    return deps.render(request, "assets/_card.html", ctx)


@router.post("/assets/{asset_id}/notes")
def set_asset_notes(request: Request, asset_id: int, form: deps.Form):
    notes = str(form.get("notes", "") or "") or None
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            a = assets_svc.set_notes(s, asset_id, notes)
        except LookupError as e:
            raise HTTPException(status_code=404, detail="unknown asset") from e
        ctx = _card_ctx(a)
    return deps.render(request, "assets/_card.html", ctx)


@router.delete("/assets/{asset_id}")
def delete_asset(request: Request, asset_id: int):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        asset = assets_svc.get(s, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="unknown asset")
        ok = assets_svc.delete(s, request.app.state.paths, asset_id)
    if not ok:
        return JSONResponse({"error": REFERENCED_MESSAGE}, status_code=409)
    return Response(status_code=200)


@router.post("/assets/{asset_id}/push")
async def push_asset(request: Request, asset_id: int):
    """Force an upload to RunWare's media store right now (rather than waiting for a
    job that references this asset to trigger it lazily) so the card can show the
    "uploaded to RunWare" badge immediately."""
    app = request.app
    with db.session_scope(app.state.boot.session_factory) as s:
        asset = assets_svc.get(s, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="unknown asset")

    api_key = app.state.api_key()
    if not api_key:
        ctx = _card_ctx(asset, error="No API key set. Add one in Settings before pushing.")
        return deps.render(request, "assets/_card.html", ctx, 422)

    transport = app.state.setting("runware.transport")
    error: str | None = None
    try:
        async with app.state.client_factory(api_key, transport) as client:
            await assets_svc.ensure_media_uuid(
                client, app.state.boot.session_factory, app.state.paths, asset_id
            )
    except assets_svc.MediaUploadError as e:
        error = e.message
    except RunwareError as e:
        error = classify(e).message

    with db.session_scope(app.state.boot.session_factory) as s:
        fresh = assets_svc.get(s, asset_id)
        ctx = _card_ctx(fresh, error=error)
    return deps.render(request, "assets/_card.html", ctx, 200 if error is None else 422)


@router.get("/hx/assets/picker")
def hx_assets_picker(request: Request, kind: str = "", role: str = ""):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        rows, _total = assets_svc.list_assets(s, kind=kind or None, page=1)
        cards = [_card_ctx(a) for a in rows]
    ctx = {"cards": cards, "role": role, "kind": kind}
    return deps.render(request, "assets/_picker.html", ctx)
