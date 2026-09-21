from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request

from ... import db
from ...models import utcnow
from ...services import gitinfo, update
from ...services import restart as restart_svc
from .. import deps

router = APIRouter()

RESTART_RETURN = "/settings#updates"


@router.get("/api/health")
async def health(request: Request):
    b = request.app.state.boot
    return {
        "ok": True,
        "app": "VJHStudio",
        "version": b.version,
        "commit": b.commit.sha if b.commit else "",
        "schema": b.schema_revision,
        "boot_id": b.boot_id,
        "started_at": b.started_at.isoformat(),
        "pid": os.getpid(),
        "port": request.app.state.port,
    }


def _local_only(request: Request) -> None:
    """Refuse a non-loopback peer. Cross-site browser requests, which *are*
    loopback, are handled earlier by web.csrf.CrossSiteBlockMiddleware."""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "local only")


@router.post("/api/restart", status_code=202)
async def api_restart(request: Request):
    _local_only(request)
    return {"restarting": True, "strategy": restart_svc.request_restart()}


@router.post("/api/shutdown", status_code=202)
async def api_shutdown(request: Request):
    _local_only(request)
    restart_svc.request_shutdown()
    return {"stopping": True}


# --- updates ---------------------------------------------------------------


def _stamp(iso: str) -> str:
    """ISO timestamp -> "YYYY-MM-DD HH:MM:SS" for the "last checked" line."""
    return iso[:19].replace("T", " ")


def _updates_ctx(
    *,
    behind: int,
    commits: list[str],
    error: str,
    checked_at: str,
    current: str = "",
) -> dict:
    return {
        "behind": behind,
        "commits": commits,
        "update_error": error,
        "checked_at": checked_at,
        "current": current,
        "is_git": gitinfo.is_git_install(),
        "update_state": update.STATE.snapshot(),
    }


def updates_context(request: Request) -> dict:
    """The panel as the last check left it, straight out of app_meta. Public so the
    Settings page can render the section inline instead of fetching it on load."""
    with db.session_scope(request.app.state.boot.session_factory) as s:
        notice = update.read_notice(s)
    return _updates_ctx(
        behind=notice.behind,
        commits=notice.commits,
        error=notice.error,
        checked_at=_stamp(notice.checked_at),
    )


@router.get("/hx/system/updates")
def updates_panel(request: Request):
    return deps.render(request, "settings/_updates.html", updates_context(request))


@router.post("/system/update/check")
async def update_check(request: Request):
    """The one async handler here: the check runs `git fetch`, which blocks for as
    long as the network wants to, so it goes to a worker thread."""
    _local_only(request)
    info = await asyncio.to_thread(update.check_and_store, request.app.state.boot.session_factory)
    ctx = _updates_ctx(
        behind=int(info.get("behind") or 0),
        commits=list(info.get("commits") or []),
        error=str(info.get("error") or ""),
        checked_at=_stamp(utcnow().isoformat()),
        current=str(info.get("current") or ""),
    )
    return deps.render(request, "settings/_updates.html", ctx)


@router.post("/system/update")
def update_now(request: Request):
    _local_only(request)
    started = update.start_update(request.app.state.paths)
    ctx = {"update_state": update.STATE.snapshot(), "conflict": not started}
    return deps.render(request, "settings/_update_log.html", ctx, 200 if started else 409)


def _restart_once(request: Request) -> None:
    """Restart at most once per finished run: the log goes on polling until the
    browser follows HX-Redirect, and a second poll must not re-exec the app."""
    state = request.app.state
    token = (id(update.STATE), getattr(update.STATE, "started_at", None))
    if getattr(state, "update_restart_token", None) == token:
        return
    state.update_restart_token = token
    restart_svc.request_restart()


@router.get("/hx/system/update-log")
def update_log(request: Request):
    snap = update.STATE.snapshot()
    response = deps.render(request, "settings/_update_log.html", {"update_state": snap})
    if not snap["running"] and snap["ok"]:
        _restart_once(request)
        response.headers["HX-Redirect"] = "/restarting"
    return response


@router.get("/restarting")
def restarting(request: Request):
    """Standalone waiting page: the browser watches /api/health for a new boot_id."""
    wanted = request.query_params.get("return") or RESTART_RETURN
    # Only a path on this app: a full URL here would be an open redirect.
    if not wanted.startswith("/") or wanted.startswith("//"):
        wanted = RESTART_RETURN
    return deps.render(
        request,
        "pages/restarting.html",
        {
            "boot_id": request.app.state.boot.boot_id,
            "port": request.app.state.port,
            "return_to": wanted,
        },
    )
