from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import db
from ...runware.catalog_api import ContentAPI
from ...services import catalog
from .. import deps

router = APIRouter()
KINDS = ("image", "video", "text")


def _rows(request: Request, kind: str, include_hidden: bool) -> list[dict]:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ms = catalog.list_models(s, kind, include_hidden=include_hidden)
        return [_view(m) for m in ms]


def _view(m) -> dict:
    return {
        "id": m.id,
        "air": m.air,
        "name": m.name,
        "kind": m.kind,
        "label": catalog.label(m),
        "price_primary": m.price_primary,
        "price_unit": m.price_unit,
        "price_in": m.price_in,
        "price_out": m.price_out,
        "family": catalog.family(m),
        "is_favourite": m.is_favourite,
        "is_hidden": m.is_hidden,
        "capabilities": m.capabilities_json or [],
        "tiers": m.price_tiers_json or {},
        "source": m.source,
        "creator": m.creator,
    }


def _list_ctx(request: Request, kind: str, include_hidden: bool = False) -> dict:
    return {
        "kind": kind,
        "rows": _rows(request, kind, include_hidden),
        "include_hidden": include_hidden,
    }


def _refresh_ctx(request: Request, message: str | None = None, error: str | None = None) -> dict:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        at = catalog.last_refreshed(s)
    return {"last_refreshed": at, "message": message, "error": error}


@router.get("/models")
def models_page(request: Request):
    ctx = {"lists": {k: _list_ctx(request, k) for k in KINDS}, **_refresh_ctx(request)}
    return deps.render(request, "pages/models.html", ctx)


@router.get("/hx/models")
def hx_models(request: Request, kind: str = "image", hidden: int = 0):
    kind = kind if kind in KINDS else "image"
    return deps.render(request, "catalog/_list.html", _list_ctx(request, kind, bool(hidden)))


@router.get("/api/models")
def api_models(request: Request, kind: str = "image", hidden: int = 0):
    return JSONResponse(_rows(request, kind if kind in KINDS else "image", bool(hidden)))


@router.post("/hx/models/search")
async def hx_search(request: Request, form: deps.Form):
    q, kind = str(form.get("q", "")).strip(), str(form.get("kind", "image"))
    key = request.app.state.api_key()
    if not key:
        return deps.render(
            request,
            "catalog/_search_results.html",
            {
                "error": "Add your RunWare API key in Settings to search.",
                "results": [],
                "kind": kind,
            },
            422,
        )
    if not q:
        return deps.render(
            request, "catalog/_search_results.html", {"results": [], "kind": kind, "empty": True}
        )
    try:
        results = await catalog.search_live(
            request.app.state.client_factory,
            key,
            request.app.state.setting("runware.transport"),
            q,
            kind,
        )
    except catalog.SearchError as e:
        return deps.render(
            request,
            "catalog/_search_results.html",
            {"error": e.error.message, "results": [], "kind": kind},
            422,
        )
    for r in results:
        r["record_json"] = json.dumps({k: v for k, v in r.items() if k != "raw"})
    return deps.render(request, "catalog/_search_results.html", {"results": results, "kind": kind})


@router.post("/models/add")
def add_model(request: Request, form: deps.Form):
    kind = str(form.get("kind", "image"))
    try:
        record = json.loads(str(form.get("record", "{}")))
        if not (isinstance(record, dict) and record.get("air")):
            raise ValueError("record missing air")
    except ValueError:
        return JSONResponse({"error": "bad record"}, status_code=400)
    with db.session_scope(request.app.state.boot.session_factory) as s:
        m = catalog.add_from_search(s, record, kind)
        name = m.name
    ctx = _list_ctx(request, kind)
    ctx["toast"] = f"Added {name}"
    return deps.render(request, "catalog/_list.html", ctx)


@router.post("/models/refresh-prices")
async def refresh_prices(request: Request):
    try:
        res = await catalog.refresh_from_content_api(
            request.app.state.boot.session_factory, ContentAPI()
        )
    except Exception as e:  # noqa: BLE001
        return deps.render(
            request,
            "catalog/_refresh_status.html",
            _refresh_ctx(request, error=f"Refresh failed: {e}"),
            422,
        )
    if res.models == 0:
        detail = res.errors[0] if res.errors else "no models returned"
        return deps.render(
            request,
            "catalog/_refresh_status.html",
            _refresh_ctx(request, error=f"Refresh failed: {detail}"),
            422,
        )
    msg = f"Refreshed {res.models} models, {res.priced} priced, {len(res.errors)} error{'s' if len(res.errors) != 1 else ''}."
    return deps.render(request, "catalog/_refresh_status.html", _refresh_ctx(request, message=msg))


def _toggle(request: Request, model_id: int, field: str):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            m = catalog.set_flag(s, model_id, field)  # type: ignore[arg-type]
        except LookupError:
            return JSONResponse({"error": "unknown model"}, status_code=404)
        view = _view(m)
    return deps.render(request, "catalog/_row.html", {"m": view, "kind": view["kind"]})


@router.post("/models/{model_id}/favourite")
def favourite(request: Request, model_id: int):
    return _toggle(request, model_id, "is_favourite")


@router.post("/models/{model_id}/hide")
def hide(request: Request, model_id: int):
    return _toggle(request, model_id, "is_hidden")
