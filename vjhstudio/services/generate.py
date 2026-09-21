"""Turn a validated ImageRequest/VideoRequest into a queued Job row. No network, no runner."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..config import Paths
from ..models import Job, JobStatus, Project
from ..schemas.image import ImageRequest
from ..schemas.video import VideoRequest
from . import catalog, costs, projects, prompts

TITLE_MAX = 80


def title_for(req: ImageRequest | VideoRequest) -> str:
    return (req.title or prompts.compose(req.form))[:TITLE_MAX].strip() or "Untitled"


def _require_kind(session: Session, air: str, kind: str) -> None:
    m = catalog.get_by_air(session, air)
    if m is None or m.kind != kind:
        article = "an" if kind[0] in "aeiou" else "a"
        raise ValueError(f"{air} is not {article} {kind} model")


def _require_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} does not exist")
    return project


def enqueue_image(
    session_factory: sessionmaker[Session],
    paths: Paths,
    req: ImageRequest,
    *,
    default_negative: str,
    polish_json: dict | None = None,
) -> Job:
    with db.session_scope(session_factory) as s:
        _require_kind(s, req.model, "image")
        project = _require_project(s, req.project_id)
        negative = prompts.build_negative(req.form, default_negative, req.form.no_text)
        # auto-history: every submit lands in the library, deduped by content hash, and
        # the job links the row that was actually used (a stale prompt_id makes a new one)
        prompt = prompts.for_request(
            s, req, kind="image", negative=negative, polish_json=polish_json
        )
        job = Job(
            id=str(uuid.uuid4()),
            project_id=project.id,
            prompt_id=prompt.id,
            kind="image",
            status=JobStatus.queued.value,
            model_air=req.model,
            request_json=req.model_dump() | {"negative": negative},
            title=title_for(req),
            expected_ms=costs.expected_ms(s, req.model),
            status_text="queued",
        )
        s.add(job)
        s.flush()
        projects.dir_for(paths, project.slug, projects.root_override(s)).mkdir(
            parents=True, exist_ok=True
        )
    return job  # detached but fully loaded: sessions are expire_on_commit=False


def enqueue_video(
    session_factory: sessionmaker[Session],
    paths: Paths,
    req: VideoRequest,
    *,
    polish_json: dict | None = None,
) -> Job:
    """Video models take no negative prompt, so ``request_json`` is the request alone --
    and the prompt row records an empty negative for the same reason, which is what its
    content hash is built from."""
    with db.session_scope(session_factory) as s:
        _require_kind(s, req.model, "video")
        project = _require_project(s, req.project_id)
        prompt = prompts.for_request(s, req, kind="video", negative="", polish_json=polish_json)
        job = Job(
            id=str(uuid.uuid4()),
            project_id=project.id,
            prompt_id=prompt.id,
            kind="video",
            status=JobStatus.queued.value,
            model_air=req.model,
            request_json=req.model_dump(),
            title=title_for(req),
            expected_ms=costs.expected_ms(s, req.model),
            status_text="queued",
        )
        s.add(job)
        s.flush()
        projects.dir_for(paths, project.slug, projects.root_override(s)).mkdir(
            parents=True, exist_ok=True
        )
    return job
