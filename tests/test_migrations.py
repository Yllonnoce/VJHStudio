import sqlite3
from pathlib import Path
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from vjhstudio import db, models
from vjhstudio.services import migrate

def test_fresh_db_upgrades_to_head(tmp_path):
    p = tmp_path / "vjh.db"
    assert migrate.current(p) is None
    assert migrate.needs_upgrade(p)
    migrate.upgrade(p)
    assert migrate.current(p) == migrate.head()
    assert not migrate.needs_upgrade(p)
    names = {r[0] for r in sqlite3.connect(p).execute(
        "select name from sqlite_master where type='table'")}
    assert {"projects", "prompts", "catalog_models", "assets", "jobs", "outputs",
            "settings", "app_meta", "usage_entries", "alembic_version"} <= names

def test_models_match_migrations(tmp_path):
    p = tmp_path / "vjh.db"
    migrate.upgrade(p)
    engine = db.make_engine(p)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "render_as_batch": True})
        diff = compare_metadata(ctx, models.Base.metadata)
    assert diff == [], diff

def test_engine_has_wal_and_fk(tmp_path):
    p = tmp_path / "vjh.db"
    migrate.upgrade(p)
    engine = db.make_engine(p)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("pragma journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("pragma foreign_keys").scalar() == 1

def test_session_scope_commits_and_rolls_back(tmp_path):
    p = tmp_path / "vjh.db"
    migrate.upgrade(p)
    factory = db.make_session_factory(db.make_engine(p))
    with db.session_scope(factory) as s:
        s.add(models.Project(name="A", slug="a"))
    try:
        with db.session_scope(factory) as s:
            s.add(models.Project(name="B", slug="b"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.session_scope(factory) as s:
        assert [x.slug for x in s.query(models.Project).order_by(models.Project.slug)] == ["a"]
