"""Boot: dirs -> backup-if-migrating -> upgrade -> meta -> default project -> orphan jobs."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import __version__, config, db
from .models import Job, JobStatus, Project, utcnow
from .services import backup, catalog, gitinfo, meta, migrate
from .services.gitinfo import CommitInfo

log = logging.getLogger(__name__)


@dataclass
class BootInfo:
    paths: config.Paths
    engine: Engine
    session_factory: sessionmaker[Session]
    schema_revision: str
    version: str
    commit: CommitInfo | None
    boot_id: str
    started_at: datetime
    orphaned_jobs: int
    requeued_jobs: list[str]


def orphan_jobs(session: Session) -> tuple[int, list[str]]:
    running = session.query(Job).filter(Job.status == JobStatus.running.value).all()
    for j in running:
        j.status = JobStatus.failed.value
        j.error_code = "orphaned"
        j.error_message = "Server restarted while this job was running."
        j.finished_at = utcnow()
    queued = [
        j.id
        for j in session.query(Job)
        .filter(Job.status == JobStatus.queued.value)
        .order_by(Job.created_at)
        .all()
    ]
    session.flush()
    return len(running), queued


def boot(paths: config.Paths) -> BootInfo:
    config.ensure_dirs(paths)
    if paths.db.exists() and migrate.needs_upgrade(paths.db):
        b = backup.backup_db(paths, "pre-migrate")
        backup.rotate(paths, "pre-migrate")
        log.info("pre-migrate backup: %s", b.name)
    migrate.upgrade(paths.db)  # raises MigrationFailed
    engine = db.make_engine(paths.db)
    factory = db.make_session_factory(engine)
    commit = gitinfo.current_commit()
    now = utcnow()
    schema_revision = migrate.head()
    with db.session_scope(factory) as s:
        meta.set(s, "schema_revision", schema_revision)
        meta.set(s, "app_version_last_boot", __version__)
        meta.set(s, "git_commit_last_boot", commit.sha if commit else "")
        meta.set(s, "last_boot_at", now.isoformat())
        if meta.get(s, "first_boot_at") is None:
            meta.set(s, "first_boot_at", now.isoformat())
        if not s.query(Project).filter_by(slug="default").first():
            s.add(Project(name="Default", slug="default"))
        (paths.outputs / "default").mkdir(parents=True, exist_ok=True)
        orphaned, requeued = orphan_jobs(s)
        try:
            catalog.seed_curated(s)
        except Exception as e:  # noqa: BLE001 - a seed failure must never block boot
            log.warning("catalog seed skipped: %s", e)
    return BootInfo(
        paths,
        engine,
        factory,
        schema_revision,
        __version__,
        commit,
        uuid.uuid4().hex,
        now,
        orphaned,
        requeued,
    )
