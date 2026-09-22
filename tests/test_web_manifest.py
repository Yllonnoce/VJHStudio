"""Add to Home Screen: the web app manifest, its icons, and the head that names them.

Checked end to end rather than by reading files: served with the right content type,
valid JSON, and every icon the manifest names really resolves to a PNG of the size it
claims.
"""

import io
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "vjhstudio" / "web" / "static"
BASE_HTML = (ROOT / "vjhstudio" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
MANIFEST = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))


async def test_manifest_is_served_as_a_manifest(client):
    r = await client.get("/static/manifest.webmanifest")
    assert r.status_code == 200
    # text/plain (what mimetypes falls back to) is enough for Chrome to ignore it
    assert r.headers["content-type"].startswith("application/manifest+json")
    assert r.json()["name"] == "VJHStudio"


def test_manifest_says_what_an_installed_app_needs():
    assert MANIFEST["short_name"] == "VJHStudio"
    assert MANIFEST["start_url"] == "/"
    assert MANIFEST["scope"] == "/"
    assert MANIFEST["display"] == "standalone"
    # straight from [data-theme="midnight"] in themes.css
    themes = (STATIC / "css" / "themes.css").read_text(encoding="utf-8")
    midnight = themes[themes.index('[data-theme="midnight"]') :]
    midnight = midnight[: midnight.index("}")]
    assert MANIFEST["background_color"] in midnight
    assert MANIFEST["theme_color"] in midnight
    assert {i["sizes"] for i in MANIFEST["icons"]} == {"192x192", "512x512"}


@pytest.mark.parametrize("size", [192, 512])
async def test_every_icon_the_manifest_names_resolves(client, size):
    icon = next(i for i in MANIFEST["icons"] if i["sizes"] == f"{size}x{size}")
    r = await client.get(icon["src"])
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(r.content)).size == (size, size)
    assert icon["type"] == "image/png"


async def test_apple_touch_icon_is_a_180px_png_without_transparency(client):
    """iOS draws its own rounded mask on an opaque icon; an alpha channel there
    comes out black at the corners on some versions."""
    r = await client.get("/static/img/apple-touch-icon.png")
    assert r.status_code == 200
    im = Image.open(io.BytesIO(r.content))
    assert im.size == (180, 180)
    assert im.mode == "RGB"


async def test_the_page_head_points_at_all_of_it(client):
    r = await client.get("/")
    for line in (
        '<link rel="manifest" href="/static/manifest.webmanifest">',
        '<meta name="theme-color" content="#00e5c8">',
        '<link rel="apple-touch-icon" href="/static/img/apple-touch-icon.png">',
        '<meta name="apple-mobile-web-app-capable" content="yes">',
        '<meta name="apple-mobile-web-app-title" content="VJHStudio">',
    ):
        assert line in r.text, line
    assert '<meta name="theme-color"' in BASE_HTML


def test_theme_color_follows_the_chosen_theme():
    theme_js = (STATIC / "js" / "theme.js").read_text(encoding="utf-8")
    assert 'meta[name="theme-color"]' in theme_js
