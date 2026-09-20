"""Turn a validated ImageRequest into a queued Job row. No network, no runner."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..config import Paths
from ..models import Job, JobStatus, Project
from ..schemas.image import ImageRequest
from . import catalog, costs, projects, prompts

TITLE_MAX = 80


def title_for(req: ImageRequest) -> str:
    return (req.title or prompts.compose(req.form))[:TITLE_MAX].strip() or "Untitled"


def _require_image_model(session: Session, air: str) -> None:
    m = catalog.get_by_air(session, air)
    if m is None or m.kind != "image":
        raise ValueError(f"{air} is not an image model")


def enqueue_image(
    session_factory: sessionmaker[Session],
    paths: Paths,
    req: ImageRequest,
    *,
    default_negative: str,
) -> Job:
    with db.session_scope(session_factory) as s:
        _require_image_model(s, req.model)
        project = s.get(Project, req.project_id)
        if project is None:
            raise ValueError(f"project {req.project_id} does not exist")
        negative = prompts.build_negative(req.form, default_negative, req.form.no_text)
        job = Job(
            id=str(uuid.uuid4()),
            project_id=project.id,
            prompt_id=req.prompt_id,
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
