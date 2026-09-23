"""Turn a validated ImageRequest/VideoRequest into a queued Job row. No network, no runner."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..config import Paths
from ..models import CatalogModel, Job, JobStatus, Project
from ..schemas.image import ImageRequest
from ..schemas.video import VideoRequest
from . import catalog, constraints, costs, projects, prompts

TITLE_MAX = 80


def title_for(req: ImageRequest | VideoRequest) -> str:
    return (req.title or prompts.compose(req.form))[:TITLE_MAX].strip() or "Untitled"


def _require_kind(session: Session, air: str, kind: str) -> CatalogModel:
    m = catalog.get_by_air(session, air)
    if m is None or m.kind != kind:
        article = "an" if kind[0] in "aeiou" else "a"
        raise ValueError(f"{air} is not {article} {kind} model")
    return m


def _preflight_video(m: CatalogModel, req: VideoRequest) -> None:
    """The two inputs the form cannot invent. Both raise ``ValueError``, which the
    Generate route turns into a 422 re-render with the sentence shown inline -- the
    whole point is that the job is never queued and never billed."""
    if constraints.requires_input_video(m.constraints_json):
        raise ValueError("This model edits an existing video. VJHStudio cannot supply one yet.")
    if (
        constraints.needs_first_frame(m.capabilities_json or [], m.constraints_json)
        and req.first_frame_asset_id is None
    ):
        raise ValueError(NEEDS_FIRST_FRAME)


# The single-slot reference roles and the request field each one fills; ``reference`` is
# many-to-one and lives in ``reference_asset_ids``, so it is not in this map. (The same
# map, for the form side, is in web/routes/generate.py.)
ROLE_FIELDS = {
    "first": "first_frame_asset_id",
    "last": "last_frame_asset_id",
    "seed": "seed_image_asset_id",
}
NEEDS_FIRST_FRAME = "This model needs a first-frame image. Add one under References."


def _filled_roles(req: ImageRequest | VideoRequest) -> dict[str, list]:
    """The asset ids this request actually carries, per reference role."""
    filled = {
        role: [getattr(req, field)]
        for role, field in ROLE_FIELDS.items()
        if getattr(req, field, None) is not None
    }
    refs = list(getattr(req, "reference_asset_ids", None) or [])
    if refs:
        filled["reference"] = refs
    return filled


def _preflight_roles(m: CatalogModel, req: ImageRequest | VideoRequest, kind: str) -> None:
    """Refuse a reference the model does not take -- and ask for one it insists on --
    before a Job row exists. The Generate page hides the slots this model has no use
    for; this is the same rule enforced where it cannot be bypassed, because a posted
    input the provider rejects is a billed failure."""
    family = catalog.family(m) if kind == "image" else "video"
    roles = constraints.accepted_roles(kind, m.capabilities_json or [], m.constraints_json, family)
    filled = _filled_roles(req)
    for role in constraints.ROLE_ORDER:
        ids = filled.get(role) or []
        spec = roles.get(role)
        label = constraints.ROLE_LABELS[role]
        if spec is None:
            if ids:
                raise ValueError(
                    f"This model does not accept a {label}. Remove it under References."
                )
            continue
        if spec.get("required") and not ids:
            # the first-frame wording predates the other roles and is pinned by its own
            # test (and by the notice the References section shows)
            if role == "first":
                raise ValueError(NEEDS_FIRST_FRAME)
            raise ValueError(f"This model needs a {label}. Add one under References.")
        cap = spec.get("max")
        if role == "reference" and cap and len(ids) > cap:
            raise ValueError(f"This model takes at most {cap} reference images.")


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
        _preflight_roles(_require_kind(s, req.model, "image"), req, "image")
        project = _require_project(s, req.project_id)
        # the same helper the Save-prompt route uses, so a saved prompt and this submit
        # hash identically and dedupe onto one row
        _, _, negative = prompts.saved_texts(
            "image", req.form, req.final_prompt or "", default_negative
        )
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
        m = _require_kind(s, req.model, "video")
        _preflight_video(m, req)
        _preflight_roles(m, req, "video")
        project = _require_project(s, req.project_id)
        _, _, negative = prompts.saved_texts("video", req.form, req.final_prompt or "")
        prompt = prompts.for_request(
            s, req, kind="video", negative=negative, polish_json=polish_json
        )
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
