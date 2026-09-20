import pytest, httpx
from vjhstudio import config
from vjhstudio.web.app import create_app
from tests.fakes.fake_runware import FakeRunware, fake_factory

@pytest.fixture
def paths(tmp_path):
    return config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "data")})

@pytest.fixture
def fake():
    return FakeRunware()

@pytest.fixture
def app(paths, fake):
    return create_app(paths, client_factory=fake_factory(fake), env={})

@pytest.fixture
async def client(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c
