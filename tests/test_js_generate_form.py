"""Source-level checks for Task 5's polish cards, save-prompt dialog and draft guard --
same convention as tests/test_js_mirror.py: a `node --check` (and a couple of node-run
assertions for the pure helper) when node is available, skipped otherwise, plus plain
Python regex/substring checks on the JS/HTML source that need no interpreter at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS = REPO_ROOT / "vjhstudio" / "web" / "static" / "js" / "app.js"
COMPOSE_JS = REPO_ROOT / "vjhstudio" / "web" / "static" / "js" / "compose.js"
POLISH_RESULTS_HTML = (
    REPO_ROOT / "vjhstudio" / "web" / "templates" / "generate" / "_polish_results.html"
)
REFS_HTML = REPO_ROOT / "vjhstudio" / "web" / "templates" / "generate" / "_refs.html"

NODE = shutil.which("node")


def _app_js() -> str:
    return APP_JS.read_text()


# ---- node --check ----------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_app_js_is_syntactically_valid():
    subprocess.run([NODE, "--check", str(APP_JS)], check=True, timeout=30)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_compose_js_is_syntactically_valid():
    subprocess.run([NODE, "--check", str(COMPOSE_JS)], check=True, timeout=30)


# ---- vjhChoosePolish (pure helper, node-run) --------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_choose_polish_merges_the_blob_with_the_clicked_cards_data_attrs():
    blob = json.dumps(
        {
            "source": "polish",
            "mode": "promptEnhance",
            "model": "runware:llama-3-1-8b@prompt-enhancer",
            "versions": ["a fox, refined", "a fox, alt"],
            "chosen_index": None,
            "cost": 0.0004,
        }
    )
    script = (
        "const {vjhChoosePolish} = require(" + json.dumps(str(COMPOSE_JS)) + ");"
        "const out = vjhChoosePolish(" + json.dumps(blob) + ", 1, 'a fox, alt', "
        "'promptEnhance', 'runware:llama-3-1-8b@prompt-enhancer');"
        "process.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    out = json.loads(result.stdout)
    assert out["text"] == "a fox, alt"
    stored = json.loads(out["polishJson"])
    assert stored == {
        "source": "promptEnhance",
        "model": "runware:llama-3-1-8b@prompt-enhancer",
        "versions": ["a fox, refined", "a fox, alt"],
        "chosen_index": 1,
        "cost": 0.0004,
    }


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_choose_polish_tolerates_a_missing_or_malformed_blob():
    script = (
        "const {vjhChoosePolish} = require(" + json.dumps(str(COMPOSE_JS)) + ");"
        "const out = vjhChoosePolish('not json', 0, 'a fox', 'promptEnhance', '');"
        "process.stdout.write(JSON.stringify(out));"
    )
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    out = json.loads(result.stdout)
    assert out["text"] == "a fox"
    stored = json.loads(out["polishJson"])
    assert stored == {
        "source": "promptEnhance",
        "model": "",
        "versions": [],
        "chosen_index": 0,
        "cost": 0,
    }


# ---- draft guard -------------------------------------------------------------------


def test_load_draft_runs_only_when_no_form_prompt_id_or_remix():
    src = _app_js()
    idx = src.index("this._loadDraft();")
    window = src[max(0, idx - 300) : idx]
    for needle in ("initial.form", "initial.prompt_id", "initial.remix"):
        assert needle in window, f"draft guard missing {needle}"
    assert "!(" in window, "expected a negated (initial.form || ...) guard"


def test_loading_a_prompt_clears_the_stale_draft():
    src = _app_js()
    idx = src.index("else if (initial.prompt_id)")
    window = src[idx : idx + 100]
    assert "_clearDraft" in window


def test_save_draft_never_persists_prompt_id_or_polish_json():
    src = _app_js()
    start = src.index("_saveDraft() {")
    end = src.index("\n    },", start)
    body = src[start:end]
    assert "promptId" not in body
    assert "polishJson" not in body


def test_manual_field_edit_clears_prompt_id():
    src = _app_js()
    idx = src.index("$watch('fields'")
    idx2 = src.index("$watch('fields'", idx + 1)
    window = src[idx2 : idx2 + 120]
    assert "this.promptId = ''" in window


# ---- usePolish + delegated click ---------------------------------------------------


def test_use_polish_assigns_final_prompt_and_polish_json():
    src = _app_js()
    start = src.index("usePolish(detail)")
    end = src.index("\n    },", start)
    body = src[start:end]
    assert "this.finalPrompt" in body
    assert "this.polishJson" in body


def test_polish_click_is_delegated_and_reads_polish_json_script():
    src = _app_js()
    assert "'.use-polish'" in src or '".use-polish"' in src
    assert "getElementById('polish-results')" in src
    assert "getElementById('polish-json')" in src
    assert "vjhChoosePolish" in src
    assert "'use-polish'" in src  # the CustomEvent name generateForm listens for


def test_use_polish_event_is_wired_in_init():
    src = _app_js()
    assert "addEventListener('use-polish'" in src
    assert "this.usePolish(" in src


# ---- prompt-saved listener ----------------------------------------------------------


def test_prompt_saved_listener_assigns_prompt_id_and_toasts():
    src = _app_js()
    idx = src.index("'prompt-saved'")
    window = src[idx : idx + 600]
    assert "this.promptId" in window
    assert "vjhToast" in window
    assert "vjhUnwrapTrigger" in window


# ---- polish_results.html card markup -------------------------------------------------


def test_polish_results_cards_carry_the_expected_data_attrs():
    html = POLISH_RESULTS_HTML.read_text()
    assert 'id="polish-results"' in html
    assert 'class="use-polish"' in html
    for attr in ("data-text", "data-index", "data-mode", "data-model"):
        assert attr in html, attr
    assert "Use this" in html
    assert 'id="polish-json"' in html


# ---- route: the page always carries exactly one save-prompt dialog and the polish
# results placeholder (functional coverage of the two ids the brief calls out; the full
# "outside the mode fieldsets" assertion already lives in test_web_generate.py) --------


async def test_generate_page_has_one_save_prompt_dialog_and_the_polish_panel(client):
    r = await client.get("/generate")
    assert r.status_code == 200
    html = r.text
    assert html.count('id="save-prompt"') == 1
    assert 'id="polish-results"' in html


# ---- uploadRef: the Generate page's own upload path ----------------------------------


def test_upload_ref_posts_the_file_to_assets_upload_and_asks_for_json():
    src = _app_js()
    idx = src.index("async uploadRef(role, input)")
    window = src[idx : idx + 1400]
    assert "fetch('/assets/upload'" in window
    assert "method: 'POST'" in window
    assert "Accept: 'application/json'" in window
    assert "new FormData()" in window and "fd.append('files', file)" in window
    # the stored asset becomes a chip through the same addRef() the picker feeds
    assert "this.addRef({ id: asset.id" in window and "thumb: asset.thumb_url" in window
    assert "role }" in window
    # failures land in the existing toast strip with the server's own message
    assert "vjhToast((body && body.error)" in window
    assert "input.value = ''" in window  # re-picking the same file must still fire


def test_needs_first_frame_is_read_off_the_swapped_params_panel():
    src = _app_js()
    idx = src.index("_syncNeedsFirstFrame() {")
    window = src[idx : idx + 400]
    assert "document.getElementById('model-params')" in window
    assert "panel.dataset.needsFirstFrame === 'true'" in window
    assert "needsFirstFrame: false," in src  # declared as state, so x-show is reactive
    assert "document.addEventListener('htmx:afterSwap', () => this._syncNeedsFirstFrame());" in src


def test_refs_template_loops_every_role_through_upload_ref():
    """The four roles are a Jinja loop (the rendered per-role markup is asserted in
    tests/test_web_generate.py); this only pins the wiring the loop emits."""
    html = REFS_HTML.read_text()
    assert "uploadRef('{{ role }}', $event.target)" in html
    assert "('first', 'first frame')" in html and "('seed', 'seed image')" in html
    assert 'type="file"' in html and 'accept="image/*"' in html
    assert "x-show=\"mode === 'video' && needsFirstFrame\"" in html
