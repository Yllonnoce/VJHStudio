"""Alembic driver. A failed upgrade raises; refusing to start beats a half-migrated DB."""
from __future__ import annotations
from pathlib import Path
import sqlite3
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from ..config import REPO_ROOT

MIGRATIONS_DIR = REPO_ROOT / "migrations"
SCHEMA_FAIL_MSG = ("Database schema migration failed. RunwareStudio will not start on an "
                   "inconsistent database. Restore the pre-migrate backup from data/backups/ if needed.")


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
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        conn.close()
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
