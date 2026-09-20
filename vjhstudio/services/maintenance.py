"""Clear the database, with an optional pre-clear backup.

Deletes every table except `settings` (user preferences survive a clear),
then re-creates the Default project and re-seeds the app_meta bookkeeping
rows so the app keeps working without a restart.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..config import Paths
from ..models import AppMeta, Base, Project, Setting, utcnow
from . import backup, migrate

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClearResult:
    backup_path: Path | None
    rows_deleted: dict[str, int]


def clear_database(session_factory: sessionmaker[Session], paths: Paths, *,
                    backup_first: bool) -> ClearResult:
    backup_path: Path | None = None
    if backup_first:
        backup_path = backup.backup_db(paths, "pre-clear")
        backup.rotate(paths, "pre-clear")

    rows_deleted: dict[str, int] = {}
    with db.session_scope(session_factory) as s:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name == Setting.__tablename__:
                continue
            # Table names come from Base.metadata, never from user input.
            rows_deleted[table.name] = s.execute(
                text(f"SELECT COUNT(*) FROM {table.name}")).scalar_one()  # noqa: S608
            s.execute(text(f"DELETE FROM {table.name}"))  # noqa: S608

        s.add(Project(name="Default", slug="default"))
        now = utcnow().isoformat()
        for key, value in (
            ("schema_revision", migrate.head()),
            ("first_boot_at", now),
            ("last_boot_at", now),
            ("cleared_at", now),
        ):
            row = s.get(AppMeta, key)
            if row:
                row.value = value
            else:
                s.add(AppMeta(key=key, value=value))

    try:
        probe = session_factory()
        try:
            probe.get_bind().connect().exec_driver_sql("VACUUM")
        finally:
            probe.close()
    except Exception:  # noqa: BLE001 — best-effort, never blocks the clear
        log.warning("VACUUM after clear failed", exc_info=True)

    return ClearResult(backup_path=backup_path, rows_deleted=rows_deleted)
