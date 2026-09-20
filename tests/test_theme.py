import re
from pathlib import Path
from vjhstudio.web.deps import STATIC_DIR
from vjhstudio.services.settings import SPEC

THEMES = SPEC["ui.theme"].choices

def test_every_theme_defines_full_token_set():
    css = (STATIC_DIR / "css" / "themes.css").read_text()
    for t in THEMES:
        block = re.search(r'\[data-theme="%s"\]\s*\{(.*?)\}' % t, css, re.S)
        assert block, t
        for tok in ("--sp-bg", "--sp-surface", "--sp-accent", "--sp-on-accent", "--sp-text", "--sp-border", "--sp-shadow-1", "--sp-danger", "color-scheme"):
            assert tok in block.group(1), (t, tok)

def test_theme_js_registry_matches_settings():
    js = (STATIC_DIR / "js" / "theme.js").read_text()
    for t in THEMES:
        assert "'%s'" % t in js or '"%s"' % t in js, t
    assert "vjh-theme" in js

async def test_html_carries_theme_attributes(client):
    r = await client.get("/")
    assert 'data-theme="midnight"' in r.text and 'data-scheme="dark"' in r.text
    assert "themes.css" in r.text and "theme.js" in r.text and "vjh-theme-panel" in r.text

async def test_saving_theme_changes_attribute(client):
    await client.post("/settings", data={"ui.theme": "daylight"})
    r = await client.get("/")
    assert 'data-theme="daylight"' in r.text and 'data-scheme="light"' in r.text
