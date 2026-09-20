"""SQLite backups via the online backup API (safe under WAL)."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import Paths
from ..models import utcnow

_NAME = re.compile(r"^vjh-(\d{8}-\d{6})-(.+)\.db$")


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    label: str
    created_at: datetime
    size_bytes: int


def backup_db(paths: Paths, label: str) -> Path:
    if not paths.db.exists():
        raise FileNotFoundError(paths.db)
    paths.backups.mkdir(parents=True, exist_ok=True)
    # Naive UTC, like every timestamp in the DB (models.utcnow), so a backup
    # filename and the created_at rows inside it agree.
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    dest = paths.backups / f"vjh-{stamp}-{label}.db"
    src = sqlite3.connect(paths.db)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    return dest


def list_backups(paths: Paths) -> list[BackupInfo]:
    out: list[BackupInfo] = []
    if not paths.backups.exists():
        return out
    for f in paths.backups.glob("vjh-*.db"):
        m = _NAME.match(f.name)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")  # noqa: DTZ007 — naive UTC by convention
        except ValueError:
            dt = datetime.min  # noqa: DTZ901 — sorts unparseable names last
        out.append(BackupInfo(f, m.group(2), dt, f.stat().st_size))
    return sorted(out, key=lambda b: b.created_at, reverse=True)


def rotate(paths: Paths, label: str, keep: int = 10) -> list[Path]:
    same = [b for b in list_backups(paths) if b.label == label]
    deleted = []
    for b in same[keep:]:
        b.path.unlink(missing_ok=True)
        deleted.append(b.path)
    return deleted
