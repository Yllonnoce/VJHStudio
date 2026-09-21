"""Alembic driver. A failed upgrade raises; refusing to start beats a half-migrated DB."""

from __future__ import annotations

import contextlib
import sqlite3
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from ..config import REPO_ROOT, ensure_dirs, resolve_paths

MIGRATIONS_DIR = REPO_ROOT / "migrations"
SCHEMA_FAIL_MSG = (
    "Database schema migration failed. VJHStudio will not start on an "
    "inconsistent database. Restore the pre-migrate backup from data/backups/ if needed."
)


class MigrationFailed(RuntimeError):
    pass


def alembic_config(db_path: Path | str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + str(db_path).replace("%", "%%"))
    return cfg


def head() -> str:
    return ScriptDirectory.from_config(alembic_config(":memory:")).get_current_head() or ""


def current(db_path: Path) -> str | None:
    if not Path(db_path).exists():
        return None
    try:
        # closing() matters on the error path: a DB without alembic_version
        # would otherwise leak one connection per boot.
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def needs_upgrade(db_path: Path) -> bool:
    return current(db_path) != head()


def upgrade(db_path: Path, revision: str = "head") -> None:
    try:
        command.upgrade(alembic_config(db_path), revision)
    except Exception as e:  # noqa: BLE001
        raise MigrationFailed(SCHEMA_FAIL_MSG) from e


USAGE = "usage: python -m vjhstudio.services.migrate [upgrade|current|head]"


def main(argv: list[str] | None = None) -> int:
    """Run migrations in a *separate* process.

    The updater calls this through `uv run` so the migration executes with the
    freshly synced packages instead of the ones this process imported at boot.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "upgrade"
    if cmd == "head":  # a pure question about the code, no data dir needed
        print(head())
        return 0
    paths = resolve_paths()
    if cmd == "current":
        print(current(paths.db) or "")
        return 0
    if cmd != "upgrade":
        print(USAGE, file=sys.stderr)
        return 2
    ensure_dirs(paths)
    try:
        upgrade(paths.db)
    except MigrationFailed as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"schema {head()} at {paths.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
