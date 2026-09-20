import json
import re
from datetime import datetime

import httpx
import pytest
from PIL import Image

from vjhstudio.runware import download
from vjhstudio.runware.results import ResultItem


def _png_bytes() -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _transport(fail_first: int = 0):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= fail_first:
            raise httpx.ConnectError("flaky")
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

    return httpx.MockTransport(handler), calls


def test_stem_format():
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", download.new_stem())


def test_stem_uses_local_time():
    stem = download.new_stem(datetime(2026, 9, 20, 15, 4, 5))
    assert stem.startswith("20260920-150405-")


async def test_download_writes_file_and_retries(tmp_path):
    t, calls = _transport(fail_first=1)
    items = [ResultItem("http://x/a.png", 1, 0.01, "u", False, {})]
    saved = await download.download_items(items, tmp_path, "png", transport=t)
    assert saved[0].path.exists() and saved[0].path.suffix == ".png" and saved[0].size > 100
    assert not list(tmp_path.glob("*.part")) and calls["n"] == 2


async def test_download_gives_up(tmp_path):
    t, _ = _transport(fail_first=10)
    with pytest.raises(download.DownloadError):
        await download.download_items(
            [ResultItem("http://x/a.png", None, None, None, False, {})],
            tmp_path,
            "png",
            transport=t,
        )


async def test_download_cleans_up_part_on_failure(tmp_path):
    t, _ = _transport(fail_first=10)
    with pytest.raises(download.DownloadError):
        await download.download_items(
            [ResultItem("http://x/a.png", None, None, None, False, {})],
            tmp_path,
            "png",
            transport=t,
        )
    assert not list(tmp_path.glob("*.part"))


def test_sidecar_and_thumbnail(tmp_path):
    img = tmp_path / "20260920-120000-abcdef.png"
    Image.new("RGB", (800, 600), (0, 0, 255)).save(img)
    side = download.write_sidecar(img, {"prompt": "x", "seed": 1})
    assert side.name == "20260920-120000-abcdef.json" and json.loads(side.read_text())["seed"] == 1
    thumb = download.make_thumbnail(img, tmp_path / "t.jpg")
    with Image.open(thumb) as im:
        assert max(im.size) == 384 and im.format == "JPEG"
    assert download.make_thumbnail(tmp_path / "missing.png", tmp_path / "t2.jpg") is None
