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
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
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
from . import settings as settings_svc

log = logging.getLogger(__name__)

ARCHIVE_PREFIX = "vjhstudio-backup-"
MANIFEST = "manifest.json"
DB_NAME = "vjh.db"
OUTPUTS_PREFIX = "outputs/"

NOT_AN_ARCHIVE = "not a VJHStudio backup"
JOBS_RUNNING = "Stop the running jobs first: a restore replaces the whole database."
COPY_CHUNK = 1024 * 1024

# The only member prefixes a restore may write. Anything else in the zip - `secrets/`,
# `backups/`, a stray `vjh.db-wal`, a directory a future version adds - is reported and
# skipped, so an archive can never drop a file into a part of the data dir it does not own.
EXTRACT_PREFIXES = ("uploads/", "thumbs/", OUTPUTS_PREFIX)

# Cumulative *expanded* size a restore will accept, so a zip bomb runs out of patience
# rather than out of disk. Overridable per call for tests and for a smaller host.
MAX_RESTORE_BYTES = 20 * 1024**3

# Row counts recorded in the manifest. `settings`/`app_meta` are deliberately absent:
# they are never a thing the user exports or merges (they travel inside the db only).
COUNT_MODELS = (Project, Prompt, CatalogModel, Asset, Job, Output, UsageEntry)

_NAME_RE = re.compile(r"^vjhstudio-backup-[A-Za-z0-9._-]+\.zip$")
_STAMP_RE = re.compile(r"^vjhstudio-backup-(\d{8}-\d{6})")


class ArchiveError(RuntimeError):
    pass


class MergeError(ArchiveError):
    """A merge that failed *after* its safety backup was taken, so the caller can say
    where that backup is. It is an ArchiveError, so existing handlers still catch it."""

    def __init__(self, message: str, safety_backup: Path | None = None):
        super().__init__(message)
        self.safety_backup = safety_backup


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
    """What a replace-restore did. The caller always restarts afterwards - the whole
    database underneath the running process has been swapped - so there is no flag to
    consult; this service simply never exits by itself."""

    safety_backup: Path | None
    counts: dict[str, int]
    missing_outputs: int
    missing_assets: int
    skipped: list[str]
    outputs_root: Path


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


def _check_members(infos: list[zipfile.ZipInfo], max_bytes: int) -> None:
    """Refuse a zip whose members could write outside the data dir or fill the disk."""
    total = 0
    for info in infos:
        raw = info.filename
        name = raw.rstrip("/")
        if not name:
            continue
        # A backslash or a colon is a path separator / drive or stream marker on
        # Windows, so both are refused outright; VJHStudio never writes either.
        if "\\" in raw or ":" in raw or ".." in PurePosixPath(name).parts or name.startswith("/"):
            raise ArchiveError(f"archive member escapes the data directory: {raw!r}")
        total += max(info.file_size, 0)
        if total > max_bytes:
            raise ArchiveError(
                f"archive expands to more than {max_bytes} bytes; refusing to restore it"
            )


def _restore_root(session: Session, paths: Paths) -> Path:
    """The outputs root this restore may write into.

    ``paths.outputs_dir`` lives in the *restored* database, which is untrusted input: an
    archive could point it at any directory on this machine. A root that does not resolve
    under the data dir is refused and the setting is reset to the default, so that the
    extraction and the app that reads those rows afterwards agree on one place.
    """
    configured = projects.root_for(session, paths)
    try:
        if configured.resolve().is_relative_to(paths.data.resolve()):
            return configured
    except OSError:
        pass
    log.warning(
        "restored paths.outputs_dir %s is outside %s; restoring into %s instead",
        configured,
        paths.data,
        paths.outputs,
    )
    settings_svc.set_many(session, {"paths.outputs_dir": ""})
    return paths.outputs


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


def _swap(target: Path, write) -> None:
    """Write through a `.part` file and `os.replace` it into place, so an interrupted
    write can never leave a torn file where a whole one used to be."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with part.open("wb") as dst:
            write(dst)
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _stream_to(zf: zipfile.ZipFile, member: str | zipfile.ZipInfo, target: Path) -> None:
    def write(dst):
        with zf.open(member) as src:
            shutil.copyfileobj(src, dst, COPY_CHUNK)

    _swap(target, write)


def _copy_to(src: Path, target: Path) -> None:
    def write(dst):
        with src.open("rb") as fh:
            shutil.copyfileobj(fh, dst, COPY_CHUNK)

    _swap(target, write)


def _put_back(paths: Paths, safety: Path | None) -> None:
    """Undo the database swap after a failed upgrade, through the same swap routine."""
    for suffix in ("-wal", "-shm"):
        paths.db.with_name(paths.db.name + suffix).unlink(missing_ok=True)
    if safety is not None and safety.exists():
        _copy_to(safety, paths.db)
    else:
        paths.db.unlink(missing_ok=True)


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
    max_bytes: int = MAX_RESTORE_BYTES,
) -> RestoreResult:
    """Replace the whole data directory with an archive's contents.

    Order: refuse while work is in flight -> validate the zip -> safety backup ->
    swap `vjh.db` -> `alembic upgrade head` (the archive may be older; a failure puts the
    safety backup back) -> extract the media members only -> re-sync the `is_missing`
    flags. The caller restarts afterwards; this service never exits by itself.
    """
    zip_path = Path(zip_path)
    if int(jobs_running) > 0 or running_jobs(paths) > 0:
        raise ArchiveError(JOBS_RUNNING)

    manifest_of(zip_path)  # ArchiveError unless it is really one of ours
    with zipfile.ZipFile(zip_path) as zf:
        _check_members(zf.infolist(), max_bytes)

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

    try:
        migrate.upgrade(paths.db)
    except Exception as e:
        # A half-migrated restore is worse than no restore: put the old database back
        # before anything else touches it, and say where it came from.
        _put_back(paths, safety)
        where = (
            f" The previous database was put back from {safety}."
            if safety is not None
            else " There was no database to put back, so the restored one was removed."
        )
        raise ArchiveError(f"The restored database could not be migrated.{where}") from e

    skipped: list[str] = []
    fresh = db_mod.make_engine(paths.db)
    try:
        factory = db_mod.make_session_factory(fresh)
        # The outputs root comes from the *restored* db, so it is validated before use.
        with db_mod.session_scope(factory) as s:
            root = _restore_root(s, paths)
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = info.filename
                if info.is_dir() or name in (MANIFEST, DB_NAME):
                    continue
                if not name.startswith(EXTRACT_PREFIXES):
                    skipped.append(name)
                    continue
                _stream_to(zf, info, _target(paths, root, name))
        if skipped:
            log.warning(
                "restore skipped %d member(s) outside %s: %s",
                len(skipped),
                ", ".join(EXTRACT_PREFIXES),
                ", ".join(skipped[:10]),
            )
        with db_mod.session_scope(factory) as s:
            outputs_svc.mark_missing(s, paths)
            missing_outputs = int(
                s.execute(
                    select(func.count()).select_from(Output).where(Output.is_missing.is_(True))
                ).scalar_one()
            )
            missing_assets = _missing_assets(s, paths)
            counts = _counts(s)
    finally:
        fresh.dispose()

    return RestoreResult(
        safety_backup=safety,
        counts=counts,
        missing_outputs=missing_outputs,
        missing_assets=missing_assets,
        skipped=skipped,
        outputs_root=root,
    )


# --- merge (additive union by natural keys) ---------------------------------

# Dependency order: every table is imported after the tables its foreign keys point at,
# so one transaction can insert the lot with `PRAGMA foreign_keys=ON` still on.
MERGE_TABLES = (
    "projects",
    "catalog_models",
    "assets",
    "prompts",
    "jobs",
    "outputs",
    "usage_entries",
)

_LABELS = {
    "projects": ("project", "projects"),
    "catalog_models": ("model", "models"),
    "assets": ("asset", "assets"),
    "prompts": ("prompt", "prompts"),
    "jobs": ("job", "jobs"),
    "outputs": ("output", "outputs"),
    "usage_entries": ("usage row", "usage rows"),
}

ORPHANED_MESSAGE = "This job was still in the queue when the backup was made."

# Where one media file stands when the rows are written; see `_want`.
_HERE, _QUEUED, _ABSENT = "here", "queued", "absent"
BAD_ARCHIVE_DB = (
    "The archive's database could not be migrated to this version's schema; nothing was merged."
)


@dataclass(frozen=True)
class TableCounts:
    """What one table contributed: rows that are new here, rows we already had (kept as
    ours), and rows whose media file the archive did not carry."""

    new: int = 0
    existing: int = 0
    missing_files: int = 0


@dataclass(frozen=True)
class MergeReport:
    counts: dict[str, TableCounts]
    errors: list[str]
    app_version: str
    schema_revision: str
    created_at: str
    dry_run: bool
    safety_backup: Path | None = None

    @property
    def total_new(self) -> int:
        return sum(c.new for c in self.counts.values())

    @property
    def total_missing_files(self) -> int:
        return sum(c.missing_files for c in self.counts.values())


@dataclass
class _Tally:
    new: int = 0
    existing: int = 0
    missing_files: int = 0


@dataclass
class _Copy:
    """One media file to lift out of the archive once the rows are committed."""

    member: str  # name inside the zip
    target: str  # destination name, same namespace (equal to member unless renamed)
    table: str | None = None  # None: an extra (sidecar/thumb); a failure is not counted
    output: Output | None = None  # un-flagged is_missing once its file is on disk
    ok: bool = False
    error: str = ""


@dataclass
class _Merge:
    """The state one merge pass carries between tables: the two sessions, the id maps
    built as rows are inserted, and everything the report is made of."""

    session: Session  # ours, inside the merge transaction
    incoming: Session  # the archive's database, read-only
    paths: Paths
    root: Path
    names: set[str]  # members of the zip
    tally: dict[str, _Tally]
    errors: list[str] = field(default_factory=list)
    project_ids: dict[int, int] = field(default_factory=dict)
    prompt_ids: dict[int, int] = field(default_factory=dict)
    job_ids: set[str] = field(default_factory=set)  # present here after the jobs pass
    new_job_ids: set[str] = field(default_factory=set)  # inserted by *this* merge
    new_slugs: list[str] = field(default_factory=list)
    copies: list[_Copy] = field(default_factory=list)


def prompt_key(
    kind: str,
    final_prompt: str,
    negative_prompt: str,
    form_json: dict | None,
    composed_prompt: str = "",
) -> str:
    """The natural key two prompts are the same by: everything the prompt *says*, and
    nothing about how it is filed (title, project, favourite, use count, timestamps).

    This is byte-for-byte the formula main's Phase 5 uses for the stored
    `prompts.content_hash` column:

        sha256(json.dumps([kind, composed, final, negative, form_canonical],
                          sort_keys=True, separators=(",", ":")))
        form_canonical = {k: v for k, v in form.items() if v != ""}

    An empty form field is dropped, so a prompt saved before a field existed hashes the
    same as one where the user left it blank. The two branches must not drift: when the
    column lands, `_prompt_key` reads it instead and this function goes away.
    """
    form = form_json if isinstance(form_json, dict) else {}
    canonical = {k: v for k, v in form.items() if v != ""}
    payload = json.dumps(
        [kind, composed_prompt, final_prompt, negative_prompt, canonical],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prompt_key(p: Prompt) -> str:
    return prompt_key(p.kind, p.final_prompt, p.negative_prompt, p.form_json, p.composed_prompt)


def _columns(model: type) -> list[str]:
    return [c.key for c in sa_inspect(model).mapper.column_attrs]


def _values(obj, *, exclude: tuple[str, ...] = ()) -> dict:
    """Every mapped column of a row as a plain dict, so it can be re-inserted into the
    other database without dragging an ORM identity along. Reading the columns off the
    mapper (rather than a hand-written list) means a column added later travels too."""
    return {k: getattr(obj, k) for k in _columns(type(obj)) if k not in exclude}


def _unique_filename(name: str, taken: set[str]) -> str:
    """Asset filenames are `<sha12>.<ext>` and carry a unique index, so a clash means two
    different files that happen to share a name; the newcomer is renamed rather than lost."""
    stem, dot, ext = name.rpartition(".")
    stem = stem or name
    n = 1
    candidate = name
    while candidate in taken:
        n += 1
        candidate = f"{stem}-{n}{dot}{ext}" if dot else f"{stem}-{n}"
    return candidate


def _merge_target(paths: Paths, root: Path, name: str) -> Path:
    """Where a media file lands. Only the media prefixes are addressable and the result
    is re-checked against its base, so a row from the archive cannot name a path outside
    the data directory. Unlike `_target` this creates nothing: a preview writes nothing."""
    if not name.startswith(EXTRACT_PREFIXES):
        raise ArchiveError(f"not a media file: {name!r}")
    if name.startswith(OUTPUTS_PREFIX):
        base, rel = root, name[len(OUTPUTS_PREFIX) :]
    else:
        base, rel = paths.data, name
    target = Path(base / rel)
    if not target.resolve().is_relative_to(base.resolve()):
        raise ArchiveError(f"archive member escapes the data directory: {name!r}")
    return target


def _have_file(m: _Merge, member: str) -> Path | None:
    """The destination for a member when it can be written there, else None."""
    try:
        return _merge_target(m.paths, m.root, member)
    except (ArchiveError, OSError) as e:
        m.errors.append(str(e))
        return None


def _want(m: _Merge, member: str, target: str, *, table: str | None, output=None) -> str:
    """Queue one file copy and say where that file stands:

    `_HERE`   - already on disk; a merge adds, so it is never overwritten.
    `_QUEUED` - the archive carries it; it is copied after the rows are committed, and
                the row it belongs to stays `is_missing` until that copy lands.
    `_ABSENT` - the archive does not carry it and it is not here.
    """
    dest = _have_file(m, target)
    if dest is None:
        return _ABSENT
    if dest.exists():
        return _HERE
    if member not in m.names:
        return _ABSENT
    m.copies.append(_Copy(member, target, table, output))
    return _QUEUED


# --- merge: one function per table, in dependency order ---------------------


def _merge_projects(m: _Merge) -> None:
    t = m.tally["projects"]
    local = {p.slug: p.id for p in m.session.execute(select(Project)).scalars()}
    for p in m.incoming.execute(select(Project).order_by(Project.id)).scalars():
        if p.slug in local:
            m.project_ids[p.id] = local[p.slug]
            t.existing += 1
            continue
        row = Project(**_values(p, exclude=("id",)))
        m.session.add(row)
        m.session.flush()
        local[p.slug] = row.id
        m.project_ids[p.id] = row.id
        m.new_slugs.append(row.slug)
        t.new += 1


def _merge_catalog(m: _Merge) -> None:
    t = m.tally["catalog_models"]
    local = {c.air for c in m.session.execute(select(CatalogModel)).scalars()}
    for c in m.incoming.execute(select(CatalogModel).order_by(CatalogModel.id)).scalars():
        if c.air in local:
            t.existing += 1
            continue
        m.session.add(CatalogModel(**_values(c, exclude=("id",))))
        local.add(c.air)
        t.new += 1
    m.session.flush()


def _merge_assets(m: _Merge) -> None:
    t = m.tally["assets"]
    rows = list(m.session.execute(select(Asset)).scalars())
    by_sha = {a.sha256 for a in rows}
    taken = {a.filename for a in rows}
    for a in m.incoming.execute(select(Asset).order_by(Asset.id)).scalars():
        if a.sha256 in by_sha:
            t.existing += 1  # ours wins: the bytes are identical by definition
            continue
        filename = _unique_filename(a.filename, taken)
        m.session.add(Asset(**{**_values(a, exclude=("id",)), "filename": filename}))
        by_sha.add(a.sha256)
        taken.add(filename)
        t.new += 1
        if _want(m, f"uploads/{a.filename}", f"uploads/{filename}", table="assets") == _ABSENT:
            t.missing_files += 1
        thumb = f"thumbs/asset-{a.sha256[:12]}.jpg"
        _want(m, thumb, thumb, table=None)
    m.session.flush()


def _merge_prompts(m: _Merge) -> None:
    t = m.tally["prompts"]
    local: dict[str, int] = {}
    for p in m.session.execute(select(Prompt)).scalars():
        local.setdefault(_prompt_key(p), p.id)
    for p in m.incoming.execute(select(Prompt).order_by(Prompt.id)).scalars():
        key = _prompt_key(p)
        if key in local:
            m.prompt_ids[p.id] = local[key]
            t.existing += 1
            continue
        values = {**_values(p, exclude=("id",)), "project_id": m.project_ids.get(p.project_id)}
        row = Prompt(**values)
        m.session.add(row)
        m.session.flush()
        local[key] = row.id
        m.prompt_ids[p.id] = row.id
        t.new += 1


def _merge_jobs(m: _Merge) -> None:
    t = m.tally["jobs"]
    local = {j for (j,) in m.session.execute(select(Job.id))}
    unfinished = (JobStatus.queued.value, JobStatus.running.value)
    for j in m.incoming.execute(select(Job).order_by(Job.created_at, Job.id)).scalars():
        if j.id in local:
            m.job_ids.add(j.id)  # ours stands; an incoming output may still attach to it
            t.existing += 1
            continue
        project_id = m.project_ids.get(j.project_id)
        if project_id is None:
            m.errors.append(f"job {j.id}: its project is not in the archive; skipped")
            continue
        values = {
            **_values(j),
            "project_id": project_id,
            "prompt_id": m.prompt_ids.get(j.prompt_id),
        }
        if values["status"] in unfinished:
            # Nothing here can finish a job that was queued somewhere else.
            values.update(
                status=JobStatus.failed.value,
                error_code="orphaned",
                error_message=ORPHANED_MESSAGE,
                finished_at=values.get("finished_at") or utcnow(),
                cancel_requested=False,
            )
        m.session.add(Job(**values))
        local.add(j.id)
        m.job_ids.add(j.id)
        m.new_job_ids.add(j.id)
        t.new += 1
    m.session.flush()


def _merge_outputs(m: _Merge) -> None:
    t = m.tally["outputs"]
    here = {p.id: p.slug for p in m.session.execute(select(Project)).scalars()}
    there = {p.id: p.slug for p in m.incoming.execute(select(Project)).scalars()}
    local = {
        (here.get(o.project_id), o.filename) for o in m.session.execute(select(Output)).scalars()
    }
    for o in m.incoming.execute(select(Output).order_by(Output.id)).scalars():
        key = (there.get(o.project_id), o.filename)
        if key in local:
            t.existing += 1  # ours stands and its file is never touched
            continue
        project_id = m.project_ids.get(o.project_id)
        if project_id is None:
            m.errors.append(f"output {o.filename}: its project is not in the archive; skipped")
            continue
        if o.job_id not in m.job_ids:
            m.errors.append(f"output {o.filename}: its job {o.job_id} is not here; skipped")
            continue
        row = Output(**{**_values(o, exclude=("id",)), "project_id": project_id})
        m.session.add(row)
        m.session.flush()
        local.add(key)
        t.new += 1
        if not row.rel_path:
            # An empty path would resolve to the outputs root itself, which exists - so
            # the row would claim a file that is really a directory.
            m.errors.append(f"output {row.filename}: the archive records no file for it")
            row.is_missing = True
            t.missing_files += 1
        else:
            member = OUTPUTS_PREFIX + row.rel_path
            state = _want(m, member, member, table="outputs", output=row)
            # Only a file that is already here is not missing *now*; a queued copy clears
            # the flag after it lands, so a crash mid-copy never leaves a false promise.
            row.is_missing = state != _HERE
            if state == _ABSENT:
                t.missing_files += 1
        if row.sidecar_rel_path:
            sidecar = OUTPUTS_PREFIX + row.sidecar_rel_path
            _want(m, sidecar, sidecar, table=None)
        for extra in (row.thumb_rel_path, row.poster_rel_path):
            if extra:
                name = extra if extra.startswith(EXTRACT_PREFIXES) else OUTPUTS_PREFIX + extra
                _want(m, name, name, table=None)
    m.session.flush()


def _merge_usage(m: _Merge) -> None:
    """Only the rows belonging to jobs this merge inserted: a job we already had already
    has its usage, and importing it again would double the spend history."""
    t = m.tally["usage_entries"]
    for u in m.incoming.execute(select(UsageEntry).order_by(UsageEntry.id)).scalars():
        if u.job_id is not None and u.job_id in m.new_job_ids:
            values = {
                **_values(u, exclude=("id",)),
                "project_id": m.project_ids.get(u.project_id),
            }
            m.session.add(UsageEntry(**values))
            t.new += 1
        else:
            t.existing += 1
    m.session.flush()


# --- merge: the pass itself -------------------------------------------------


@contextlib.contextmanager
def _incoming(zip_path: Path):
    """Extract the archive's database to a temp directory, bring it to head (the archive
    may be older) and yield a read session over it together with the open zip. The temp
    directory is always removed; the live data dir is never touched by any of this."""
    tmp = Path(tempfile.mkdtemp(prefix="vjh-merge-"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            db_path = tmp / DB_NAME
            _stream_to(zf, DB_NAME, db_path)
            try:
                migrate.upgrade(db_path)
            except Exception as e:
                raise ArchiveError(BAD_ARCHIVE_DB) from e
            engine = db_mod.make_engine(db_path)
            try:
                session = db_mod.make_session_factory(engine)()
                try:
                    yield session, zf
                finally:
                    session.rollback()
                    session.close()
            finally:
                engine.dispose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_copies(m: _Merge, zf: zipfile.ZipFile) -> None:
    """Media, after the rows are committed. No database work happens here: copying a
    multi-GB outputs folder must not hold sqlite's write lock, and the rows are already
    safe on disk. Existing files are left alone: a merge adds, it never overwrites."""
    for c in m.copies:
        try:
            target = _merge_target(m.paths, m.root, c.target)
            if not target.exists():
                _stream_to(zf, c.member, target)
            c.ok = True
        except (ArchiveError, OSError, KeyError) as e:
            c.ok, c.error = False, str(e)


def _settle(m: _Merge) -> None:
    """A second, short transaction: every row whose file did land stops being missing.

    Rows were committed as `is_missing` for anything still to be copied, so an interrupted
    merge leaves rows that under-promise (flagged, file present) rather than rows that
    lie. `outputs.mark_missing` clears those on the next sweep either way.
    """
    for c in m.copies:
        if c.ok:
            if c.output is not None:
                c.output.is_missing = False
            continue
        if c.table is None:
            log.warning("merge could not copy %s: %s", c.member, c.error)
            continue
        m.errors.append(f"{c.member}: {c.error}")
        m.tally[c.table].missing_files += 1
    m.session.commit()


def _run_merge(
    session_factory: sessionmaker[Session],
    paths: Paths,
    zip_path: Path | str,
    *,
    dry_run: bool,
    max_bytes: int = MAX_RESTORE_BYTES,
) -> MergeReport:
    zip_path = Path(zip_path)
    manifest = manifest_of(zip_path)  # ArchiveError unless it is really one of ours
    with zipfile.ZipFile(zip_path) as zf:
        _check_members(zf.infolist(), max_bytes)

    tally = {t: _Tally() for t in MERGE_TABLES}
    errors: list[str] = []
    safety: Path | None = None
    # The safety backup comes *after* the archive's own database has been extracted and
    # migrated: an archive we cannot read must not leave a pre-merge backup behind.
    with _incoming(zip_path) as (incoming, zf):
        if not dry_run and paths.db.exists():
            paths.backups.mkdir(parents=True, exist_ok=True)
            safety = backup.backup_db(paths, "pre-merge")
            backup.rotate(paths, "pre-merge")
        session = session_factory()
        try:
            m = _Merge(
                session=session,
                incoming=incoming,
                paths=paths,
                root=projects.root_for(session, paths),
                names=set(zf.namelist()),
                tally=tally,
                errors=errors,
            )
            _merge_projects(m)
            _merge_catalog(m)
            _merge_assets(m)
            _merge_prompts(m)
            _merge_jobs(m)
            _merge_outputs(m)
            _merge_usage(m)
            if dry_run:
                # Everything above was a rehearsal against the real schema, which is the
                # only way the preview can promise the numbers the merge will produce.
                session.rollback()
            else:
                # Rows first, and only then the filesystem: the transaction ends before
                # any directory is made or any media is copied.
                session.commit()
                for slug in m.new_slugs:
                    (m.root / slug).mkdir(parents=True, exist_ok=True)
                _run_copies(m, zf)
                _settle(m)
        except Exception as e:
            session.rollback()
            raise MergeError(f"The merge failed: {e}", safety) from e
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    return MergeReport(
        counts={
            t: TableCounts(tally[t].new, tally[t].existing, tally[t].missing_files)
            for t in MERGE_TABLES
        },
        errors=errors,
        app_version=str(manifest.get("app_version") or ""),
        schema_revision=str(manifest.get("schema_revision") or ""),
        created_at=str(manifest.get("created_at") or ""),
        dry_run=dry_run,
        safety_backup=safety,
    )


def preview_merge(
    session_factory: sessionmaker[Session], paths: Paths, zip_path: Path | str
) -> MergeReport:
    """What `merge` would do, without doing any of it: no safety backup, no directories,
    no files, and a transaction that is rolled back rather than committed."""
    return _run_merge(session_factory, paths, zip_path, dry_run=True)


def merge(
    session_factory: sessionmaker[Session], paths: Paths, zip_path: Path | str
) -> MergeReport:
    """Add everything the archive has and this install has not, keeping ours on every
    clash. A safety backup is written first; the rows go in one transaction."""
    return _run_merge(session_factory, paths, zip_path, dry_run=False)


def merge_summary(report: MergeReport) -> str:
    """One line for the CLI and the confirmation partial."""
    parts = [
        f"{report.counts[t].new} {_LABELS[t][0 if report.counts[t].new == 1 else 1]}"
        for t in MERGE_TABLES
        if report.counts[t].new
    ]
    text = "Merged: " + (", ".join(parts) if parts else "nothing new")
    missing = report.total_missing_files
    if missing:
        text += f", {missing} file{'' if missing == 1 else 's'} missing"
    return text
