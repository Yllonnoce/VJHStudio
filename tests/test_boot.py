import pytest
from runwarestudio import boot, config, db, models
from runwarestudio.services import backup, migrate


@pytest.fixture
def paths(tmp_path):
    return config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path)})


def test_first_boot_creates_schema_default_project_and_meta(paths):
    info = boot.boot(paths)
    assert info.schema_revision == migrate.head()
    assert paths.db.exists() and paths.outputs.is_dir()
    with db.session_scope(info.session_factory) as s:
        assert s.query(models.Project).filter_by(slug="default").one().name == "Default"
        assert s.get(models.AppMeta, "schema_revision").value == migrate.head()
        assert s.get(models.AppMeta, "first_boot_at") is not None
    assert backup.list_backups(paths) == []  # fresh DB: nothing to back up


def test_orphan_and_requeue(paths):
    info = boot.boot(paths)
    with db.session_scope(info.session_factory) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
        s.add(models.Job(id="run1", project_id=pid, kind="image", status="running", model_air="m", request_json={}))
        s.add(models.Job(id="q1", project_id=pid, kind="image", status="queued", model_air="m", request_json={}))
    info2 = boot.boot(paths)
    assert info2.orphaned_jobs == 1 and info2.requeued_jobs == ["q1"]
    with db.session_scope(info2.session_factory) as s:
        j = s.get(models.Job, "run1")
        assert j.status == "failed" and j.error_code == "orphaned"


def test_backup_taken_when_schema_behind(paths, monkeypatch):
    boot.boot(paths)
    monkeypatch.setattr(migrate, "needs_upgrade", lambda p: True)
    monkeypatch.setattr(migrate, "upgrade", lambda p, revision="head": None)
    boot.boot(paths)
    assert [b.label for b in backup.list_backups(paths)] == ["pre-migrate"]


def test_migration_failure_propagates(paths, monkeypatch):
    def bad(p, revision="head"):
        raise migrate.MigrationFailed("nope")
    monkeypatch.setattr(migrate, "upgrade", bad)
    with pytest.raises(migrate.MigrationFailed):
        boot.boot(paths)
