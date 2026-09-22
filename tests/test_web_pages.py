"""Global page polish: page heads, active navigation, inline buttons, compact rows.

These are the invariants that make every page look like the same app: one
`<header class="page-head">` per page, exactly one nav link marked as current,
the Models actions on one row, and the theme bridge out-ranking Pico's own
light palette so a named dark theme is actually dark.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "vjhstudio" / "web"
APP_CSS = (WEB / "static" / "css" / "app.css").read_text(encoding="utf-8")
PICO_CSS = (WEB / "static" / "vendor" / "pico.min.css").read_text(encoding="utf-8")
HEADER_HTML = (WEB / "templates" / "_header.html").read_text(encoding="utf-8")
BASE_HTML = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
TEMPLATES = sorted((WEB / "templates").rglob("*.html"))

PAGES = [
    "/",
    "/generate/image",
    "/generate/video",
    "/gallery",
    "/prompts",
    "/assets",
    "/projects",
    "/models",
    "/settings",
]

PROMPT_FORM = {
    "project_id": "1",
    "mode": "image",
    "subject": "a red fox",
    "final_prompt": "a red fox, oil painting",
    "title": "Fox portrait",
    "tags": "wildlife",
}


def _block(html: str, opener: str, tag: str = "div") -> str:
    """The inner HTML of the element that starts at `opener`, brace-matched over
    `<tag …>` / `</tag>` so a nested element does not end the match early."""
    start = html.index(opener)
    body = html.index(">", start) + 1
    depth, i = 1, body
    token = re.compile(rf"<{tag}\b|</{tag}>")
    while depth:
        m = token.search(html, i)
        assert m, f"unclosed <{tag}> after {opener!r}"
        depth += -1 if m.group(0).startswith("</") else 1
        i = m.end()
    return html[body : i - len(f"</{tag}>")]


# ── Page heads ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", PAGES)
async def test_every_page_opens_with_one_page_head(client, path):
    r = await client.get(path)
    assert r.status_code == 200, path
    assert r.text.count('class="page-head"') == 1, path
    head = _block(r.text, '<header class="page-head"', "header")
    assert re.search(r"<h1>\s*\S.*?</h1>", head, re.S), path
    assert re.search(r'<p class="lead">\s*\S.*?</p>', head, re.S), path


async def test_page_head_style_is_defined_once(client):
    """Task 2 introduced `.page-head`; every later page extends that one rule."""
    assert APP_CSS.count(".page-head{") == 1
    assert APP_CSS.count(".page-head h1{") == 1


async def test_no_page_still_promises_later_phases(client):
    for t in TEMPLATES:
        assert "arrive in the next phases" not in t.read_text(encoding="utf-8"), t.name


# ── Active navigation ─────────────────────────────────────────────────────

CURRENT = [
    ("/", "Home"),
    ("/generate/image", "Generate"),
    ("/generate/video", "Generate"),
    ("/gallery", "Gallery"),
    ("/prompts", "Prompts"),
    ("/assets", "Assets"),
    ("/projects", "Projects"),
    ("/models", "Models"),
    ("/settings", "Settings"),
]


@pytest.mark.parametrize("path,label", CURRENT)
async def test_nav_marks_exactly_the_current_page(client, path, label):
    r = await client.get(path)
    nav = _block(r.text, "<nav", "nav")
    marked = re.findall(r'<a [^>]*aria-current="page"[^>]*>([^<]*)</a>', nav)
    assert [m.strip() for m in marked] == [label], (path, nav)


async def test_current_nav_link_is_styled_with_the_accent():
    assert 'header nav a[aria-current="page"]' in APP_CSS


# ── Buttons ───────────────────────────────────────────────────────────────


async def test_buttons_are_inline_width_unless_opted_in():
    """Pico's `button[type=submit]` is full-width at specificity 0,1,1, so the
    override has to repeat the attribute; a bare `button` selector would lose."""
    m = re.search(r"([^{}\n]*button\[type=\"?submit\"?\][^{}\n]*)\{([^}]*)\}", APP_CSS)
    assert m, "nothing out-ranks Pico's full-width button[type=submit]"
    assert "width:auto" in m.group(2).replace(" ", "")
    selector = m.group(1).replace('"', "")
    assert "[role=button]" in selector and "input[type=submit]" in selector
    assert re.search(r"\.gen-submit[^{]*\{[^}]*width:\s*100%", APP_CSS)


async def test_models_actions_share_one_row_with_status_beneath(client):
    r = await client.get("/models")
    actions = _block(r.text, '<div class="actions')
    assert "Refresh prices" in actions and "Harvest constraints" in actions
    assert actions.index("Refresh prices") < actions.index("Harvest constraints")
    # the status lines stay with their button, below it
    assert "Last refreshed:" in actions


async def test_model_rows_use_compact_buttons(client):
    r = await client.get("/models")
    assert r.status_code == 200
    row = (WEB / "templates" / "catalog" / "_row.html").read_text(encoding="utf-8")
    assert row.count("btn-sm") == 2


# ── Prompt rows ───────────────────────────────────────────────────────────


async def test_prompt_row_save_tags_is_small_and_inline(client):
    assert (await client.post("/prompts", data=PROMPT_FORM)).status_code == 200
    r = await client.get("/prompts")
    row = _block(r.text, '<article class="prompt-row"', "article")
    assert 'class="block"' not in row
    tag_row = _block(row, '<form class="tag-row', "form")
    assert '<input type="text" name="tags"' in tag_row
    save = re.search(r"<button[^>]*>\s*Save tags\s*</button>", tag_row)
    assert save and "btn-sm" in save.group(0)
    assert _block(row, '<div class="actions').count("Load into form") == 1


async def test_tag_row_lays_the_input_and_button_on_one_line():
    assert re.search(r"\.tag-row\{[^}]*display:flex", APP_CSS)


# ── One `.card-head` contract ─────────────────────────────────────────────


async def test_card_head_is_defined_once_and_wraps_its_hint():
    """The dashboard wanted `justify-content:space-between` (title left, link right);
    the builder wants its hint under the title. One rule serves both because the hint
    takes a full flex line of its own."""
    assert APP_CSS.count(".card-head{") == 1
    rule = re.search(r"\.card-head\{([^}]*)\}", APP_CSS).group(1)
    assert "display:flex" in rule and "flex-wrap:wrap" in rule
    assert "justify-content:space-between" in rule
    assert re.search(r"\.card-head \.hint\{[^}]*flex-basis:100%", APP_CSS)


# ── Header nav at 390 px ──────────────────────────────────────────────────


async def test_nav_wraps_instead_of_overflowing_on_a_phone():
    """No DOM harness here, so this guards the two things that make the wrap
    possible; the real layout is checked by the 390 px screenshots in the task
    report (t4-index-390-midnight.png, t4-generate-video-390-midnight.png)."""
    assert re.search(r"header nav,\s*header nav > ul\{[^}]*flex-wrap:wrap", APP_CSS)
    assert "@media (max-width: 900px)" in APP_CSS


async def test_no_template_pins_the_nav_open_with_nowrap():
    for t in TEMPLATES:
        text = t.read_text(encoding="utf-8")
        assert "white-space: nowrap" not in text, t.name
        assert "white-space:nowrap" not in text, t.name
    assert not re.search(r"header nav[^{]*\{[^}]*white-space:\s*nowrap", APP_CSS)


# ── The Pico bridge out-ranks Pico's light palette ────────────────────────


def _pico_light_vars() -> set[str]:
    start = PICO_CSS.index(":root:not([data-theme=dark]),[data-theme=light]{")
    body = PICO_CSS[PICO_CSS.index("{", start) + 1 : PICO_CSS.index("}", start)]
    return {d.split(":")[0].strip() for d in body.split(";") if d.strip()}


def _bridge() -> tuple[str, set[str]]:
    m = re.search(r"(:root[^{]*)\{(.*?)\n\}", APP_CSS, re.S)
    return m.group(1).strip(), {
        d.split(":")[0].strip() for d in m.group(2).split(";") if d.strip().startswith("--pico")
    }


def test_bridge_selector_outranks_picos_light_block():
    """Pico's light block is `:root:not([data-theme=dark]),[data-theme=light]`
    (specificity 0,2,0) and it matches every one of our named themes except the
    one literally called "dark". A bare `:root` bridge (0,1,0) therefore loses,
    and midnight rendered Pico's dark-on-white headings and white inputs. The
    bridge must carry an attribute of its own to tie on specificity, and app.css
    must load after Pico so the tie breaks our way."""
    selector, _ = _bridge()
    assert ":root[data-theme]" in selector
    assert BASE_HTML.index("vendor/pico.min.css") < BASE_HTML.index("css/app.css")


def test_bridge_overrides_every_palette_variable_pico_would_light():
    """Anything Pico recolours in its light block and that a theme owns has to be
    remapped, or that one widget stays light on a dark page."""
    themed = {
        "--pico-background-color",
        "--pico-color",
        "--pico-muted-color",
        "--pico-muted-border-color",
        "--pico-h1-color",
        "--pico-h2-color",
        "--pico-h3-color",
        "--pico-h4-color",
        "--pico-h5-color",
        "--pico-h6-color",
        "--pico-form-element-background-color",
        "--pico-form-element-border-color",
        "--pico-form-element-color",
        "--pico-form-element-placeholder-color",
        "--pico-form-element-active-background-color",
        "--pico-form-element-selected-background-color",
        "--pico-card-background-color",
        "--pico-card-sectioning-background-color",
        "--pico-card-border-color",
        "--pico-code-background-color",
        "--pico-code-color",
        "--pico-table-border-color",
        "--pico-table-row-stripped-background-color",
        "--pico-accordion-active-summary-color",
        "--pico-accordion-close-summary-color",
        "--pico-accordion-open-summary-color",
        "--pico-accordion-border-color",
        "--pico-dropdown-background-color",
        "--pico-dropdown-border-color",
        "--pico-dropdown-color",
        "--pico-dropdown-hover-background-color",
        "--pico-mark-background-color",
        "--pico-mark-color",
        "--pico-blockquote-border-color",
        "--pico-blockquote-footer-color",
        "--pico-progress-background-color",
        "--pico-progress-color",
        "--pico-text-selection-color",
        "--pico-tooltip-background-color",
        "--pico-tooltip-color",
        "--pico-contrast",
        "--pico-contrast-background",
        "--pico-contrast-border",
        "--pico-contrast-inverse",
        "--pico-contrast-hover",
        "--pico-contrast-hover-background",
    }
    light = _pico_light_vars()
    assert themed <= light, themed - light  # keeps the list honest against Pico
    _, bridged = _bridge()
    assert themed <= bridged, themed - bridged


def test_bridge_uses_theme_tokens_only():
    m = re.search(r":root[^{]*\{(.*?)\n\}", APP_CSS, re.S)
    for decl in m.group(1).split(";"):
        decl = decl.strip()
        if not decl.startswith("--pico"):
            continue
        value = decl.split(":", 1)[1].strip()
        assert "var(--sp-" in value or value in ("transparent",), decl


@pytest.mark.parametrize("theme,path", [("midnight", "/generate/image"), ("daylight", "/")])
async def test_named_themes_reach_the_document(client, theme, path):
    await client.post("/settings", data={"ui.theme": theme})
    r = await client.get(path)
    assert f'data-theme="{theme}"' in r.text
