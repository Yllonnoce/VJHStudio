"""Queue panel, job cards and the JSON job views.

The DB row is the record; the runner's in-memory snapshot is an overlay on top of it
(progress is written to the database at most every two seconds, so a card built from
the row alone would visibly lag). Everything the panel and the API need is assembled
by :func:`job_view`, which both this module and the generate routes render from.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ... import db
from ...models import CatalogModel, Job, JobStatus, Output, Project, utcnow
from ...schemas.image import ImageRequest
from ...services import costs, generate, meta
from ...services import jobs as jobs_svc
from ...services import settings as settings_svc
from .. import deps
from ..urls import output_url, thumb_url

router = APIRouter()

ACTIVE = (JobStatus.queued.value, JobStatus.running.value)
FINISHED = (JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.cancelled.value)
FINISHED_SHOWN = 10


def _ms(start, end) -> int:
    if start is None:
        return 0
    return max(0, int(((end or utcnow()) - start).total_seconds() * 1000))


def _output_views(session: Session, job_ids: list[str], slugs: dict[int, str]) -> dict[str, list]:
    out: dict[str, list] = {}
    if not job_ids:
        return out
    rows = session.execute(
        select(Output).where(Output.job_id.in_(job_ids)).order_by(Output.id)
    ).scalars()
    for o in rows:
        slug = slugs.get(o.project_id, "")
        out.setdefault(o.job_id, []).append(
            {
                "id": o.id,
                "url": output_url(slug, o.filename),
                "thumb_url": thumb_url(o.thumb_rel_path),
                "seed": o.seed,
                "width": o.width,
                "height": o.height,
                "is_missing": o.is_missing,
            }
        )
    return out


def _slugs(session: Session) -> dict[int, str]:
    return dict(session.execute(select(Project.id, Project.slug)).all())


def job_view(job: Job, snap: dict, outputs: list, model_name: str) -> dict:
    """One job, merged with the runner's live snapshot. Finished jobs never estimate."""
    live = snap.get(job.id) or {}
    expected_ms = int(job.expected_ms or costs.DEFAULT_EXPECTED_MS)
    elapsed_ms = _ms(job.started_at, job.finished_at)
    if job.status == JobStatus.succeeded.value:
        progress, estimated = 100, False
    elif job.status in FINISHED:
        progress, estimated = 0, False
    elif live:
        # snapshot() already returns max(reported, estimate); it lags the row only upwards
        progress, estimated = int(live.get("progress") or 0), not live.get("real", False)
    elif job.status == JobStatus.running.value:
        progress, estimated = jobs_svc.estimate_progress(elapsed_ms, expected_ms), True
    else:
        progress, estimated = 0, False
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "title": job.title or "Untitled",
        "model_air": job.model_air,
        "model_name": model_name or job.model_air,
        "progress": progress,
        "estimated": estimated,
        "stage": live.get("stage") or job.status_text or job.status,
        "expected_ms": expected_ms,
        "elapsed_ms": elapsed_ms,
        "eta_ms": max(0, expected_ms - elapsed_ms) if estimated else 0,
        "cost": job.cost,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "dropped_params": list(job.dropped_params_json or []),
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "outputs": outputs,
    }


def _views(request: Request, session: Session, jobs: list[Job]) -> list[dict]:
    runner = getattr(request.app.state, "runner", None)
    snap = runner.snapshot() if runner is not None else {}
    slugs = _slugs(session)
    outs = _output_views(session, [j.id for j in jobs], slugs)
    names = {
        m.air: m.name for m in session.execute(select(CatalogModel)).scalars()
    }  # one read: the catalog is small and every card wants a display name
    return [job_view(j, snap, outs.get(j.id, []), names.get(j.model_air, "")) for j in jobs]


def _select(session: Session, statuses, order, limit: int | None = None) -> list[Job]:
    q = select(Job).where(Job.status.in_(statuses)).order_by(*order)
    if limit:
        q = q.limit(limit)
    return list(session.execute(q).scalars())


def panel_ctx(request: Request, oob_badge: bool = False) -> dict:
    """Active jobs oldest-first, then the most recent finished ones."""
    with db.session_scope(request.app.state.boot.session_factory) as s:
        active = _select(s, ACTIVE, (Job.created_at.asc(), Job.id.asc()))
        done = _select(s, FINISHED, (Job.finished_at.desc(), Job.created_at.desc()), FINISHED_SHOWN)
        # "Clear finished" hides everything that had finished by the time it was clicked;
        # jobs finishing later show up again. The Queue page's history keeps them all.
        cleared = meta.get(s, "queue.cleared_at")
        if cleared:
            cutoff = datetime.fromisoformat(cleared)
            done = [j for j in done if j.finished_at and j.finished_at > cutoff]
        return {
            "active_jobs": len(active),
            "jobs_active": _views(request, s, active),
            "jobs_done": _views(request, s, done),
            "today_spend": costs.today_spend(s),
            "oob": oob_badge,
        }


def _at(request: Request) -> str:
    """The page a poll was issued from (`?at=/queue`), so the header chip can keep its
    aria-current after the badge or the panel re-renders. Only a plain path counts."""
    at = str(request.query_params.get("at") or "")
    if at.startswith("/") and not at.startswith("//") and "\\" not in at:
        return at
    return request.url.path


def _panel(request: Request, headers: dict | None = None, oob_badge: bool = True):
    ctx = panel_ctx(request, oob_badge)
    ctx["current_path"] = _at(request)
    r = deps.render(request, "generate/_queue_panel.html", ctx)
    for k, v in (headers or {}).items():
        r.headers[k] = v
    return r


def _claim_finished(request: Request) -> list[dict]:
    """Finished-since-last-poll notifications, claimed so they fire exactly once.

    The stamp is a *single* conditional UPDATE inside one transaction and only the rows
    this call flipped come back, so the queue-panel poller and the header badge poller
    racing each other cannot both announce the same job.
    """
    with db.session_scope(request.app.state.boot.session_factory) as s:
        claimed = list(
            s.execute(
                update(Job)
                .where(Job.seen_at.is_(None), Job.status.in_(FINISHED))
                .values(seen_at=utcnow())
                .returning(Job.id),
                execution_options={"synchronize_session": False},
            ).scalars()
        )
        if not claimed:
            return []
        fresh = list(
            s.execute(
                select(Job).where(Job.id.in_(claimed)).order_by(Job.finished_at.asc())
            ).scalars()
        )
        outs = _output_views(s, claimed, _slugs(s))
        events = []
        for j in fresh:
            thumbs = [o["thumb_url"] for o in outs.get(j.id, []) if o["thumb_url"]]
            events.append(
                {
                    "id": j.id,
                    "status": j.status,
                    "title": j.title or "Untitled",
                    "thumb": thumbs[0] if thumbs else None,
                }
            )
        return events


def _finished_trigger(events: list[dict]) -> dict:
    return {"HX-Trigger": json.dumps({"job-finished": events})} if events else {}


HISTORY_SHOWN = 50


@router.get("/queue")
def queue_page(request: Request):
    """The Queue page: the live panel plus a table of the last finished jobs. The header
    chip links here, so "Queue" never drops the user into the creation form."""
    ctx = panel_ctx(request)
    with db.session_scope(request.app.state.boot.session_factory) as s:
        done = _select(s, FINISHED, (Job.finished_at.desc(), Job.created_at.desc()), HISTORY_SHOWN)
        ctx["history"] = _views(request, s, done)
    return deps.render(request, "pages/queue.html", ctx)


@router.get("/hx/jobs/active")
def hx_active(request: Request):
    return _panel(request, _finished_trigger(_claim_finished(request)))


@router.get("/hx/jobs/badge")
def hx_badge(request: Request):
    """The header polls this on *every* page, so this - not the Generate-only queue
    panel - is what makes completion toasts and the unseen count work app-wide."""
    events = _claim_finished(request)
    # the claim already cleared seen_at, so show what this very response claimed
    ctx = {"oob": False, "unseen_jobs": len(events)} if events else {"oob": False}
    ctx["current_path"] = _at(request)
    r = deps.render(request, "partials/_jobs_badge.html", ctx)
    for k, v in _finished_trigger(events).items():
        r.headers[k] = v
    return r


@router.get("/hx/jobs/{job_id}")
def hx_job(request: Request, job_id: str):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return deps.render(request, "generate/_job_card.html", {"j": _views(request, s, [job])[0]})


@router.post("/jobs/seen")
def mark_seen(request: Request):
    _claim_finished(request)
    with db.session_scope(request.app.state.boot.session_factory) as s:
        meta.set(s, "queue.cleared_at", utcnow().isoformat())
    return _panel(request)


@router.post("/jobs/{job_id}/cancel")
def cancel(request: Request, job_id: str):
    runner = getattr(request.app.state, "runner", None)
    if runner is None or not runner.cancel(job_id):
        return JSONResponse({"error": "unknown or finished job"}, status_code=404)
    with db.session_scope(request.app.state.boot.session_factory) as s:
        job = s.get(Job, job_id)
        if job is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        ctx = {"j": _views(request, s, [job])[0]}
    r = deps.render(request, "generate/_job_card.html", ctx)
    r.headers["HX-Trigger"] = "jobs-changed"
    return r


@router.post("/jobs/{job_id}/retry")
def retry(request: Request, job_id: str):
    """A retry is a *new* job built from the stored request: the failed row stays as history."""
    with db.session_scope(request.app.state.boot.session_factory) as s:
        job = s.get(Job, job_id)
        if job is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        data = dict(job.request_json or {})
        data.pop("negative", None)
        default_negative = settings_svc.get(s, "defaults.negative_prompt", request.app.state.env)
    try:
        req = ImageRequest(**data)
        new = generate.enqueue_image(
            request.app.state.boot.session_factory,
            request.app.state.paths,
            req,
            default_negative=default_negative,
        )
    except (ValueError, TypeError) as e:
        # the button targets #queue-panel: a JSON body here would replace the whole queue
        ctx = panel_ctx(request)
        ctx["error"] = str(e)
        return deps.render(request, "generate/_queue_panel.html", ctx, 422)
    runner = getattr(request.app.state, "runner", None)
    if runner is not None:
        runner.submit(new.id)
    return _panel(request, {"HX-Trigger": "jobs-changed"})


@router.get("/api/jobs")
def api_jobs(request: Request, status: str = "", limit: int = 50):
    statuses = tuple(s for s in status.split(",") if s) or ACTIVE + FINISHED
    with db.session_scope(request.app.state.boot.session_factory) as s:
        jobs = _select(
            s, statuses, (Job.created_at.desc(), Job.id.desc()), max(1, min(int(limit), 200))
        )
        return JSONResponse(_json_rows(_views(request, s, jobs)))


@router.get("/api/jobs/{job_id}")
def api_job(request: Request, job_id: str):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        job = s.get(Job, job_id)
        if job is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        return JSONResponse(_json_rows(_views(request, s, [job]))[0])


def _json_rows(views: list[dict]) -> list[dict]:
    rows = []
    for v in views:
        row = dict(v)
        for k in ("created_at", "started_at", "finished_at"):
            row[k] = row[k].isoformat() if row[k] else None
        rows.append(row)
    return rows
