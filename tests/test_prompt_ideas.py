"""The shipped idea phrases (vjhstudio/data/prompt_ideas.json) and the chip row the
prompt builder renders from them. The chips are rendered server-side by a Jinja loop, so
these assertions hold with JavaScript disabled; Alpine only takes over `active` /
`aria-pressed` afterwards."""

from __future__ import annotations

import json
from pathlib import Path

from vjhstudio.services import ideas

APP_JS = Path(__file__).resolve().parent.parent / "vjhstudio" / "web" / "static" / "js" / "app.js"


def _app_js() -> str:
    return APP_JS.read_text()


KEYS = ("style", "mood", "lighting", "camera", "composition", "colour", "extras")


def test_load_ideas_has_the_seven_field_keys():
    data = ideas.load_ideas()
    assert tuple(data) == KEYS


def test_every_idea_list_is_ten_to_fourteen_clean_unique_phrases():
    data = ideas.load_ideas()
    for key in KEYS:
        phrases = data[key]
        assert isinstance(phrases, list)
        assert 10 <= len(phrases) <= 14, key
        for p in phrases:
            assert isinstance(p, str) and p
            assert p == p.strip(), (key, p)
            assert len(p) <= 40, (key, p)
        lowered = [p.lower() for p in phrases]
        assert len(set(lowered)) == len(lowered), key


def test_load_ideas_is_cached_and_hands_back_the_same_mapping():
    assert ideas.load_ideas() is ideas.load_ideas()


async def test_generate_page_ships_the_ideas_json(client):
    r = await client.get("/generate/image")
    assert r.status_code == 200
    assert 'id="prompt-ideas"' in r.text and 'type="application/json"' in r.text
    assert '"golden hour"' in r.text


async def test_generate_page_renders_idea_chips_server_side(client):
    r = await client.get("/generate/image")
    html = r.text
    assert '<button type="button" class="idea"' in html
    assert html.count('class="idea"') >= 70
    # the lighting row, unpressed until Alpine says otherwise
    start = html.index('aria-label="Lighting ideas"')
    row = html[start : html.index("</div>", start)]
    assert 'data-field="lighting" data-phrase="golden hour"' in row
    assert ">golden hour</button>" in row and 'aria-pressed="false"' in row
    assert "toggleIdea($el.dataset.field, $el.dataset.phrase)" in row
    assert "hasIdea($el.dataset.field, $el.dataset.phrase)" in row


async def test_every_lighting_phrase_in_the_json_has_a_chip(client):
    """The blob Alpine reads and the buttons Jinja rendered come from one call to
    load_ideas(); this pins them together so a template change cannot drop a chip."""
    html = (await client.get("/generate/image")).text
    blob = json.loads(
        html.split('id="prompt-ideas" type="application/json">', 1)[1].split("</script>", 1)[0]
    )
    start = html.index('aria-label="Lighting ideas"')
    row = html[start : html.index("</div>", start)]
    assert len(blob["lighting"]) == row.count('data-field="lighting"')
    assert len(blob["lighting"]) == len(ideas.load_ideas()["lighting"])


async def test_builder_long_fields_are_textareas(client):
    html = (await client.get("/generate/image")).text
    assert '<textarea name="subject"' in html
    assert '<textarea name="extras"' in html
    assert '<textarea name="negative"' in html
    assert '<textarea name="final_prompt"' in html


async def test_composed_prompt_block_has_a_copy_button(client):
    html = (await client.get("/generate/image")).text
    assert "Composed prompt" in html
    assert "copyComposed()" in html and ">Copy</button>" in html


async def test_describe_header_and_hint(client):
    html = (await client.get("/generate/image")).text
    assert "<h3>Describe</h3>" in html
    assert "Fill in what you can; the app writes the prompt." in html


async def test_generate_image_alias_matches_the_mode_query(client):
    alias = (await client.get("/generate/image")).text
    query = (await client.get("/generate?mode=image")).text
    assert 'name="subject"' in alias and 'name="subject"' in query
    assert alias.count('class="idea"') == query.count('class="idea"')


def test_generate_form_wires_the_chip_helpers():
    """Source-level guard (same convention as tests/test_js_generate_form.py): the
    template's chip handlers must exist on the Alpine component, and the component must
    read the JSON blob the builder renders."""
    app_js = _app_js()
    for needle in (
        "toggleIdea(field, phrase)",
        "hasIdea(field, phrase)",
        "copyComposed()",
        "window.vjhToggleIdea",
        "window.vjhHasIdea",
        "window.vjhAutosize",
        "getElementById('prompt-ideas')",
    ):
        assert needle in app_js, needle


def test_autosize_is_skipped_where_field_sizing_is_supported():
    """vjhAutosize writes an inline `height`, which would out-rank `field-sizing: content`
    and freeze the box for every later programmatic change (a polish card filling Final
    prompt, a chip pushing Extras onto a new line). So the fallback is gated on the
    feature test, and the two programmatic writers grow their own box."""
    app_js = _app_js()
    assert "CSS.supports('field-sizing', 'content')" in app_js
    assert "if (!VJH_FIELD_SIZING) {" in app_js  # the one-off pass in init()
    assert "if (VJH_FIELD_SIZING || !window.vjhAutosize) return;" in app_js  # _grow()
    assert "this._grow(field)" in app_js  # toggleIdea()
    assert "this._grow('final_prompt')" in app_js  # usePolish()
