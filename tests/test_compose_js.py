"""The pure idea-chip helpers in static/js/compose.js. Like tests/test_js_mirror.py this
runs the real file under node and is skipped (not failed) when node is unavailable."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_JS = REPO_ROOT / "vjhstudio" / "web" / "static" / "js" / "compose.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(expression: str):
    script = (
        "const m = require(" + json.dumps(str(COMPOSE_JS)) + ");"
        "const {vjhToggleIdea, vjhHasIdea, vjhAutosize, composePrompt} = m;"
        "process.stdout.write(JSON.stringify(" + expression + "));"
    )
    out = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(out.stdout)


def test_toggle_idea_appends_to_an_empty_field():
    assert _run('vjhToggleIdea("", "golden hour")') == "golden hour"


def test_toggle_idea_appends_with_a_comma_space():
    assert _run('vjhToggleIdea("soft light", "golden hour")') == "soft light, golden hour"


def test_toggle_idea_removes_case_insensitively():
    assert _run('vjhToggleIdea("soft light, Golden Hour", "golden hour")') == "soft light"


def test_toggle_idea_ignores_a_blank_phrase():
    assert _run('vjhToggleIdea("soft light", "   ")') == "soft light"


def test_has_idea_compares_case_insensitively():
    assert _run('vjhHasIdea("a, b", "B")') is True
    assert _run('vjhHasIdea("a, b", "c")') is False


def test_helpers_are_exported_on_the_module_and_on_the_global():
    assert (
        _run(
            "[typeof vjhToggleIdea, typeof vjhHasIdea, typeof vjhAutosize, "
            "typeof globalThis.vjhToggleIdea, typeof globalThis.vjhHasIdea, "
            "typeof globalThis.vjhAutosize]"
        )
        == ["function"] * 6
    )


def test_compose_prompt_still_exists():
    assert _run("typeof composePrompt") == "function"
    assert _run('composePrompt({subject: "a fox", style: "oil painting"})') == (
        "a fox, oil painting"
    )


def test_autosize_is_a_no_op_without_a_textarea():
    assert _run('(vjhAutosize(null), vjhAutosize({tagName: "INPUT"}), "ok")') == "ok"
