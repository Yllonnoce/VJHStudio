"""Backup archives: one zip holding the database (and optionally the media files),
plus the restore that puts such an archive back over the live data directory.

An archive is `data/backups/vjhstudio-backup-<YYYYMMDD-HHMMSS>.zip` containing
`manifest.json`, `vjh.db` (a consistent snapshot taken with the sqlite backup API)
and, when asked for, `uploads/…`, `outputs/…` and `thumbs/…`. Media is stored
uncompressed (it is already compressed) and everything is streamed, so a multi-GB
outputs folder never lands in memory. `data/secrets/` is never included: an archive
carries no API key.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .. import __version__
from .. import db as db_mod
from ..config import Paths
from ..models import (
    Asset,
    CatalogModel,
    Job,
    JobStatus,
    Output,
    Project,
    Prompt,
    UsageEntry,
    utcnow,
)
from . import backup, migrate, projects
from . import outputs as outputs_svc

log = logging.getLogger(__name__)

ARCHIVE_PREFIX = "vjhstudio-backup-"
MANIFEST = "manifest.json"
DB_NAME = "vjh.db"
OUTPUTS_PREFIX = "outputs/"

NOT_AN_ARCHIVE = "not a VJHStudio backup"
JOBS_RUNNING = "Stop the running jobs first: a restore replaces the whole database."
COPY_CHUNK = 1024 * 1024

# Row counts recorded in the manifest. `settings`/`app_meta` are deliberately absent:
# they are never a thing the user exports or merges (they travel inside the db only).
COUNT_MODELS = (Project, Prompt, CatalogModel, Asset, Job, Output, UsageEntry)

_NAME_RE = re.compile(r"^vjhstudio-backup-[A-Za-z0-9._-]+\.zip$")
_STAMP_RE = re.compile(r"^vjhstudio-backup-(\d{8}-\d{6})")


class ArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveInfo:
    path: Path
    name: str
    created_at: datetime
    size_bytes: int
    includes: list[str]
    counts: dict[str, int]
    app_version: str
    schema_revision: str


@dataclass(frozen=True)
class RestoreResult:
    """What a replace-restore did. ``restart_required`` is a flag for the caller:
    this service never restarts the process itself (a route or the CLI decides)."""

    safety_backup: Path | None
    counts: dict[str, int]
    missing_outputs: int
    missing_assets: int
    restart_required: bool = True


# --- helpers ----------------------------------------------------------------


def _counts(session: Session) -> dict[str, int]:
    return {
        m.__tablename__: int(session.execute(select(func.count()).select_from(m)).scalar_one())
        for m in COUNT_MODELS
    }


def _stamp() -> str:
    # Naive UTC, exactly like backup.backup_db, so archive and .db names sort together.
    return utcnow().strftime("%Y%m%d-%H%M%S")


def _unique(backups: Path, name: str) -> Path:
    dest = backups / name
    stem, n = dest.stem, 1
    while dest.exists():
        n += 1
        dest = backups / f"{stem}-{n}.zip"
    return dest


def _snapshot_db(db_path: Path, dest: Path) -> None:
    """A consistent copy of a live (WAL) database, via the sqlite backup API."""
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def running_jobs(paths: Paths) -> int:
    """Queued or running jobs according to the database.

    Asking the DB rather than a JobRunner keeps this service runner-agnostic: a CLI
    restore has no runner at all, and a second process could hold the queue.
    """
    if not paths.db.exists():
        return 0
    try:
        with contextlib.closing(sqlite3.connect(paths.db)) as conn:
            row = conn.execute(
                "SELECT count(*) FROM jobs WHERE status IN (?, ?)",
                (JobStatus.queued.value, JobStatus.running.value),
            ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0  # no jobs table yet (or an unreadable file): nothing can be running


# --- create -----------------------------------------------------------------


def _media_members(
    session: Session, paths: Paths, root: Path, *, uploads: bool, outputs: bool
) -> list[tuple[str, Path]]:
    """(arcname, source) for every media file worth packing. Rows whose file is gone
    are skipped silently: the manifest counts rows, the zip holds what exists."""
    members: dict[str, Path] = {}
    if uploads:
        for a in session.execute(select(Asset)).scalars():
            src = outputs_svc.contained(paths.uploads, a.filename)
            if src is not None and src.is_file():
                members[f"uploads/{a.filename}"] = src
            thumb = paths.thumbs / f"asset-{a.sha256[:12]}.jpg"
            if thumb.is_file():
                members[f"thumbs/{thumb.name}"] = thumb
    if outputs:
        for o in session.execute(select(Output)).scalars():
            for rel in (o.rel_path, o.sidecar_rel_path):
                src = outputs_svc.contained(root, rel)
                if src is not None and src.is_file():
                    members[OUTPUTS_PREFIX + rel] = src
            if o.thumb_rel_path:
                src = outputs_svc.contained(paths.data, o.thumb_rel_path)
                if src is not None and src.is_file():
                    members[o.thumb_rel_path] = src
    return sorted(members.items())


def create_archive(
    session_factory: sessionmaker[Session],
    paths: Paths,
    *,
    uploads: bool = False,
    outputs: bool = False,
) -> Path:
    """Write a new archive and return its path."""
    if not paths.db.exists():
        raise ArchiveError(f"no database at {paths.db}")
    paths.backups.mkdir(parents=True, exist_ok=True)
    dest = _unique(paths.backups, f"{ARCHIVE_PREFIX}{_stamp()}.zip")

    with db_mod.session_scope(session_factory) as s:
        counts = _counts(s)
        root = projects.root_for(s, paths)
        media = _media_members(s, paths, root, uploads=uploads, outputs=outputs)

    manifest = {
        "app_version": __version__,
        "schema_revision": migrate.current(paths.db) or migrate.head(),
        "created_at": utcnow().isoformat(),
        "host": socket.gethostname(),
        "includes": ["db", *(["uploads"] if uploads else []), *(["outputs"] if outputs else [])],
        "counts": counts,
    }

    snapshot = paths.backups / f"{dest.stem}.db.part"
    part = dest.with_name(dest.name + ".part")
    try:
        _snapshot_db(paths.db, snapshot)
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2), zipfile.ZIP_DEFLATED)
            zf.write(snapshot, DB_NAME, zipfile.ZIP_DEFLATED)
            for arcname, src in media:
                try:
                    zf.write(src, arcname, zipfile.ZIP_STORED)
                except OSError as e:  # a file vanished or is unreadable mid-run
                    log.warning("archive skipped %s: %s", arcname, e)
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        snapshot.unlink(missing_ok=True)
    return dest


# --- read / list / name -----------------------------------------------------


def manifest_of(zip_path: Path | str) -> dict:
    """The manifest of an archive. Raises ArchiveError for anything that is not one."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            if MANIFEST not in names or DB_NAME not in names:
                raise ArchiveError(NOT_AN_ARCHIVE)
            data = json.loads(zf.read(MANIFEST).decode("utf-8"))
    except ArchiveError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError) as e:
        raise ArchiveError(NOT_AN_ARCHIVE) from e
    if not isinstance(data, dict):
        raise ArchiveError(NOT_AN_ARCHIVE)
    return data


def _created_at(path: Path, manifest: dict) -> datetime:
    raw = manifest.get("created_at")
    if isinstance(raw, str):
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(raw)
    m = _STAMP_RE.match(path.name)
    if m:
        with contextlib.suppress(ValueError):
            return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")  # noqa: DTZ007 — naive UTC
    return datetime.fromtimestamp(path.stat().st_mtime)  # noqa: DTZ006 — local mtime, last resort


def info_for(path: Path) -> ArchiveInfo:
    m = manifest_of(path)
    includes = [str(x) for x in (m.get("includes") or ["db"])]
    raw_counts = m.get("counts") or {}
    counts = {str(k): int(v) for k, v in raw_counts.items() if isinstance(v, int)}
    return ArchiveInfo(
        path=path,
        name=path.name,
        created_at=_created_at(path, m),
        size_bytes=path.stat().st_size,
        includes=includes,
        counts=counts,
        app_version=str(m.get("app_version") or ""),
        schema_revision=str(m.get("schema_revision") or ""),
    )


def list_archives(paths: Paths) -> list[ArchiveInfo]:
    """Every readable archive, newest first. An unreadable zip is skipped, never raised."""
    out: list[ArchiveInfo] = []
    if not paths.backups.exists():
        return out
    for f in paths.backups.glob(f"{ARCHIVE_PREFIX}*.zip"):
        try:
            out.append(info_for(f))
        except (ArchiveError, OSError) as e:
            log.warning("skipping unreadable archive %s: %s", f.name, e)
    return sorted(out, key=lambda a: (a.created_at, a.name), reverse=True)


def archive_path(paths: Paths, name: str) -> Path:
    """Resolve an archive name coming from the outside. Any separator, any name that
    is not an archive name at all, is refused rather than joined onto the data dir."""
    if not name or name != PurePosixPath(name).name or "\\" in name or not _NAME_RE.match(name):
        raise ArchiveError(f"{NOT_AN_ARCHIVE}: {name!r}")
    return paths.backups / name


def delete_archive(paths: Paths, name: str) -> bool:
    path = archive_path(paths, name)
    if not path.is_file():
        return False
    path.unlink()
    return True


def import_archive(paths: Paths, filename: str, content: bytes) -> Path:
    """Store an uploaded archive under a fresh, safe name. The bytes are validated
    before they are given an archive name, so a stray upload never shows up in the list."""
    paths.backups.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(dir=paths.backups, prefix="import-", suffix=".part")
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        manifest_of(tmp)
        base = PurePosixPath(filename or "").name
        wanted = base if _NAME_RE.match(base) else f"{ARCHIVE_PREFIX}{_stamp()}.zip"
        dest = _unique(paths.backups, wanted)
        os.replace(tmp, dest)
        return dest
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# --- restore (replace) ------------------------------------------------------


def _check_members(names: list[str]) -> None:
    """Refuse a zip whose member names could write outside the data directory."""
    for raw in names:
        name = raw.rstrip("/")
        if not name:
            continue
        # A backslash or a colon is a path separator / drive or stream marker on
        # Windows, so both are refused outright; VJHStudio never writes either.
        if "\\" in raw or ":" in raw or ".." in PurePosixPath(name).parts or name.startswith("/"):
            raise ArchiveError(f"archive member escapes the data directory: {raw!r}")


def _target(paths: Paths, root: Path, name: str) -> Path:
    """Where a media member lands, re-checked against its root after resolution."""
    if name.startswith(OUTPUTS_PREFIX):
        base, rel = root, name[len(OUTPUTS_PREFIX) :]
    else:
        base, rel = paths.data, name
    base.mkdir(parents=True, exist_ok=True)
    target = Path(base / rel)
    if not target.resolve().is_relative_to(base.resolve()):
        raise ArchiveError(f"archive member escapes the data directory: {name!r}")
    return target


def _stream_to(zf: zipfile.ZipFile, member: str | zipfile.ZipInfo, target: Path) -> None:
    """Write one member through a .part file so a crash cannot leave a torn file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with zf.open(member) as src, part.open("wb") as dst:
            shutil.copyfileobj(src, dst, COPY_CHUNK)
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _missing_assets(session: Session, paths: Paths) -> int:
    n = 0
    for a in session.execute(select(Asset)).scalars():
        path = outputs_svc.contained(paths.uploads, a.filename)
        if path is None or not path.exists():
            n += 1
    return n


def restore_replace(
    paths: Paths,
    zip_path: Path | str,
    *,
    engine: Engine | None = None,
    jobs_running: int = 0,
) -> RestoreResult:
    """Replace the whole data directory with an archive's contents.

    Order: refuse while work is in flight -> validate the zip -> safety backup ->
    swap `vjh.db` -> `alembic upgrade head` (the archive may be older) -> extract media
    -> flag rows whose files are absent. The caller restarts the process when
    ``restart_required`` says so; this service never exits.
    """
    zip_path = Path(zip_path)
    if int(jobs_running) > 0 or running_jobs(paths) > 0:
        raise ArchiveError(JOBS_RUNNING)

    manifest_of(zip_path)  # ArchiveError unless it is really one of ours
    with zipfile.ZipFile(zip_path) as zf:
        _check_members(zf.namelist())

    paths.data.mkdir(parents=True, exist_ok=True)
    paths.backups.mkdir(parents=True, exist_ok=True)
    safety: Path | None = None
    if paths.db.exists():
        safety = backup.backup_db(paths, "pre-restore")
        backup.rotate(paths, "pre-restore")

    if engine is not None:
        engine.dispose()
    for suffix in ("-wal", "-shm"):
        paths.db.with_name(paths.db.name + suffix).unlink(missing_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        _stream_to(zf, DB_NAME, paths.db)

    migrate.upgrade(paths.db)  # raises MigrationFailed

    fresh = db_mod.make_engine(paths.db)
    try:
        factory = db_mod.make_session_factory(fresh)
        # The outputs root can be redirected by a setting, and the setting we must
        # honour is the restored one - so it is read after the db is in place.
        with db_mod.session_scope(factory) as s:
            root = projects.root_for(s, paths)
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.filename in (MANIFEST, DB_NAME):
                    continue
                _stream_to(zf, info, _target(paths, root, info.filename))
        with db_mod.session_scope(factory) as s:
            missing_outputs = outputs_svc.mark_missing(s, paths)
            missing_assets = _missing_assets(s, paths)
            counts = _counts(s)
    finally:
        fresh.dispose()

    return RestoreResult(
        safety_backup=safety,
        counts=counts,
        missing_outputs=missing_outputs,
        missing_assets=missing_assets,
        restart_required=True,
    )
