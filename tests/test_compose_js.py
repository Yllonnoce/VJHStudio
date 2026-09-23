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
        "const {vjhToggleIdea, vjhSetIdea, vjhHasIdea, vjhAutosize, composePrompt} = m;"
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


def test_set_idea_replaces_whatever_the_field_held():
    """Style is single-select: the media are mutually exclusive, so a chip overwrites."""
    assert _run('vjhSetIdea("", "anime")') == "anime"
    assert _run('vjhSetIdea("oil painting", "anime")') == "anime"
    assert _run('vjhSetIdea("oil painting, watercolour", "anime")') == "anime"


def test_set_idea_clears_the_field_when_the_active_chip_is_clicked_again():
    assert _run('vjhSetIdea("anime", "anime")') == ""
    assert _run('vjhSetIdea("  Anime ", "anime")') == ""


def test_set_idea_ignores_a_blank_phrase():
    assert _run('vjhSetIdea("anime", "   ")') == "anime"


def test_has_idea_compares_case_insensitively():
    assert _run('vjhHasIdea("a, b", "B")') is True
    assert _run('vjhHasIdea("a, b", "c")') is False


def test_helpers_are_exported_on_the_module_and_on_the_global():
    assert (
        _run(
            "[typeof vjhToggleIdea, typeof vjhSetIdea, typeof vjhHasIdea, "
            "typeof vjhAutosize, typeof globalThis.vjhToggleIdea, "
            "typeof globalThis.vjhSetIdea, typeof globalThis.vjhHasIdea, "
            "typeof globalThis.vjhAutosize]"
        )
        == ["function"] * 8
    )


def test_compose_prompt_still_exists():
    assert _run("typeof composePrompt") == "function"
    assert _run('composePrompt({subject: "a fox", style: "oil painting"})') == (
        "a fox, oil painting"
    )


def test_autosize_is_a_no_op_without_a_textarea():
    assert _run('(vjhAutosize(null), vjhAutosize({tagName: "INPUT"}), "ok")') == "ok"


# `field-sizing: content` beats nothing except an inline `height`, so vjhAutosize -- the
# fallback that writes exactly that -- must only ever be reached for a real text area.
# app.js additionally skips it wholesale where the browser supports `field-sizing`
# (see test_autosize_is_skipped_where_field_sizing_is_supported in test_prompt_ideas.py).
def test_autosize_never_writes_a_height_on_a_non_textarea():
    fake = '{tagName: "INPUT", style: {height: "7px"}, scrollHeight: 999}'
    assert _run("(function(){var e = " + fake + "; vjhAutosize(e); return e.style.height;})()") == (
        "7px"
    )


def test_autosize_grows_a_textarea_to_its_content_capped_at_twelve_lines():
    stub = "globalThis.getComputedStyle = function () { return {lineHeight: '20px'}; };"
    grew = _run(
        "(function(){" + stub + "var e = {tagName: 'TEXTAREA', style: {}, scrollHeight: 100};"
        "vjhAutosize(e); return e.style.height;})()"
    )
    capped = _run(
        "(function(){" + stub + "var e = {tagName: 'TEXTAREA', style: {}, scrollHeight: 4000};"
        "vjhAutosize(e); return e.style.height;})()"
    )
    assert grew == "100px"
    assert capped == "240px"  # 12 lines x 20px


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_step_value_honours_min_max_step_and_decimals():
    """vjhStepValue drives the big − / + buttons that replace the tiny native spinner."""
    import json
    import subprocess

    app_js = REPO_ROOT / "vjhstudio" / "web" / "static" / "js" / "app.js"
    src = app_js.read_text()
    fn = src[src.index("function vjhStepValue(") : src.index("function vjhEnhanceSteppers(")]
    script = (
        fn
        + """
      console.log(JSON.stringify([
        vjhStepValue('5', '1', 1, '3', '15'), vjhStepValue('15', '1', 1, '3', '15'), vjhStepValue('3', '1', -1, '3', '15'),
        vjhStepValue('', '1', 1, '3', '15'), vjhStepValue('3.5', '0.5', 1, '0', '20'), vjhStepValue('0.1', '0.1', 1, null, null),
        vjhStepValue('1024', '64', -1, '128', '2048'),
      ]));
    """
    )
    got = json.loads(
        subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    )
    assert got == ["6", "15", "3", "3", "4", "0.2", "960"]


def test_steppers_are_wired_on_load_and_after_swaps():
    src = (REPO_ROOT / "vjhstudio" / "web" / "static" / "js" / "app.js").read_text()
    body = src.split("function vjhEnhanceSteppers(", 1)[1]
    assert 'input[type="number"]:not([data-stepper])' in body
    assert (
        "new Event('input', { bubbles: true })" in body
        and "new Event('change', { bubbles: true })" in body
    )
    assert "htmx:afterSettle" in body and "vjhEnhanceSteppers(e.target || document)" in body
    css = (REPO_ROOT / "vjhstudio" / "web" / "static" / "css" / "app.css").read_text()
    assert ".stepper-btn{flex:0 0 2.75rem;width:2.75rem;min-height:42px" in css
    assert "::-webkit-inner-spin-button{-webkit-appearance:none" in css
