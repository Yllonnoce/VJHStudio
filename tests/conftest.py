import io
import subprocess
from pathlib import Path

import httpx
import pytest
from PIL import Image

from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio import config
from vjhstudio.runware import download
from vjhstudio.services import restart as restart_svc
from vjhstudio.web.app import create_app


@pytest.fixture(autouse=True, scope="session")
def never_really_exit():
    """Nothing in the suite may re-exec or kill the pytest process.

    A successful update asks for a restart from its own worker thread, and a daemon
    thread can outlive the test that started it — long enough for monkeypatch to have
    put the real `request_restart` back. Blanking the two calls that actually leave
    the process is the backstop. test_restart.py patches these per test and still
    observes its own calls."""
    real_execv, real_exit = restart_svc._execv, restart_svc._exit
    restart_svc._execv = lambda argv: None
    restart_svc._exit = lambda code: None
    yield
    restart_svc._execv, restart_svc._exit = real_execv, real_exit


@pytest.fixture
def paths(tmp_path):
    return config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "data")})


@pytest.fixture
def fake():
    return FakeRunware()


HAVE_FFMPEG = download.ffmpeg_exe() is not None


@pytest.fixture
def make_mp4():
    """Build a tiny real MP4 with the very ffmpeg the app would use for the poster.

    Hermetic: `imageio-ffmpeg` ships the binary in its wheel, so this touches no
    network and no system package. Skips when the platform has no ffmpeg at all."""
    exe = download.ffmpeg_exe()
    if exe is None:
        pytest.skip("no ffmpeg available")

    def build(dest, seconds: float = 1.0, size: str = "64x64", colour: str = "red"):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [exe, "-nostdin", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"color=c={colour}:s={size}:d={seconds:g}",
             "-pix_fmt", "yuv420p", str(dest)],
            check=True, capture_output=True,
        )  # fmt: skip
        return dest

    return build


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
