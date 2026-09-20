import io

import httpx
import pytest
from PIL import Image

from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio import config
from vjhstudio.web.app import create_app


@pytest.fixture
def paths(tmp_path):
    return config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "data")})


@pytest.fixture
def fake():
    return FakeRunware()


def _png_bytes(size: int = 64) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (size, size), (1, 2, 3)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def download_transport():
    """Every output URL the runner fetches in tests resolves to a tiny in-memory PNG."""
    png = _png_bytes()
    return httpx.MockTransport(lambda request: httpx.Response(200, content=png))


@pytest.fixture
def app(paths, fake, download_transport):
    return create_app(
        paths,
        client_factory=fake_factory(fake),
        env={},
        auto_refresh=False,
        download_transport=download_transport,
    )


@pytest.fixture
async def client(app):
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c
