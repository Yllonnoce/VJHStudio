"""Outputs: record what was saved, browse the gallery, favourite, delete, remix."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, object_session

from .. import __version__
from ..config import Paths
from ..models import Job, Output, Project, utcnow
from ..runware.download import SavedFile, make_thumbnail, write_sidecar
from . import projects, prompts

log = logging.getLogger(__name__)
PER_PAGE = 48


def contained(root: Path, rel_path: str | None) -> Path | None:
    """Resolve ``rel_path`` under ``root``; None when it escapes (``..``, absolute, symlink)."""
    if not rel_path:
        return None
    base = root.resolve()
    target = Path(base / rel_path).resolve()
    return target if target.is_relative_to(base) else None


def abs_path(paths: Paths, output: Output, outputs_dir: str = "") -> Path:
    """The file for an output row. Raises LookupError when the row points outside the root,
    so a file route can 404 instead of serving whatever the path escaped to."""
    path = contained(projects.outputs_root(paths, outputs_dir), output.rel_path)
    if path is None:
        raise LookupError(output.rel_path)
    return path


def _dims(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.width, im.height
    except Exception:  # noqa: BLE001 - a non-image (or corrupt) file just has no dimensions
        return None, None


@dataclass(frozen=True)
class OutputMeta:
    """Everything the file work needs, read out of the ORM before any of it starts."""

    job_id: str
    project_id: int
    project_slug: str
    kind: str
    model_air: str
    prompt_text: str
    negative_prompt: str


@dataclass(frozen=True)
class Prepared:
    saved: SavedFile
    rel_path: str
    sidecar_rel_path: str
    thumb_rel_path: str | None
    width: int | None
    height: int | None
    params: dict


def meta_for(session: Session, job: Job) -> OutputMeta:
    request = dict(job.request_json or {})
    project = session.get(Project, job.project_id)
    return OutputMeta(
        job_id=job.id,
        project_id=job.project_id,
        project_slug=project.slug if project else "",
        kind=job.kind,
        model_air=job.model_air,
        prompt_text=str(
            (job.task_json or {}).get("positivePrompt")
            or prompts.compose(request.get("form") or {})
        ),
        negative_prompt=str(request.get("negative") or ""),
    )


def prepare(
    paths: Paths,
    meta: OutputMeta,
    saved: list[SavedFile],
    params: dict,
    sidecar_extra: dict | None = None,
    *,
    root: Path,
) -> list[Prepared]:
    """Sidecars, dimensions and thumbnails. Deliberately does no DB work: JPEG encoding
    must not happen inside a write transaction."""
    out: list[Prepared] = []
    for f in saved:
        item = f.item
        sidecar = {
            "app_version": __version__,
            "job_id": meta.job_id,
            "project": meta.project_slug,
            "model": meta.model_air,
            "kind": meta.kind,
            "prompt": meta.prompt_text,
            "negative_prompt": meta.negative_prompt,
            "seed": item.seed,
            "cost": item.cost,
            "source_url": f.url,
            "created_at": utcnow().isoformat(),
            "params": params,
            **(sidecar_extra or {}),
        }
        side = write_sidecar(f.path, sidecar)
        w, h = _dims(f.path)
        thumb = paths.thumbs / f"{f.path.stem}.jpg"
        made = make_thumbnail(f.path, thumb)
        out.append(
            Prepared(
                saved=f,
                rel_path=f.path.relative_to(root).as_posix(),
                sidecar_rel_path=side.relative_to(root).as_posix(),
                thumb_rel_path=thumb.relative_to(paths.data).as_posix() if made else None,
                width=w,
                height=h,
                params=params,
            )
        )
    return out


def insert(session: Session, meta: OutputMeta, prepared: list[Prepared]) -> list[Output]:
    """One short transaction: the files are already on disk."""
    rows = [
        Output(
            job_id=meta.job_id,
            project_id=meta.project_id,
            kind=meta.kind,
            filename=p.saved.path.name,
            rel_path=p.rel_path,
            sidecar_rel_path=p.sidecar_rel_path,
            thumb_rel_path=p.thumb_rel_path,
            model_air=meta.model_air,
            prompt_text=meta.prompt_text,
            negative_prompt=meta.negative_prompt,
            params_json=p.params,
            seed=p.saved.item.seed,
            width=p.width,
            height=p.height,
            cost=p.saved.item.cost,
            source_url=p.saved.url,
            file_size=p.saved.size,
        )
        for p in prepared
    ]
    session.add_all(rows)
    session.flush()
    return rows


def record_outputs(
    session: Session,
    paths: Paths,
    job: Job,
    saved: list[SavedFile],
    params: dict,
    sidecar_extra: dict | None = None,
    *,
    root: Path | None = None,
) -> list[Output]:
    """Convenience wrapper. The runner calls prepare() and insert() separately so the file
    work stays outside the transaction."""
    meta = meta_for(session, job)
    root = root or projects.root_for(session, paths)
    return insert(session, meta, prepare(paths, meta, saved, params, sidecar_extra, root=root))


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
    targets = (
        contained(root, o.rel_path),
        contained(root, o.sidecar_rel_path),
        contained(paths.data, o.thumb_rel_path),
    )
    for rel, path in zip((o.rel_path, o.sidecar_rel_path, o.thumb_rel_path), targets, strict=True):
        if rel and path is None:
            # a row pointing outside the data root is never followed onto the filesystem
            log.warning("refusing to delete %r: outside the outputs root", rel)
            continue
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as e:  # a locked file must not block deleting the row
            log.warning("could not delete %s: %s", path, e)
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
        path = contained(root, o.rel_path)
        if path is None or not path.exists():  # escaping rows count as missing, never as files
            o.is_missing = True
            n += 1
    session.flush()
    return n
