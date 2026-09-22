from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import db
from ...runware.catalog_api import ContentAPI
from ...services import catalog, constraints
from .. import deps
from .system import _local_only

router = APIRouter()
KINDS = ("image", "video", "text")


_view = catalog.view  # one shape for /api/models, the row partials and the task builders


def _row_view(m) -> dict:
    """``catalog.view`` plus the three things only the Models table needs: the row's one
    warning chip, whether the model can be generated with at all, and how much is known
    about its sizes."""
    v = _view(m)
    v["badge"] = catalog.badge(m)
    v["generate_capable"] = constraints.is_generate_capable(
        m.kind, m.capabilities_json or [], m.constraints_json
    )
    v["dims_mode"] = ((m.constraints_json or {}).get("dims") or {}).get("mode") or "unknown"
    return v


def _rows(request: Request, kind: str, include_hidden: bool) -> list[dict]:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ms = catalog.list_models(s, kind, include_hidden=include_hidden)
        return [_row_view(m) for m in ms]


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
    ctx = {
        "lists": {k: _list_ctx(request, k) for k in KINDS},
        **_refresh_ctx(request),
        "harvest": constraints.STATE.snapshot(),
    }
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
    if kind not in KINDS:
        return JSONResponse({"error": "bad kind"}, status_code=400)
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
        view = _row_view(m)
    return deps.render(request, "catalog/_row.html", {"m": view, "kind": view["kind"]})


@router.post("/models/{model_id}/favourite")
def favourite(request: Request, model_id: int):
    return _toggle(request, model_id, "is_favourite")


@router.post("/models/{model_id}/hide")
def hide(request: Request, model_id: int):
    return _toggle(request, model_id, "is_hidden")


# --- constraint harvest ----------------------------------------------------


def _harvest_ctx(conflict: str = "") -> dict:
    return {"harvest": constraints.STATE.snapshot(), "conflict": conflict}


@router.post("/models/harvest")
async def harvest_models(request: Request):
    """Start the (free) constraint harvest in the background. The partial it returns
    polls itself until the run is over, exactly like the update log does."""
    _local_only(request)
    started = constraints.start_harvest(
        request.app.state,
        api_key=request.app.state.api_key() or "",
        transport=request.app.state.setting("runware.transport"),
    )
    return deps.render(
        request,
        "catalog/_harvest_status.html",
        _harvest_ctx("" if started else "A harvest is already running."),
        200 if started else 409,
    )


@router.get("/hx/models/harvest-status")
def harvest_status(request: Request):
    """Local only as well as the POST: a stopped run prints the account balance."""
    _local_only(request)
    return deps.render(request, "catalog/_harvest_status.html", _harvest_ctx())
