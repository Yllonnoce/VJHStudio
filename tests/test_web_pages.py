"""The Pico bridge out-ranks Pico's own light palette.

Pico's light block matches every named theme except the one literally called
"dark", so a bare `:root` bridge lost the cascade and midnight rendered Pico's
near-black headings and white inputs on its dark ground.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "vjhstudio" / "web"
APP_CSS = (WEB / "static" / "css" / "app.css").read_text(encoding="utf-8")
PICO_CSS = (WEB / "static" / "vendor" / "pico.min.css").read_text(encoding="utf-8")
BASE_HTML = (WEB / "templates" / "base.html").read_text(encoding="utf-8")


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
