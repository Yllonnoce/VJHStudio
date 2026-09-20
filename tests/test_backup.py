import sqlite3

import pytest

from vjhstudio import config
from vjhstudio.services import backup, migrate


@pytest.fixture
def paths(tmp_path):
    p = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    config.ensure_dirs(p)
    migrate.upgrade(p.db)
    return p


def test_backup_is_consistent_copy(paths):
    sqlite3.connect(paths.db).execute(
        "insert into projects(name,slug,is_archived,created_at,updated_at) "
        "values('A','a',0,'2026-01-01','2026-01-01')"
    ).connection.commit()
    out = backup.backup_db(paths, "manual")
    assert (
        out.parent == paths.backups
        and out.name.startswith("vjh-")
        and out.name.endswith("-manual.db")
    )
    assert sqlite3.connect(out).execute("select count(*) from projects").fetchone()[0] == 1


def test_backup_without_db_raises(tmp_path):
    p = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "x")})
    config.ensure_dirs(p)
    with pytest.raises(FileNotFoundError):
        backup.backup_db(p, "manual")


def test_rotate_keeps_newest_per_label(paths):
    made = []
    for i in range(4):
        f = paths.backups / f"vjh-2026010{i}-000000-manual.db"
        f.write_bytes(b"x")
        made.append(f)
    (paths.backups / "vjh-20260101-000000-pre-update.db").write_bytes(b"y")
    deleted = backup.rotate(paths, "manual", keep=2)
    assert sorted(d.name for d in deleted) == [made[0].name, made[1].name]
    assert (paths.backups / "vjh-20260101-000000-pre-update.db").exists()
    infos = backup.list_backups(paths)
    assert {i.label for i in infos} == {"manual", "pre-update"}
