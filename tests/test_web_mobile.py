"""Phone-sized UI: every dialog opens as a full-screen sheet, and the gallery's
details dialog leaves the top bar alone.

The rules are pure CSS, so they are checked as CSS: the phone-only block has to exist
and say the things that keep a dialog usable on a 390 px screen (one scrolling region,
media that fits, an action row that wraps), and the templates have to carry the class
hooks those rules are written against.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "vjhstudio" / "web"
STATIC = WEB / "static"
APP_CSS = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
APP_JS = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
GALLERY_HTML = (WEB / "templates" / "pages" / "gallery.html").read_text(encoding="utf-8")
DETAIL_HTML = (WEB / "templates" / "gallery" / "_detail.html").read_text(encoding="utf-8")
CARD_HTML = (WEB / "templates" / "gallery" / "_card.html").read_text(encoding="utf-8")
REFS_HTML = (WEB / "templates" / "generate" / "_refs.html").read_text(encoding="utf-8")
SAVE_HTML = (WEB / "templates" / "generate" / "_save_prompt.html").read_text(encoding="utf-8")


def _phone_block() -> str:
    """The `@media (max-width: 767px)` block that holds the sheet rules."""
    start = APP_CSS.index("Phones: every dialog is a full-screen sheet")
    start = APP_CSS.index("@media", start)
    body = APP_CSS.index("{", start) + 1
    depth, i = 1, body
    while depth:
        m = re.compile(r"[{}]").search(APP_CSS, i)
        assert m, "unclosed @media block"
        depth += 1 if m.group(0) == "{" else -1
        i = m.end()
    return APP_CSS[body : i - 1]


# ── Full-screen sheets ────────────────────────────────────────────────────


def test_every_dialog_becomes_a_full_screen_sheet_on_a_phone():
    """A centred panel with Pico's margins leaves a phone a strip of the screen,
    and Pico gives the panel no scrolling region of its own -- the action row simply
    falls off the bottom. Below 768 px the dialog fills the viewport instead."""
    block = _phone_block()
    assert "dialog[open]" in block
    for rule in ("width:100vw", "max-width:none", "height:100dvh", "max-height:none"):
        assert rule in block, rule
    assert "margin:0" in block and "border-radius:0" in block


def test_the_sheet_has_a_sticky_head_one_scrolling_body_and_a_pinned_action_row():
    block = _phone_block()
    article = block[block.index("dialog[open] > article{") :]
    article = article[: article.index("}")]
    assert "height:100%" in article and "display:flex" in article
    assert "flex-direction:column" in article
    assert "dialog[open] > article > header{position:sticky;top:0" in block
    assert "dialog[open] > article > .sheet-body" in block
    body = block[block.index("dialog[open] > article > .sheet-body") :]
    body = body[: body.index("}")]
    assert "overflow:auto" in body and "flex:1 1 auto" in body
    footer = block[block.rindex("dialog[open] > article > footer{") :]
    footer = footer[: footer.index("}")]
    assert "position:sticky;bottom:0" in footer and "flex-wrap:wrap" in footer


def test_the_scrolling_regions_have_their_hooks_in_the_templates():
    """The CSS above is written against one class per dialog body; without the hook
    the sheet has no scrolling region and the content is clipped instead."""
    assert 'id="ref-picker-body" class="sheet-body"' in REFS_HTML
    assert 'class="sheet-body"' in SAVE_HTML
    assert 'class="lightbox-meta"' in DETAIL_HTML


def test_the_lightbox_media_fits_and_its_actions_wrap():
    block = _phone_block()
    media = block[block.index(".lightbox-image,video.lightbox-image{") :]
    media = media[: media.index("}")]
    assert "max-height:60dvh" in media and "object-fit:contain" in media
    meta = block[block.index(".lightbox-meta{") :]
    meta = meta[: meta.index("}")]
    assert "overflow:auto" in meta
    assert ".lightbox-actions{" in block
    # the action row is a wrapping flex row at every width
    actions = APP_CSS[APP_CSS.index(".lightbox-actions{display:flex") :]
    assert "flex-wrap:wrap" in actions[: actions.index("}")]


def test_the_asset_picker_shows_three_columns_on_a_phone():
    assert "#ref-picker .asset-picker-grid{grid-template-columns:repeat(3, 1fr)}" in _phone_block()


def test_the_save_prompt_form_stacks():
    block = _phone_block()
    assert "#save-prompt .sheet-body > label{display:block;width:100%}" in block
    assert "#save-prompt #save-prompt-submit{width:100%" in block


def test_the_per_dialog_widths_are_out_ranked():
    """`#ref-picker article{max-width:min(46rem, 92vw)}` is an ID rule, so the
    generic `dialog[open] > article` sheet cannot win against it on its own."""
    block = _phone_block()
    assert "#ref-picker[open] > article" in block and "#save-prompt[open] > article" in block
    assert "max-width:none" in block[block.index("#ref-picker[open] > article") :]


# ── The lightbox keeps the top bar ────────────────────────────────────────


def test_the_lightbox_is_not_modal_so_the_top_bar_stays_usable():
    """A modal dialog is raised into the browser's top layer and covers the sticky
    header, so the details of an output hid the navigation."""
    assert "showModal" not in GALLERY_HTML
    lightbox_js = APP_JS[APP_JS.index("function vjhOpenLightbox") :]
    lightbox_js = lightbox_js[: lightbox_js.index("// ── Assets")]
    assert "showModal" not in lightbox_js
    assert "dlg.show()" in lightbox_js


def test_the_lightbox_sits_below_the_header_at_every_width():
    rule = APP_CSS[APP_CSS.index("#lightbox{") :]
    rule = rule[: rule.index("}")]
    assert "position:fixed" in rule
    assert "inset:var(--vjh-header-h, 4.5rem) 0 0 0" in rule
    phone = _phone_block()
    assert "inset:var(--vjh-header-h, 4.5rem) 0 0 0" in phone[phone.index("#lightbox[open]{") :]


def test_esc_and_a_click_on_the_dimmed_area_still_close_the_lightbox():
    """Both are free for a modal dialog and have to be re-added for a non-modal one."""
    assert "e.key === 'Escape'" in APP_JS
    assert "e.target === dlg" in APP_JS
    assert "close-lightbox" in APP_JS  # the htmx-driven close is untouched


def test_a_drag_that_ends_on_the_dim_area_does_not_close_the_lightbox():
    """Selecting the prompt text and releasing the button outside the panel fires a
    click whose target is the dialog; only a press AND release out there may close."""
    assert "vjhLightboxDownTarget = e.target" in APP_JS
    assert "vjhLightboxDownTarget === dlg" in APP_JS


def test_escape_is_only_swallowed_when_it_was_aimed_at_the_lightbox():
    """An unconditional preventDefault would eat Esc from an open select or a search
    field elsewhere on the page while the details happen to be open."""
    esc = APP_JS[APP_JS.index("if (e.key === 'Escape')") :]
    esc = esc[: esc.index("} else if")]
    assert "if (dlg.contains(e.target)) e.preventDefault();" in esc


def test_the_page_behind_the_lightbox_does_not_scroll():
    """A modal dialog blocks the page under it; a non-modal one does not, so the class
    goes on when it opens and comes off on the dialog's own close event -- which covers
    Esc, the close button, the dim area and the delete path alike."""
    assert "html.vjh-lightbox-open{overflow:hidden}" in APP_CSS
    assert "classList.add('vjh-lightbox-open')" in APP_JS
    close = APP_JS[APP_JS.index("dlg.addEventListener('close'") :]
    assert "classList.remove('vjh-lightbox-open')" in close[: close.index("});")]
    assert APP_JS.count("classList.remove('vjh-lightbox-open')") == 1


def test_the_scrolling_regions_keep_their_overscroll():
    """Without this, reaching the end of the params table hands the scroll to the
    gallery behind the panel -- the usual phone "the page ran away" surprise."""
    body = APP_CSS[APP_CSS.index("#lightbox-body{") :]
    assert "overscroll-behavior:contain" in body[: body.index("}")]
    phone = _phone_block()
    meta = phone[phone.index(".lightbox-meta{") :]
    assert "overscroll-behavior:contain" in meta[: meta.index("}")]


def test_dvh_has_a_vh_fallback():
    """`dvh` is the right unit (it follows a mobile browser's address bar) but an older
    browser drops the whole declaration, leaving the sheet auto-height."""
    block = _phone_block()
    assert "height:100vh;height:100dvh" in block
    assert "max-height:60vh;max-height:60dvh" in block


def test_focus_goes_somewhere_sensible_around_the_lightbox():
    """`show()` runs the focusing steps, so autofocus puts the keyboard on Close; and
    when a delete re-fetches the grid the focused card disappears with it."""
    assert 'class="secondary outline lightbox-close" aria-label="Close" autofocus' in GALLERY_HTML
    swap = APP_JS[APP_JS.index("htmx:afterSwap") :]
    assert "gallery-grid" in swap and "document.activeElement" in swap
    assert "button.gallery-thumb" in swap


def test_the_lightbox_buttons_state_their_swap():
    """hx-swap is inherited, and #gallery-grid (the cards' parent) sets outerHTML for
    its own reload -- so without this the detail REPLACES #lightbox-body and every
    later click finds no target and leaves the first output on screen."""
    assert CARD_HTML.count('hx-target="#lightbox-body"') == 2
    assert CARD_HTML.count('hx-swap="innerHTML"') == 2


async def test_the_lightbox_detail_still_carries_its_actions(client, fake, app):
    """The sheet rules lean on the partial's shape, so the shape is asserted here."""
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/0.png", "seed": 1, "cost": 0.001}]]
    await client.post(
        "/generate/image",
        data={
            "project_id": "1",
            "model": "runware:101@1",
            "subject": "castle",
            "width": "1024",
            "height": "1024",
            "number_results": "1",
            "output_format": "PNG",
        },
    )
    await app.state.runner.wait_idle()
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/hx/outputs/{oid}")
    assert r.status_code == 200
    assert 'class="lightbox-meta"' in r.text
    assert 'class="lightbox-actions"' in r.text
    assert r.text.index('class="lightbox-image"') < r.text.index('class="lightbox-meta"')


def test_a_closed_lightbox_is_not_painted():
    """#lightbox{display:flex} outranks Pico's dialog:not([open]){display:none}; without
    this rule a closed lightbox drew a dim wash over the whole gallery on a cold load."""
    from pathlib import Path

    css = (Path(__file__).parents[1] / "vjhstudio/web/static/css/app.css").read_text()
    assert "#lightbox:not([open]){display:none}" in css
