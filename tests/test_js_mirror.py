"""static/js/compose.js must mirror services/prompts.py byte-for-byte. The node run is
skipped (not failed) when node is unavailable, so CI without node still passes."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vjhstudio.services.prompts import compose

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "compose_cases.json"
COMPOSE_JS = REPO_ROOT / "vjhstudio" / "web" / "static" / "js" / "compose.js"
BASE_HTML = REPO_ROOT / "vjhstudio" / "web" / "templates" / "base.html"

NODE = shutil.which("node")


def _cases() -> list[dict]:
    return json.loads(FIXTURES.read_text())


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_compose_prompt_js_matches_python_for_every_fixture_case():
    cases = _cases()
    script = (
        "const {composePrompt} = require(" + json.dumps(str(COMPOSE_JS)) + ");"
        "const cases = " + json.dumps(cases) + ";"
        "process.stdout.write(JSON.stringify(cases.map(c => composePrompt(c.form))));"
    )
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    js_results = json.loads(result.stdout)
    assert len(js_results) == len(cases)
    for case, js_composed in zip(cases, js_results, strict=True):
        assert js_composed == case["composed"]
        assert js_composed == compose(case["form"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_build_negative_js_dedupes_case_insensitively_and_respects_flags():
    script = (
        "const {buildNegative} = require(" + json.dumps(str(COMPOSE_JS)) + ");"
        "const out = buildNegative('blurry, Blurry', 'watermark', true, true, 'text, Watermark');"
        "process.stdout.write(out);"
    )
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    # user token first, then default (deduped against a later case-variant), then no-text tokens
    assert result.stdout == "blurry, watermark, text"


def test_base_html_loads_compose_before_app_and_sets_data_notify():
    html = BASE_HTML.read_text()
    html_tag = html.split("<html", 1)[1].split(">", 1)[0]
    assert "data-notify=" in html_tag
    compose_pos = html.index("static/js/compose.js")
    app_pos = html.index("static/js/app.js")
    assert compose_pos < app_pos
