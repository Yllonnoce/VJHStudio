from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Request

from ... import config, db, secrets
from ...services import account, automation, catalog, maintenance
from ...services import settings as settings_svc
from .. import deps
from .system import _local_only, backups_context, updates_context

router = APIRouter()

# The three mcp.* keys have their own card (with real labels, the token and the
# copy-paste blocks), so the General form - which otherwise renders every key in
# SPEC - leaves them alone. Both ends of the general route filter on this prefix.
MCP_PREFIX = "mcp."

# form field -> settings key. The form cannot use the dotted keys: they would be
# picked up by POST /settings, which saves anything in SPEC that it is posted.
MCP_FIELDS = {
    "mcp_enabled": "mcp.enabled",
    "mcp_daily_cap_usd": "mcp.daily_cap_usd",
    "mcp_max_jobs_per_day": "mcp.max_jobs_per_day",
}


MCP_FIELD_OF = {v: k for k, v in MCP_FIELDS.items()}
MCP_NUMBERS = ("mcp.daily_cap_usd", "mcp.max_jobs_per_day")
MCP_LABELS = {
    "mcp.enabled": "Let agents connect",
    "mcp.daily_cap_usd": "The daily spend cap",
    "mcp.max_jobs_per_day": "Most jobs a day",
}


def _negative(raw: str) -> bool:
    """True only for a number below zero; a value that is not a number at all is left
    to set_many, which says what type the key wants."""
    try:
        return float(raw) < 0
    except ValueError:
        return False


def general_spec() -> dict[str, settings_svc.Spec]:
    return {k: sp for k, sp in settings_svc.SPEC.items() if not k.startswith(MCP_PREFIX)}


LOOPBACK = ("127.0.0.1", "::1", "localhost", "testclient")


def _browser_host(request: Request) -> tuple[str, int, bool]:
    """The address the browser actually reached this page on, and whether it is a
    loopback one.

    Not the bind address: the setup this card documents is VJHSTUDIO_HOST=0.0.0.0,
    and `http://0.0.0.0:8080/mcp` is a wildcard to listen on, not one an agent can
    dial. Whatever is in the URL bar worked, by definition - for a phone on the LAN
    that is the computer's real address, and for the owner it is 127.0.0.1.
    """
    host = request.url.hostname or config.DEFAULT_HOST
    port = request.url.port or request.app.state.port
    # a bare IPv6 address needs its brackets back before it goes into a URL
    return (f"[{host}]" if ":" in host else host), port, host in LOOPBACK


def _venv_bin() -> tuple[str, str]:
    """The directory the `vjhstudio` command lives in, for the stdio snippet.

    `sys.executable` is the interpreter inside the virtual environment, so its parent
    is the command directory (`.venv/bin`, or `.venv\\Scripts` on Windows). Translating
    a POSIX path into backslashes would only produce a path that exists nowhere, so
    off Windows the Windows line is a placeholder of the right shape instead.
    """
    bin_dir = Path(sys.executable).parent
    win = str(bin_dir) if sys.platform == "win32" else "<your VJHStudio folder>\\.venv\\Scripts"
    return bin_dir.as_posix(), win


def _general_ctx(request: Request, saved: bool = False, error: str | None = None) -> dict:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        values = settings_svc.all_values(s, request.app.state.env)
        model_options = {k: catalog.list_models(s, k) for k in ("image", "video", "text")}
        labels = {m.air: catalog.label(m) for k in model_options for m in model_options[k]}
    return {
        "spec": general_spec(),
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


def _automation_ctx(
    request: Request,
    *,
    saved: bool = False,
    error: str | None = None,
    error_field: str | None = None,
    message: str | None = None,
    token: str | None = None,
    typed: dict | None = None,
) -> dict:
    """The Automation card. ``token`` is the one and only way the real token reaches
    the page: a plain /settings render passes None and the card shows the mask.
    ``typed`` puts the rejected form back the way the user left it, so a 422 does not
    quietly throw away the field they got right."""
    app = request.app
    paths, env = app.state.paths, app.state.env
    with db.session_scope(app.state.boot.session_factory) as s:
        values = settings_svc.all_values(s, env)
        cap = automation.status(s)
    shown = {
        "mcp.enabled": values["mcp.enabled"],
        "mcp.daily_cap_usd": values["mcp.daily_cap_usd"],
        "mcp.max_jobs_per_day": values["mcp.max_jobs_per_day"],
    } | (typed or {})
    host, port, host_is_local = _browser_host(request)
    venv_bin, venv_bin_win = _venv_bin()
    return {
        "mcp_enabled": shown["mcp.enabled"],
        "mcp_daily_cap_usd": shown["mcp.daily_cap_usd"],
        "mcp_max_jobs_per_day": shown["mcp.max_jobs_per_day"],
        "mcp_saved": saved,
        "mcp_error": error,
        "mcp_error_field": error_field,
        "mcp_message": message,
        "mcp_token": token,
        "mcp_masked": secrets.mask(secrets.effective_mcp_token(paths, env)) or "\u2022" * 8,
        "mcp_token_source": secrets.mcp_token_source(paths, env),
        "mcp_token_file": secrets.mcp_token_file(paths),
        "mcp_host": host,
        "mcp_host_is_local": host_is_local,
        "mcp_url": f"http://{host}:{port}/mcp",
        "mcp_venv_bin": venv_bin,
        "mcp_venv_bin_win": venv_bin_win,
        "mcp_cap_usd": cap.cap_usd,
        "mcp_spent_usd": cap.spent_usd,
        "mcp_jobs_today": cap.jobs_today,
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
            **_automation_ctx(request),
            **_key_ctx(request, balance=bal),
            **_maintenance_ctx(request),
            **updates_context(request),
            **backups_context(request),
        },
    )


@router.post("/settings")
def save_settings(request: Request, form: deps.Form):
    spec = general_spec()
    values = {k: str(v) for k, v in form.items() if k in spec}
    if str(form.get("_form", "")) == "general":
        # An unchecked checkbox posts nothing at all. The template already sends a
        # hidden "off" companion for each bool field, so a real browser submit never
        # hits this; it only matters for a partial post that omits the field entirely.
        for key, sp in spec.items():
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


@router.post("/settings/automation")
def save_automation(request: Request, form: deps.Form):
    """The three mcp.* values. An unchecked box posts nothing, which is "off"."""
    _local_only(request)
    values = {
        "mcp.enabled": "on" if "on" in form.getlist("mcp_enabled") else "off",
        "mcp.daily_cap_usd": str(form.get("mcp_daily_cap_usd", "")).strip(),
        "mcp.max_jobs_per_day": str(form.get("mcp_max_jobs_per_day", "")).strip(),
    }
    error = next((f"{k} cannot be negative" for k in MCP_NUMBERS if _negative(values[k])), None)
    if error is None:
        try:
            with db.session_scope(request.app.state.boot.session_factory) as s:
                settings_svc.set_many(s, values)
        except ValueError as e:
            error = str(e)  # "<key> must be a number" / "... an integer"
    if error is not None:
        key = error.split(" ", 1)[0]
        return deps.render(
            request,
            "settings/_automation.html",
            _automation_ctx(
                request,
                error=error.replace(key, MCP_LABELS.get(key, key), 1) + ".",
                error_field=MCP_FIELD_OF.get(key),
                typed={**values, "mcp.enabled": values["mcp.enabled"] == "on"},
            ),
            422,
        )
    return deps.render(request, "settings/_automation.html", _automation_ctx(request, saved=True))


@router.post("/settings/automation/reveal")
def reveal_mcp_token(request: Request):
    """Puts the real token back on the card. It is deliberately not part of the page
    itself, so seeing it always takes this one deliberate click. This is the only
    route in the app that reads a stored credential back out, so - like /api/restart -
    it answers nobody but this computer."""
    _local_only(request)
    token = secrets.effective_mcp_token(request.app.state.paths, request.app.state.env)
    if not token:
        return deps.render(
            request,
            "settings/_automation.html",
            _automation_ctx(request, error="No token yet. Click Regenerate to make one."),
            422,
        )
    return deps.render(request, "settings/_automation.html", _automation_ctx(request, token=token))


@router.post("/settings/automation/token")
def regenerate_mcp_token(request: Request):
    _local_only(request)
    if secrets.mcp_token_source(request.app.state.paths, request.app.state.env) == "env":
        return deps.render(
            request,
            "settings/_automation.html",
            _automation_ctx(
                request,
                error="VJHSTUDIO_MCP_TOKEN is set in the environment; the file is ignored.",
            ),
            422,
        )
    token = secrets.rotate_mcp_token(request.app.state.paths)
    return deps.render(
        request,
        "settings/_automation.html",
        _automation_ctx(
            request,
            token=token,
            message="New token. Give it to your agents; the old one stops working "
            "when VJHStudio restarts.",
        ),
    )


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
async def header_balance(request: Request):
    """The header chip polls this every 60 s and after a job finishes. The cache is
    only as fresh as the last refresh, so when it is older than a few seconds we ask
    RunWare again; if that fails the last known amount is shown, marked stale."""
    app = request.app
    with db.session_scope(app.state.boot.session_factory) as s:
        bal = account.cached_balance(s)
    stale = False
    key = app.state.api_key()
    if key and account.is_stale(bal):
        try:
            bal = await account.refresh_balance(
                app.state.client_factory,
                key,
                app.state.setting("runware.transport"),
                app.state.boot.session_factory,
            )
        except account.BalanceError:
            stale = bal is not None
    # ``polled`` drops the `load` trigger from the swapped-in chip: the response replaces
    # the element, and a `load` trigger on the replacement would fire again at once,
    # forever (~3 requests a second).
    return deps.render(
        request,
        "partials/_balance_chip.html",
        {"balance": bal, "stale": stale, "polled": True},
    )


def _trace_ctx(request: Request) -> dict:
    trace = getattr(request.app.state, "mcp_trace", None)
    return {
        "calls": trace.recent(20) if trace is not None else [],
        "mcp_on": request.app.state.mcp_server is not None,
    }


@router.get("/settings/automation/trace")
def mcp_trace(request: Request):
    """The last agent calls, exactly as the host sent them. Arguments can carry a
    prompt, so - like the token routes - this answers nobody but this computer."""
    _local_only(request)
    return deps.render(request, "settings/_mcp_trace.html", _trace_ctx(request))
