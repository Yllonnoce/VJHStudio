import pytest

from vjhstudio import boot, config, db, models
from vjhstudio.services import backup, maintenance


@pytest.fixture
def booted(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    with db.session_scope(info.session_factory) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
        s.add(models.Project(name="Other", slug="other"))
        s.add(models.Job(id="j1", project_id=pid, kind="image", status="succeeded", model_air="m", request_json={}))
        s.add(models.Setting(key="ui.theme", value="ocean"))
    return paths, info

def test_clear_with_backup(booted):
    paths, info = booted
    res = maintenance.clear_database(info.session_factory, paths, backup_first=True)
    assert res.backup_path and res.backup_path.name.endswith("-pre-clear.db")
    assert res.rows_deleted["jobs"] == 1 and res.rows_deleted["projects"] == 2
    with db.session_scope(info.session_factory) as s:
        assert [p.slug for p in s.query(models.Project)] == ["default"]
        assert s.query(models.Job).count() == 0
        assert s.get(models.Setting, "ui.theme").value == "ocean"  # settings survive
        assert s.get(models.AppMeta, "cleared_at") is not None
        assert s.get(models.AppMeta, "schema_revision") is not None
    assert [b.label for b in backup.list_backups(paths)] == ["pre-clear"]

def test_clear_without_backup(booted):
    paths, info = booted
    res = maintenance.clear_database(info.session_factory, paths, backup_first=False)
    assert res.backup_path is None and backup.list_backups(paths) == []
