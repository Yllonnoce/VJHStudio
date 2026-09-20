import pytest
from runwarestudio import db, models
from runwarestudio.services import migrate, settings, meta

@pytest.fixture
def session(tmp_path):
    p = tmp_path / "s.db"; migrate.upgrade(p)
    factory = db.make_session_factory(db.make_engine(p))
    with db.session_scope(factory) as s:
        yield s

def test_defaults(session):
    assert settings.get(session, "jobs.concurrency") == 3
    assert settings.get(session, "ui.theme") == "dark"

def test_set_and_get_casts(session):
    settings.set_many(session, {"jobs.concurrency": "5", "ui.theme": "light"})
    assert settings.get(session, "jobs.concurrency") == 5
    assert settings.get(session, "ui.theme") == "light"

def test_invalid_values_rejected(session):
    with pytest.raises(ValueError): settings.set_many(session, {"jobs.concurrency": "x"})
    with pytest.raises(ValueError): settings.set_many(session, {"ui.theme": "sepia"})
    with pytest.raises(ValueError): settings.set_many(session, {"nope": "1"})

def test_env_precedence(session):
    settings.set_many(session, {"runware.transport": "websocket"})
    assert settings.get(session, "runware.transport", env={"RUNWARESTUDIO_TRANSPORT": "rest"}) == "rest"

def test_meta_round_trip(session):
    assert meta.get(session, "x") is None
    meta.set(session, "x", "1"); meta.set(session, "x", "2")
    assert meta.get(session, "x") == "2"
