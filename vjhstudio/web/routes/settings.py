from __future__ import annotations

from fastapi import APIRouter, Request

from ... import db, secrets
from ...services import account, catalog, maintenance
from ...services import settings as settings_svc
from .. import deps
from .system import updates_context

router = APIRouter()


def _general_ctx(request: Request, saved: bool = False, error: str | None = None) -> dict:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        values = settings_svc.all_values(s, request.app.state.env)
        model_options = {k: catalog.list_models(s, k) for k in ("image", "video", "text")}
        labels = {m.air: catalog.label(m) for k in model_options for m in model_options[k]}
    return {
        "spec": settings_svc.SPEC,
        "values": values,
        "saved": saved,
        "error": error,
        "model_options": model_options,
        "labels": labels,
    }


def _key_ctx(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    balance: account.BalanceInfo | None = None,
) -> dict:
    paths = request.app.state.paths
    key = request.app.state.api_key()
    return {
        "masked": secrets.mask(key),
        "source": request.app.state.key_source(),
        "message": message,
        "error": error,
        "balance": balance,
        "env_locked": request.app.state.key_source() == "env",
        "paths": paths,
    }


def _maintenance_ctx(
    request: Request, message: str | None = None, error: str | None = None
) -> dict:
    return {"paths": request.app.state.paths, "message": message, "error": error}


@router.get("/settings")
def settings_page(request: Request):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        bal = account.cached_balance(s)
    return deps.render(
        request,
        "pages/settings.html",
        {
            **_general_ctx(request),
            **_key_ctx(request, balance=bal),
            **_maintenance_ctx(request),
            **updates_context(request),
        },
    )


@router.post("/settings")
def save_settings(request: Request, form: deps.Form):
    values = {k: str(v) for k, v in form.items() if k in settings_svc.SPEC}
    if str(form.get("_form", "")) == "general":
        # An unchecked checkbox posts nothing at all. The template already sends a
        # hidden "off" companion for each bool field, so a real browser submit never
        # hits this; it only matters for a partial post that omits the field entirely.
        for key, sp in settings_svc.SPEC.items():
            if sp.type is bool and key not in values:
                values[key] = "false"
    try:
        with db.session_scope(request.app.state.boot.session_factory) as s:
            settings_svc.set_many(s, values)
    except ValueError as e:
        return deps.render(
            request, "settings/_general_form.html", _general_ctx(request, error=str(e)), 422
        )
    runner = getattr(request.app.state, "runner", None)
    if runner is not None and "jobs.concurrency" in values:
        runner.set_concurrency(int(values["jobs.concurrency"]))
    return deps.render(request, "settings/_general_form.html", _general_ctx(request, saved=True))


@router.post("/settings/api-key")
def save_api_key(request: Request, form: deps.Form):
    if request.app.state.key_source() == "env":
        return deps.render(
            request,
            "settings/_api_key_response.html",
            _key_ctx(
                request, error="RUNWARE_API_KEY is set in the environment; the file is ignored."
            ),
            422,
        )
    try:
        secrets.write_api_key(request.app.state.paths, str(form.get("api_key", "")))
    except ValueError:
        return deps.render(
            request, "settings/_api_key_response.html", _key_ctx(request, error="Enter a key."), 422
        )
    return deps.render(
        request, "settings/_api_key_response.html", _key_ctx(request, message="Key saved.")
    )


@router.post("/settings/api-key/clear")
def clear_api_key(request: Request):
    secrets.clear_api_key(request.app.state.paths)
    return deps.render(
        request, "settings/_api_key_response.html", _key_ctx(request, message="Key removed.")
    )


@router.post("/settings/api-key/test")
async def test_api_key(request: Request):
    key = request.app.state.api_key()
    if not key:
        return deps.render(
            request,
            "settings/_api_key_response.html",
            _key_ctx(request, error="No API key set."),
            422,
        )
    try:
        bal = await account.refresh_balance(
            request.app.state.client_factory,
            key,
            request.app.state.setting("runware.transport"),
            request.app.state.boot.session_factory,
        )
    except account.BalanceError as e:
        return deps.render(
            request,
            "settings/_api_key_response.html",
            _key_ctx(request, error=e.error.message),
            422,
        )
    return deps.render(
        request,
        "settings/_api_key_response.html",
        _key_ctx(request, message="Key works.", balance=bal),
    )


@router.post("/settings/database/clear")
def clear_database(request: Request, form: deps.Form):
    backup_first = str(form.get("backup_first", "")) == "on"
    try:
        res = maintenance.clear_database(
            request.app.state.boot.session_factory,
            request.app.state.paths,
            backup_first=backup_first,
        )
    except FileNotFoundError:
        return deps.render(
            request,
            "settings/_maintenance_form.html",
            _maintenance_ctx(request, error="No database file found to back up."),
            422,
        )
    total = sum(res.rows_deleted.values())
    if res.backup_path:
        message = f"Database cleared (backup: {res.backup_path.name}). {total} rows removed."
    else:
        message = f"Database cleared (no backup taken). {total} rows removed."
    return deps.render(
        request, "settings/_maintenance_form.html", _maintenance_ctx(request, message=message)
    )


@router.get("/hx/header/balance")
def header_balance(request: Request):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        bal = account.cached_balance(s)
    return deps.render(request, "partials/_balance_chip.html", {"balance": bal})
