import pytest

from vjhstudio import db
from vjhstudio.services import meta, migrate, settings


@pytest.fixture
def session(tmp_path):
    p = tmp_path / "s.db"
    migrate.upgrade(p)
    factory = db.make_session_factory(db.make_engine(p))
    with db.session_scope(factory) as s:
        yield s


def test_defaults(session):
    assert settings.get(session, "jobs.concurrency") == 3
    assert settings.get(session, "ui.theme") == "midnight"


def test_set_and_get_casts(session):
    settings.set_many(session, {"jobs.concurrency": "5", "ui.theme": "daylight"})
    assert settings.get(session, "jobs.concurrency") == 5
    assert settings.get(session, "ui.theme") == "daylight"


def test_invalid_values_rejected(session):
    with pytest.raises(ValueError):
        settings.set_many(session, {"jobs.concurrency": "x"})
    with pytest.raises(ValueError):
        settings.set_many(session, {"ui.theme": "sepia"})
    with pytest.raises(ValueError):
        settings.set_many(session, {"nope": "1"})


def test_env_precedence(session):
    settings.set_many(session, {"runware.transport": "websocket"})
    assert settings.get(session, "runware.transport", env={"VJHSTUDIO_TRANSPORT": "rest"}) == "rest"


def test_meta_round_trip(session):
    assert meta.get(session, "x") is None
    meta.set(session, "x", "1")
    meta.set(session, "x", "2")
    assert meta.get(session, "x") == "2"


def test_invalid_env_override_warns_and_falls_back(session, caplog):
    """A typo in an env var must not turn the settings page into a 500."""
    settings.set_many(session, {"runware.transport": "websocket"})
    with caplog.at_level("WARNING"):
        assert (
            settings.get(session, "runware.transport", env={"VJHSTUDIO_TRANSPORT": "grpc"})
            == "websocket"
        )
    assert "VJHSTUDIO_TRANSPORT" in caplog.text
    # with nothing saved, the SPEC default is the fallback
    assert (
        settings.all_values(session, env={"VJHSTUDIO_TRANSPORT": "grpc"})["runware.transport"]
        == "websocket"
    )


def test_transport_defaults_to_websocket(session):
    assert settings.get(session, "runware.transport") == "websocket"
