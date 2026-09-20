"""Outputs: record what was saved, browse the gallery, favourite, delete, remix."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, object_session

from ..config import Paths
from ..models import Job, Output, utcnow
from ..runware.download import SavedFile, make_thumbnail, write_sidecar
from . import projects, prompts

log = logging.getLogger(__name__)
PER_PAGE = 48


def abs_path(paths: Paths, output: Output, outputs_dir: str = "") -> Path:
    return projects.outputs_root(paths, outputs_dir) / output.rel_path


def _dims(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.width, im.height
    except Exception:  # noqa: BLE001 - a non-image (or corrupt) file just has no dimensions
        return None, None


def record_outputs(
    session: Session,
    paths: Paths,
    job: Job,
    saved: list[SavedFile],
    params: dict,
    sidecar_extra: dict | None = None,
) -> list[Output]:
    root = projects.root_for(session, paths)
    request = dict(job.request_json or {})
    negative = str(request.get("negative") or "")
    prompt_text = str(
        (job.task_json or {}).get("positivePrompt") or prompts.compose(request.get("form") or {})
    )
    rows: list[Output] = []
    for f in saved:
        item = f.item
        sidecar = {
            "job_id": job.id,
            "model": job.model_air,
            "kind": job.kind,
            "prompt": prompt_text,
            "negative": negative,
            "seed": item.seed,
            "cost": item.cost,
            "source_url": f.url,
            "created_at": utcnow().isoformat(),
            "params": params,
            **(sidecar_extra or {}),
        }
        side = write_sidecar(f.path, sidecar)
        w, h = _dims(f.path)
        row = Output(
            job_id=job.id,
            project_id=job.project_id,
            kind=job.kind,
            filename=f.path.name,
            rel_path=f.path.relative_to(root).as_posix(),
            sidecar_rel_path=side.relative_to(root).as_posix(),
            model_air=job.model_air,
            prompt_text=prompt_text,
            negative_prompt=negative,
            params_json=params,
            seed=item.seed,
            width=w,
            height=h,
            cost=item.cost,
            source_url=f.url,
            file_size=f.size,
        )
        session.add(row)
        session.flush()  # the thumbnail is named after the row id
        thumb = paths.thumbs / f"{row.id}.jpg"
        if make_thumbnail(f.path, thumb) is not None:
            row.thumb_rel_path = thumb.relative_to(paths.data).as_posix()
        rows.append(row)
    session.flush()
    return rows


def _as_datetime(value: str | date | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    return datetime.fromisoformat(str(value))


def gallery(
    session: Session,
    *,
    project_id: int | None = None,
    kind: str | None = None,
    model: str | None = None,
    favourite: bool | None = None,
    q: str | None = None,
    date_from: str | date | None = None,
    date_to: str | date | None = None,
    page: int = 1,
    per_page: int = PER_PAGE,
) -> tuple[list[Output], int]:
    filters = []
    if project_id is not None:
        filters.append(Output.project_id == project_id)
    if kind:
        filters.append(Output.kind == kind)
    if model:
        filters.append(Output.model_air == model)
    if favourite is not None:
        filters.append(Output.is_favourite.is_(bool(favourite)))
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(Output.prompt_text.like(like), Output.filename.like(like)))
    start = _as_datetime(date_from)
    if start is not None:
        filters.append(Output.created_at >= start)
    end = _as_datetime(date_to)
    if end is not None:
        filters.append(Output.created_at < end + timedelta(days=1))
    total = int(session.execute(select(func.count(Output.id)).where(*filters)).scalar() or 0)
    page = max(1, int(page))
    per_page = max(1, int(per_page))
    rows = session.execute(
        select(Output)
        .where(*filters)
        .order_by(Output.created_at.desc(), Output.id.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    ).scalars()
    return list(rows), total


def get(session: Session, output_id: int) -> Output | None:
    return session.get(Output, output_id)


def toggle_favourite(session: Session, output_id: int) -> Output:
    o = session.get(Output, output_id)
    if o is None:
        raise LookupError(output_id)
    o.is_favourite = not o.is_favourite
    session.flush()
    return o


def delete(session: Session, paths: Paths, output_id: int) -> bool:
    o = session.get(Output, output_id)
    if o is None:
        return False
    root = projects.root_for(session, paths)
    for p in (
        root / o.rel_path,
        root / o.sidecar_rel_path,
        (paths.data / o.thumb_rel_path) if o.thumb_rel_path else None,
    ):
        if p is None:
            continue
        try:
            p.unlink(missing_ok=True)
        except OSError as e:  # noqa: PERF203 - a locked file must not block the row deletion
            log.warning("could not delete %s: %s", p, e)
    session.delete(o)
    session.flush()
    return True


def remix_request(output: Output) -> dict:
    """The job's original request with this output's seed pinned, ready to re-submit."""
    session = object_session(output)
    job = session.get(Job, output.job_id) if session is not None else None
    data = dict((job.request_json if job else None) or {})
    data["seed"] = output.seed
    return data


def mark_missing(session: Session, paths: Paths) -> int:
    root = projects.root_for(session, paths)
    n = 0
    for o in session.execute(select(Output).where(Output.is_missing.is_(False))).scalars():
        if not (root / o.rel_path).exists():
            o.is_missing = True
            n += 1
    session.flush()
    return n
