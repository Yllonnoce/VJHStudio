import re

from vjhstudio.services.settings import SPEC
from vjhstudio.web.deps import STATIC_DIR

THEMES = SPEC["ui.theme"].choices


def test_every_theme_defines_full_token_set():
    css = (STATIC_DIR / "css" / "themes.css").read_text()
    for t in THEMES:
        block = re.search(rf'\[data-theme="{t}"\]\s*\{{(.*?)\}}', css, re.S)
        assert block, t
        for tok in (
            "--sp-bg",
            "--sp-surface",
            "--sp-accent",
            "--sp-on-accent",
            "--sp-text",
            "--sp-border",
            "--sp-shadow-1",
            "--sp-danger",
            "color-scheme",
        ):
            assert tok in block.group(1), (t, tok)


def test_theme_js_registry_matches_settings():
    js = (STATIC_DIR / "js" / "theme.js").read_text()
    for t in THEMES:
        assert f"'{t}'" in js or f'"{t}"' in js, t
    assert "vjh-theme" in js


async def test_html_carries_theme_attributes(client):
    r = await client.get("/")
    assert 'data-theme="midnight"' in r.text and 'data-scheme="dark"' in r.text
    assert "themes.css" in r.text and "theme.js" in r.text and "vjh-theme-panel" in r.text


async def test_saving_theme_changes_attribute(client):
    await client.post("/settings", data={"ui.theme": "daylight"})
    r = await client.get("/")
    assert 'data-theme="daylight"' in r.text and 'data-scheme="light"' in r.text


def test_theme_js_guards_every_storage_access():
    """localStorage throws when a browser blocks site storage; an unguarded read
    would abort the file and the swatch picker would never be built."""
    js = (STATIC_DIR / "js" / "theme.js").read_text()
    accesses = re.findall(r"^.*\blocalStorage\.\w+\(.*$", js, re.M)
    assert accesses, "theme.js no longer touches localStorage"
    for line in accesses:
        assert "try {" in line and "catch" in line, line


def test_theme_js_lets_the_server_value_win():
    """The saved ui.theme setting is the source of truth; localStorage is only
    the pre-paint fast path."""
    js = (STATIC_DIR / "js" / "theme.js").read_text()
    assert "dataset.serverTheme" in js
    assert "_vjhReconcileTheme" in js
    # the old clobbering default must not come back
    assert "localStorage.getItem('vjh-theme') || " not in js


async def test_html_carries_server_theme_attribute(client):
    r = await client.get("/")
    assert 'data-server-theme="midnight"' in r.text
    await client.post("/settings", data={"ui.theme": "daylight"})
    r = await client.get("/")
    assert 'data-server-theme="daylight"' in r.text
