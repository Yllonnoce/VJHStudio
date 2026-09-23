"""Outputs: record what was saved, browse the gallery, favourite, delete, remix."""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, object_session, sessionmaker

from .. import __version__
from .. import db as db_mod
from ..config import Paths
from ..models import Job, Output, Project, UsageEntry, utcnow
from ..runware.download import SavedFile, make_poster, make_thumbnail, write_sidecar
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
    duration_s: float | None = None
    poster_rel_path: str | None = None


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
    dims: tuple[int | None, int | None] | None = None,
    duration_s: float | None = None,
    thumbnail: bool = True,
    poster: bool = False,
) -> list[Prepared]:
    """Sidecars, dimensions and thumbnails. Deliberately does no DB work: JPEG encoding
    must not happen inside a write transaction.

    ``dims`` supplies width/height for files Pillow cannot open (video), and
    ``thumbnail=False`` skips the still that only makes sense for an image.
    ``poster=True`` asks ffmpeg for a frame out of a video instead; the frame is stored
    as both the poster *and* the thumbnail, so every existing "thumb" consumer -- job
    cards, the home strip, the backup archive -- shows it without knowing about posters."""
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
        w, h = dims if dims is not None else _dims(f.path)
        thumb_rel: str | None = None
        poster_rel: str | None = None
        still = paths.thumbs / f"{f.path.stem}.jpg"
        if thumbnail:
            if make_thumbnail(f.path, still) is not None:
                thumb_rel = still.relative_to(paths.data).as_posix()
        elif poster and make_poster(f.path, still) is not None:
            poster_rel = thumb_rel = still.relative_to(paths.data).as_posix()
        out.append(
            Prepared(
                saved=f,
                rel_path=f.path.relative_to(root).as_posix(),
                sidecar_rel_path=side.relative_to(root).as_posix(),
                thumb_rel_path=thumb_rel,
                width=w,
                height=h,
                params=params,
                duration_s=duration_s,
                poster_rel_path=poster_rel,
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
            poster_rel_path=p.poster_rel_path,
            model_air=meta.model_air,
            prompt_text=meta.prompt_text,
            negative_prompt=meta.negative_prompt,
            params_json=p.params,
            seed=p.saved.item.seed,
            width=p.width,
            height=p.height,
            duration_s=p.duration_s,
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


def backfill_posters(
    session_factory: sessionmaker[Session],
    paths: Paths,
    limit: int | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Give every video output that still has no poster a frame out of its own file.

    Best effort by design: a row whose file is gone, or that ffmpeg cannot read, is
    skipped and the next one is tried. The frame grab happens outside any session and
    each row is committed on its own, so a crash keeps whatever was already done and the
    next start picks up the rest.

    ``should_stop`` is checked before every row. This runs in a worker thread that an
    ``asyncio`` cancellation cannot interrupt, so shutdown sets the flag and the loop
    returns between rows -- crucially *before* opening another session, which after
    ``engine.dispose()`` would be a use of a torn-down engine.
    Returns the number of posters actually written.
    """
    query = (
        select(Output.id, Output.rel_path)
        .where(Output.kind == "video", Output.poster_rel_path.is_(None))
        .order_by(Output.created_at.desc(), Output.id.desc())
    )
    if limit is not None:
        query = query.limit(max(0, int(limit)))
    with db_mod.session_scope(session_factory) as s:
        root = projects.root_for(s, paths)
        todo = list(s.execute(query).all())
    made = 0
    for output_id, rel_path in todo:
        if should_stop is not None and should_stop():
            log.info("video poster backfill stopping early after %d", made)
            break
        src = contained(root, rel_path)
        if src is None or not src.is_file():
            continue
        dest = paths.thumbs / f"{src.stem}.jpg"
        if make_poster(src, dest) is None:
            continue
        poster_rel = dest.relative_to(paths.data).as_posix()
        with db_mod.session_scope(session_factory) as s:
            row = s.get(Output, output_id)
            if row is None:
                continue
            row.poster_rel_path = poster_rel
            if not row.thumb_rel_path:
                row.thumb_rel_path = poster_rel
        made += 1
    return made


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
    # The poster is usually the very same file as the thumbnail, so the same path can
    # appear twice here; ``unlink(missing_ok=True)`` makes the second pass a no-op.
    rels = (o.rel_path, o.sidecar_rel_path, o.thumb_rel_path, o.poster_rel_path)
    targets = (
        contained(root, o.rel_path),
        contained(root, o.sidecar_rel_path),
        contained(paths.data, o.thumb_rel_path),
        contained(paths.data, o.poster_rel_path),
    )
    for rel, path in zip(rels, targets, strict=True):
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


class OutputMoveError(RuntimeError):
    """A move that could not be carried out. The row is left exactly as it was."""


def _sidecar_name(filename: str, sidecar_rel_path: str) -> str:
    """Keep the sidecar paired with its media file: the file's stem, the sidecar's suffix."""
    return Path(filename).stem + (Path(sidecar_rel_path).suffix or ".json")


def _free_filename(
    session: Session, dest_dir: Path, project_id: int, output_id: int, output: Output
) -> str:
    """A name that is free in the target project, both on disk and in the table.

    ``ux_outputs_project_filename`` is unique, so a collision has to be resolved for the
    row as well as for the file -- and the sidecar is checked too, or the media file
    would land on a free name next to somebody else's sidecar."""
    stem, suffix = Path(output.filename).stem, Path(output.filename).suffix
    candidate, n = output.filename, 1
    while True:
        on_disk = (dest_dir / candidate).exists() or (
            dest_dir / _sidecar_name(candidate, output.sidecar_rel_path)
        ).exists()
        in_db = (
            session.execute(
                select(Output.id).where(
                    Output.project_id == project_id,
                    Output.filename == candidate,
                    Output.id != output_id,
                )
            ).first()
            is not None
        )
        if not (on_disk or in_db):
            return candidate
        n += 1
        candidate = f"{stem}-{n}{suffix}"


def _relocate(src: Path, dest: Path) -> None:
    """``os.replace`` is atomic but only within one filesystem; the outputs root can be
    set to another drive, and that raises EXDEV -- which is exactly ``shutil.move``'s job."""
    try:
        os.replace(src, dest)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        shutil.move(str(src), str(dest))


def _retag_sidecar(path: Path, slug: str) -> None:
    """Keep the sidecar's own ``project`` field honest after a move. Best effort: a
    sidecar that is missing, or is not readable JSON, is left as it is -- the move
    itself has already succeeded and must not be undone over a metadata nicety."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("could not re-tag sidecar %s: %s", path, e)
        return
    if not isinstance(data, dict) or data.get("project") == slug:
        return
    data["project"] = slug
    try:
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except OSError as e:
        log.warning("could not re-tag sidecar %s: %s", path, e)


def move(session: Session, paths: Paths, output_id: int, project_id: int) -> Output:
    """File an output under another project: the media file, its sidecar and its cost.

    The thumbnail (and a video's poster) live in the shared ``thumbs/`` folder, which is
    project-independent, so they stay exactly where they are.

    Cost follows the *job*: the output's job and that job's usage rows are re-pointed too,
    which is what the project totals are built from. A job with several outputs therefore
    takes its whole spend along with the first one moved -- spend is charged per task, and
    splitting it over the images a task returned would invent numbers nobody was billed.

    The disk is done first and the row second, so a failed move leaves the row describing
    a file that is still there. ``is_missing`` rows (and rows whose file has gone without
    the flag being set yet) move as a row only.
    """
    o = session.get(Output, output_id)
    if o is None:
        raise LookupError(output_id)
    target = projects.get(session, int(project_id))
    if target is None:
        raise OutputMoveError("that project does not exist")
    if target.is_archived:
        raise OutputMoveError(f"{target.name} is archived — un-archive it first")
    if o.project_id == target.id:
        return o

    root = projects.root_for(session, paths)
    src = contained(root, o.rel_path)
    if src is None:
        # a row pointing outside the outputs root is never followed onto the filesystem
        raise OutputMoveError("this output's path lies outside the outputs folder")
    src_sidecar = contained(root, o.sidecar_rel_path)
    on_disk = src.is_file()

    dest_dir = projects.dir_for(paths, target.slug, projects.root_override(session))
    if on_disk:
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise OutputMoveError(f"could not open the folder for {target.name}: {e}") from e

    filename = _free_filename(session, dest_dir, target.id, o.id, o)
    rel_path = f"{target.slug}/{filename}"
    sidecar_rel_path = f"{target.slug}/{_sidecar_name(filename, o.sidecar_rel_path)}"
    dest, dest_sidecar = contained(root, rel_path), contained(root, sidecar_rel_path)
    if dest is None or dest_sidecar is None:
        raise OutputMoveError("that project's folder lies outside the outputs folder")

    if on_disk:
        try:
            _relocate(src, dest)
        except OSError as e:
            raise OutputMoveError(f"could not move {o.filename}: {e}") from e
        if src_sidecar is not None and src_sidecar.is_file():
            try:
                _relocate(src_sidecar, dest_sidecar)
            except OSError as e:
                # put the media file back, so the untouched row still describes the disk
                try:
                    _relocate(dest, src)
                except OSError:
                    log.warning("could not undo the move of %s back to %s", dest, src)
                raise OutputMoveError(f"could not move the sidecar for {o.filename}: {e}") from e
        _retag_sidecar(dest_sidecar, target.slug)

    o.filename = filename
    o.rel_path = rel_path
    o.sidecar_rel_path = sidecar_rel_path
    o.project_id = target.id
    o.is_missing = not on_disk
    job = session.get(Job, o.job_id)
    if job is not None:
        job.project_id = target.id
    session.execute(
        update(UsageEntry).where(UsageEntry.job_id == o.job_id).values(project_id=target.id)
    )
    session.flush()
    return o


def remix_request(output: Output) -> dict:
    """The job's original request with this output's seed pinned, ready to re-submit."""
    session = object_session(output)
    job = session.get(Job, output.job_id) if session is not None else None
    data = dict((job.request_json if job else None) or {})
    data["seed"] = output.seed
    return data


def mark_missing(session: Session, paths: Paths) -> int:
    """Re-sync every row's ``is_missing`` flag with the disk, both ways.

    Clearing matters as much as setting: a restore puts files back underneath rows that
    were flagged long ago, and a row left flagged would hide a file that is there.
    Returns the number of rows *newly* flagged missing.
    """
    root = projects.root_for(session, paths)
    n = 0
    for o in session.execute(select(Output)).scalars():
        path = contained(root, o.rel_path)
        gone = path is None or not path.exists()  # escaping rows count as missing, never as files
        if gone and not o.is_missing:
            o.is_missing = True
            n += 1
        elif not gone and o.is_missing:
            o.is_missing = False
    session.flush()
    return n
