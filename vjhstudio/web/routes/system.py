from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from ... import db
from ...models import utcnow
from ...services import archive, gitinfo, update
from ...services import restart as restart_svc
from .. import deps

router = APIRouter()

RESTART_RETURN = "/settings#updates"

# A restore or a merge swaps the database file under this process, and a failed
# update puts its own pre-update copy back. Running the two at the same time loses
# one of them, so each refuses while the other is in flight.
_restore_lock = threading.Lock()

UPDATE_BUSY = "An update is running. Wait for it to finish."
RESTORE_BUSY = "A restore or merge is running. Wait for it to finish."


def _update_running() -> bool:
    return bool(update.STATE.snapshot()["running"])


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
    if _restore_lock.locked():
        ctx = {"update_state": update.STATE.snapshot(), "conflict": RESTORE_BUSY}
        return deps.render(request, "settings/_update_log.html", ctx, 409)
    started = update.start_update(request.app.state.paths)
    ctx = {
        "update_state": update.STATE.snapshot(),
        "conflict": "" if started else "An update is already running.",
    }
    return deps.render(request, "settings/_update_log.html", ctx, 200 if started else 409)


@router.post("/system/update/dismiss")
def update_dismiss(request: Request):
    """Forget a finished run's log so Settings stops showing it. Refused (409, with
    the live log) while a run is in flight."""
    _local_only(request)
    cleared = update.STATE.reset()
    return deps.render(
        request, "settings/_updates.html", updates_context(request), 200 if cleared else 409
    )


@router.get("/hx/system/update-log")
def update_log(request: Request):
    snap = update.STATE.snapshot()
    response = deps.render(request, "settings/_update_log.html", {"update_state": snap})
    # A GET that restarts the app must not be reachable from an <img src> on a
    # hostile page: only htmx's own poll (HX-Request) may finish the run. The CSRF
    # middleware cannot help here, it guards the unsafe methods only.
    if deps.is_hx(request) and not snap["running"] and snap["ok"]:
        # The worker normally claims the restart the moment the run ends; this is
        # the fallback for a run whose worker did not (or could not) get there.
        # UpdateState.claim_restart is atomic, so only one of them ever re-execs.
        if update.STATE.claim_restart():
            restart_svc.request_restart()
        response.headers["HX-Redirect"] = "/restarting"
    return response


def _safe_return(raw: str) -> str:
    """A path on this app, or the default. Anything that could send the browser to
    another origin — a scheme, "//host", or the backslash browsers normalise to "/" —
    is dropped rather than repaired."""
    wanted = "".join(c for c in (raw or "") if c.isprintable()).strip()
    if not wanted.startswith("/") or wanted[1:2] in ("/", "\\"):
        return RESTART_RETURN
    return wanted


@router.get("/restarting")
def restarting(request: Request):
    """Standalone waiting page: the browser watches /api/health for a new boot_id."""
    wanted = _safe_return(request.query_params.get("return") or "")
    return deps.render(
        request,
        "pages/restarting.html",
        {
            "boot_id": request.app.state.boot.boot_id,
            "port": request.app.state.port,
            "return_to": wanted,
        },
    )


# --- backups ---------------------------------------------------------------

BACKUPS_RETURN = "/restarting?return=/settings%23backups"

# Only the tables worth a one-line summary in the archive table, in reading order.
_SUMMARY_TABLES = ("projects", "prompts", "jobs", "outputs", "assets")

_TABLE_LABELS = {
    "projects": ("project", "projects"),
    "catalog_models": ("model", "models"),
    "assets": ("asset", "assets"),
    "prompts": ("prompt", "prompts"),
    "jobs": ("job", "jobs"),
    "outputs": ("output", "outputs"),
    "usage_entries": ("usage row", "usage rows"),
}


def _label(table: str, n: int) -> str:
    names = _TABLE_LABELS.get(table, (table, table))
    return f"{n} {names[0 if n == 1 else 1]}"


def _counts_summary(counts: dict[str, int]) -> str:
    parts = [_label(t, counts[t]) for t in _SUMMARY_TABLES if int(counts.get(t) or 0)]
    return " · ".join(parts) if parts else "empty"


def _archive_row(info: archive.ArchiveInfo) -> dict:
    """One table row. The template gets strings only: formatting a size or a date in
    Jinja would need a filter this app does not have."""
    return {
        "name": info.name,
        "created": info.created_at.strftime("%Y-%m-%d %H:%M"),
        "size": archive.human_size(info.size_bytes),
        "includes": ", ".join(info.includes),
        "counts": _counts_summary(info.counts),
        "app_version": info.app_version,
    }


def _backups_ctx(request: Request, message: str = "", error: str = "") -> dict:
    """Keys are prefixed so the Settings page can render this section beside the
    Updates and Maintenance forms without their `message`/`error` colliding."""
    paths = request.app.state.paths
    return {
        "paths": paths,
        "backups": [_archive_row(a) for a in archive.list_archives(paths)],
        "backups_message": message,
        "backups_error": error,
    }


def backups_context(request: Request) -> dict:
    """Public so the Settings page can render the section inline, like the Updates one."""
    return _backups_ctx(request)


def _panel(request: Request, message: str = "", error: str = "", status: int = 200):
    return deps.render(
        request, "settings/_backups.html", _backups_ctx(request, message, error), status
    )


def _archive_or_404(request: Request, name: str) -> Path:
    try:
        path = archive.archive_path(request.app.state.paths, name)
    except archive.ArchiveError:
        raise HTTPException(404, "no such backup") from None
    if not path.is_file():
        raise HTTPException(404, "no such backup")
    return path


@router.get("/hx/system/backups")
def backups_panel(request: Request):
    return _panel(request)


@router.post("/system/backup")
def backup_create(request: Request, form: deps.Form):
    _local_only(request)
    try:
        dest = archive.create_archive(
            request.app.state.boot.session_factory,
            request.app.state.paths,
            uploads=str(form.get("uploads", "")) == "on",
            outputs=str(form.get("outputs", "")) == "on",
        )
    except (archive.ArchiveError, OSError) as e:
        return _panel(request, error=f"The backup could not be written: {e}", status=422)
    return _panel(request, message=f"Backup created: {dest.name}")


@router.get("/system/backups/{name}/download")
def backup_download(request: Request, name: str):
    _local_only(request)
    path = _archive_or_404(request, name)
    return FileResponse(path, media_type="application/zip", filename=path.name)


def _upload_size(request: Request, upload: UploadFile) -> int | None:
    """The upload's size, or the whole body's as an upper bound when the part carries
    no length of its own. None when neither is known."""
    if upload.size is not None:
        return upload.size
    raw = request.headers.get("content-length")
    return int(raw) if raw and raw.isdigit() else None


@router.post("/system/backups/import")
async def backup_import(request: Request):
    """Multipart, so this one handler is async: the body is parsed in the event loop
    and the (possibly multi-gigabyte) upload is streamed to disk in a worker thread -
    it is never read into memory in one piece."""
    _local_only(request)
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        return _panel(request, error="Choose a backup file to import.", status=422)
    limit = archive.MAX_RESTORE_BYTES
    size = _upload_size(request, upload)
    if size is not None and size > limit:
        return _panel(
            request,
            error=f"That file is {archive.human_size(size)}; the limit is "
            f"{archive.human_size(limit)}. Copy it into the backups folder instead.",
            status=422,
        )
    # Starlette's parser rewinds each part, but the import reads from the start either way.
    await upload.seek(0)
    try:
        dest = await asyncio.to_thread(
            archive.import_archive_file,
            request.app.state.paths,
            upload.filename or "",
            upload.file,
        )
    except (archive.ArchiveError, OSError) as e:
        return _panel(request, error=str(e), status=422)
    return _panel(request, message=f"Backup imported: {dest.name}")


@router.delete("/system/backups/{name}")
def backup_delete(request: Request, name: str):
    _local_only(request)
    try:
        removed = archive.delete_archive(request.app.state.paths, name)
    except archive.ArchiveError as e:
        return _panel(request, error=str(e), status=422)
    if not removed:
        return _panel(request, error=f"That backup is already gone: {name}", status=422)
    return _panel(request, message=f"Backup deleted: {name}")


def _active_jobs(request: Request) -> int:
    """No `getattr` default: quietly reporting "nothing running" when the runner is
    missing would let a restore replace the database under a live job."""
    return len(request.app.state.runner.active_ids())


@router.post("/system/backups/{name}/restore")
def backup_restore(request: Request, name: str):
    _local_only(request)
    if _update_running():
        return _panel(request, error=UPDATE_BUSY, status=409)
    if not _restore_lock.acquire(blocking=False):
        return _panel(request, error=RESTORE_BUSY, status=409)
    try:
        paths = request.app.state.paths
        try:
            result = archive.restore_replace(
                paths,
                archive.archive_path(paths, name),
                engine=request.app.state.boot.engine,
                jobs_running=_active_jobs(request),
            )
        except (archive.ArchiveError, OSError) as e:
            return _panel(request, error=str(e), status=422)
        # Exactly once: the whole database underneath this process was just swapped.
        restart_svc.request_restart()
        response = deps.render(
            request,
            "settings/_restore_done.html",
            {
                "name": name,
                "counts": _counts_summary(result.counts),
                "skipped": len(result.skipped),
                "outputs_root": result.outputs_root,
                "safety_backup": result.safety_backup.name if result.safety_backup else "",
                "missing": result.missing_outputs + result.missing_assets,
            },
        )
        response.headers["HX-Redirect"] = BACKUPS_RETURN
        return response
    finally:
        _restore_lock.release()


def _merge_rows(report: archive.MergeReport) -> list[dict]:
    return [
        {
            "table": t,
            "label": _TABLE_LABELS[t][1],
            "new": report.counts[t].new,
            "existing": report.counts[t].existing,
            "missing_files": report.counts[t].missing_files,
        }
        for t in archive.MERGE_TABLES
    ]


@router.post("/system/backups/{name}/preview-merge")
def backup_preview_merge(request: Request, name: str):
    _local_only(request)
    if _update_running():
        return _panel(request, error=UPDATE_BUSY, status=409)
    paths = request.app.state.paths
    try:
        report = archive.preview_merge(
            request.app.state.boot.session_factory, paths, archive.archive_path(paths, name)
        )
    except (archive.ArchiveError, OSError) as e:
        return _panel(request, error=str(e), status=422)
    return deps.render(
        request,
        "settings/_merge_preview.html",
        {
            "name": name,
            "rows": _merge_rows(report),
            "errors": report.errors,
            "total_new": report.total_new,
            "total_missing_files": report.total_missing_files,
            "created_at": report.created_at[:19].replace("T", " "),
            "archive_version": report.app_version,
        },
    )


@router.post("/system/backups/{name}/merge")
def backup_merge(request: Request, name: str):
    _local_only(request)
    if _update_running():
        return _panel(request, error=UPDATE_BUSY, status=409)
    if not _restore_lock.acquire(blocking=False):
        return _panel(request, error=RESTORE_BUSY, status=409)
    try:
        paths = request.app.state.paths
        try:
            report = archive.merge(
                request.app.state.boot.session_factory, paths, archive.archive_path(paths, name)
            )
        except archive.MergeError as e:
            safety = ""
            if e.safety_backup is not None:
                safety = f" A safety backup was taken first: {e.safety_backup.name}."
            return _panel(request, error=f"{e}{safety}", status=422)
        except (archive.ArchiveError, OSError) as e:
            return _panel(request, error=str(e), status=422)
        message = archive.merge_summary(report)
        if report.safety_backup is not None:
            message += f" (safety backup: {report.safety_backup.name})"
        if report.errors:
            message += f" {len(report.errors)} note(s): " + "; ".join(report.errors[:3])
        response = _panel(request, message=message)
        # New projects, jobs and outputs: every counter in the chrome is now stale.
        response.headers["HX-Trigger"] = "jobs-changed"
        return response
    finally:
        _restore_lock.release()
