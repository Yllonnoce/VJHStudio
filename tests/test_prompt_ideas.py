"""The shipped idea phrases (vjhstudio/data/prompt_ideas.json) and the chip rows the
prompt builder renders from them. Stills and clips get different phrases, and both rows
are rendered server-side by a Jinja loop, so these assertions hold with JavaScript
disabled; Alpine only takes over `active` / `aria-pressed` and the row swap afterwards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vjhstudio import db, models
from vjhstudio.services import catalog, ideas
from vjhstudio.services import settings as settings_svc

WEB = Path(__file__).resolve().parent.parent / "vjhstudio" / "web"
APP_JS = WEB / "static" / "js" / "app.js"
APP_CSS = WEB / "static" / "css" / "app.css"
BUILDER_HTML = WEB / "templates" / "generate" / "_prompt_builder.html"


def _app_js() -> str:
    return APP_JS.read_text()


KEYS = ("style", "mood", "lighting", "camera", "composition", "colour", "extras")
MODES = ("image", "video")
# the aria-label each row is found by, in the same order
LABELS = {k: k.capitalize() for k in KEYS}


def _blob(html: str) -> dict:
    """The JSON Alpine reads back, both modes of it."""
    return json.loads(
        html.split('id="prompt-ideas" type="application/json">', 1)[1].split("</script>", 1)[0]
    )


def _row(html: str, label: str, mode: str) -> str:
    """One chip row: the `label` field's phrases for `mode`, up to its closing tag."""
    start = html.index(f'aria-label="{label} ideas" data-mode="{mode}"')
    return html[start : html.index("</div>", start)]


def _row_tag(html: str, label: str, mode: str) -> str:
    """Just the row's own opening tag, without the chips inside it."""
    row = _row(html, label, mode)
    return row[: row.index("<button")]


def test_load_ideas_has_both_modes_with_the_seven_field_keys():
    data = ideas.load_ideas()
    assert tuple(data) == MODES
    for mode in MODES:
        assert tuple(data[mode]) == KEYS


def test_every_idea_list_is_ten_to_fourteen_clean_unique_phrases():
    data = ideas.load_ideas()
    for mode in MODES:
        for key in KEYS:
            phrases = data[mode][key]
            assert isinstance(phrases, list)
            assert 10 <= len(phrases) <= 14, (mode, key)
            for p in phrases:
                assert isinstance(p, str) and p
                assert p == p.strip(), (mode, key, p)
                assert len(p) <= 40, (mode, key, p)
            lowered = [p.lower() for p in phrases]
            assert len(set(lowered)) == len(lowered), (mode, key)


def test_no_phrase_contains_a_comma():
    """Chips are comma-joined into the field and `vjhToggleIdea` splits the field back on
    commas, so a phrase with a comma in it could never be un-picked as one chip."""
    data = ideas.load_ideas()
    for mode in MODES:
        for key in KEYS:
            for phrase in data[mode][key]:
                assert "," not in phrase, (mode, key, phrase)


def test_the_two_modes_pull_their_own_weight():
    """The whole point of the split: a clip's phrases are camera moves, motion and
    grading terms, not painting styles."""
    data = ideas.load_ideas()
    assert "oil painting" in data["image"]["style"]
    assert "oil painting" not in data["video"]["style"]
    assert "slow dolly in" in data["video"]["camera"]
    assert "slow dolly in" not in data["image"]["camera"]
    assert "slow motion" in data["video"]["extras"]
    for key in KEYS:
        assert data["image"][key] != data["video"][key], key


def test_load_ideas_for_hands_back_one_mode():
    data = ideas.load_ideas()
    assert ideas.load_ideas_for("video") is data["video"]
    assert ideas.load_ideas_for("image") is data["image"]
    # anything else reads as the image set, the same default the routes use
    assert ideas.load_ideas_for("") is data["image"]


def test_load_ideas_is_cached_and_hands_back_the_same_mapping():
    assert ideas.load_ideas() is ideas.load_ideas()


async def test_generate_page_ships_the_ideas_json_for_both_modes(client):
    r = await client.get("/generate/image")
    assert r.status_code == 200
    assert 'id="prompt-ideas"' in r.text and 'type="application/json"' in r.text
    blob = _blob(r.text)
    assert tuple(blob) == MODES
    assert "golden hour" in blob["image"]["lighting"]
    assert "slow dolly in" in blob["video"]["camera"]


async def test_generate_page_renders_idea_chips_server_side(client):
    r = await client.get("/generate/image")
    html = r.text
    assert '<button type="button" class="idea"' in html
    assert html.count('class="idea"') >= 140  # seven fields, two modes
    # the image lighting row, unpressed until Alpine says otherwise
    row = _row(html, "Lighting", "image")
    assert 'data-field="lighting" data-phrase="golden hour"' in row
    assert ">golden hour</button>" in row and 'aria-pressed="false"' in row
    assert "toggleIdea($el.dataset.field, $el.dataset.phrase, " in row
    assert "hasIdea($el.dataset.field, $el.dataset.phrase)" in row


async def test_only_the_active_modes_rows_are_shown(client):
    """Both rows ship on every load; the inactive one is hidden server-side so there is
    no flash before Alpine boots, and x-show takes the swap over from there."""
    for page, other in (("image", "video"), ("video", "image")):
        html = (await client.get(f"/generate/{page}")).text
        for label in ("Style", "Mood", "Lighting", "Camera", "Composition", "Colour", "Extras"):
            active = _row_tag(html, label, page)
            hidden = _row_tag(html, label, other)
            assert f"x-show=\"mode === '{page}'\"" in active, (page, label)
            assert 'style="display:none"' not in active, (page, label)
            assert 'style="display:none"' in hidden, (page, label)


async def test_the_style_and_camera_rows_suit_their_mode(client):
    """A clip has no oil painting and a still has no dolly move."""
    image = (await client.get("/generate/image")).text
    video = (await client.get("/generate/video")).text
    for html in (image, video):
        assert html.count('data-phrase="oil painting"') == 1
        assert 'data-phrase="oil painting"' in _row(html, "Style", "image")
        assert "oil painting" not in _row(html, "Style", "video")
        assert html.count('data-phrase="slow dolly in"') == 1
        assert 'data-phrase="slow dolly in"' in _row(html, "Camera", "video")
        assert "slow dolly in" not in _row(html, "Camera", "image")


async def test_every_phrase_in_the_json_has_a_chip_in_its_own_row(client):
    """The blob Alpine reads and the buttons Jinja rendered come from one call to
    load_ideas(); this pins them together, per mode, so a template change cannot drop a
    chip or render one mode's phrases into the other's row."""
    html = (await client.get("/generate/image")).text
    blob = _blob(html)
    shipped = ideas.load_ideas()
    for mode in MODES:
        for field in KEYS:
            row = _row(html, LABELS[field], mode)
            # the three Extras sound chips are model-dependent, not shipped phrases
            chips = row.count(f'data-field="{field}"') - row.count('data-audio="1"')
            assert len(blob[mode][field]) == chips, (mode, field)
            assert len(blob[mode][field]) == len(shipped[mode][field]), (mode, field)
            for phrase in shipped[mode][field]:
                assert f'data-phrase="{phrase}"' in row, (mode, field, phrase)


async def test_the_subject_example_follows_the_mode(client):
    """Subject has no chips, so its placeholder is the only nudge it gets; a still is a
    moment, a clip is a movement."""
    still = "a red fox in a snowy forest, looking back over its shoulder"
    clip = "a red fox running through a snowy forest, looking back at the camera"
    image = (await client.get("/generate/image")).text
    video = (await client.get("/generate/video")).text
    assert f'placeholder="{still}"' in image and f'placeholder="{clip}"' not in image
    assert f'placeholder="{clip}"' in video and f'placeholder="{still}"' not in video
    for html in (image, video):
        assert f":placeholder=\"mode === 'video' ? '{clip}' : '{still}'\"" in html


# ---- Style is single-select -------------------------------------------------------
async def test_only_the_style_rows_are_single_select(client):
    """A picture is one medium, not three: a Style chip replaces the field instead of
    appending to it. The row says so and the click hands the flag to `toggleIdea`."""
    html = (await client.get("/generate/image")).text
    for mode in MODES:
        assert 'data-single="1"' in _row(html, "Style", mode), mode
        for label in ("Mood", "Lighting", "Camera", "Composition", "Colour", "Extras"):
            assert "data-single" not in _row(html, label, mode), (mode, label)
    assert html.count('data-single="1"') == 2  # one Style row per mode, nothing else
    assert (
        "toggleIdea($el.dataset.field, $el.dataset.phrase, "
        "$el.parentElement.dataset.single === '1')" in html
    )


def test_the_single_select_branch_is_wired_to_the_pure_helper():
    """`vjhSetIdea` is tested under node (tests/test_compose_js.py); this pins the branch
    in the component that reaches it."""
    app_js = _app_js()
    assert "toggleIdea(field, phrase, single) {" in app_js
    assert "single && window.vjhSetIdea" in app_js
    assert "window.vjhSetIdea(this.fields[field], phrase)" in app_js
    assert "window.vjhToggleIdea(this.fields[field], phrase)" in app_js


# ---- the video-only sound chips ----------------------------------------------------
AUDIO_CHIPS = ("ambient sound", "spoken dialogue", "background music")
WITH_AUDIO = "vjh:sings@1"
NO_AUDIO = "vjh:silent@1"


def _seed_video_model(app, air: str, name: str, caps: list[str]) -> None:
    with db.session_scope(app.state.boot.session_factory) as s:
        row = catalog.get_by_air(s, air)
        if row is None:
            row = models.CatalogModel(air=air, name=name, kind="video", source="curated")
            s.add(row)
        row.name, row.kind = name, "video"
        row.capabilities_json = list(caps)
        row.price_primary, row.price_unit = 0.1, "per_second"
        s.flush()


@pytest.fixture
def audio_models(app):
    _seed_video_model(app, WITH_AUDIO, "Sings", ["io:text-to-video", "feat:audio"])
    _seed_video_model(app, NO_AUDIO, "Silent", ["io:text-to-video"])
    return app


def _extras_audio(row: str) -> list[str]:
    return [p for p in AUDIO_CHIPS if f'data-phrase="{p}" data-audio="1"' in row]


async def test_a_video_model_with_audio_shows_the_sound_chips(client, audio_models, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        settings_svc.set_many(s, {"defaults.video_model": WITH_AUDIO})
    html = (await client.get("/generate/video")).text
    assert 'data-has-audio="1"' in html
    row = _row(html, "Extras", "video")
    assert _extras_audio(row) == list(AUDIO_CHIPS)
    assert 'style="display:none"' not in row  # the row and all three chips are shown
    assert row.count('x-show="hasAudio"') == 3


async def test_a_video_model_without_audio_keeps_the_sound_chips_hidden(client, audio_models, app):
    """They stay in the DOM -- Alpine shows them the moment a model that sings is
    picked, without a round trip -- but hidden server-side so there is no flash."""
    with db.session_scope(app.state.boot.session_factory) as s:
        settings_svc.set_many(s, {"defaults.video_model": NO_AUDIO})
    html = (await client.get("/generate/video")).text
    assert 'data-has-audio="0"' in html
    row = _row(html, "Extras", "video")
    assert _extras_audio(row) == list(AUDIO_CHIPS)
    assert row.count('style="display:none"') == 3  # one per sound chip, row itself shown


async def test_the_audio_flag_follows_a_model_change_in_the_swapped_panel(client, audio_models):
    r = await client.get(f"/hx/model-options?mode=video&air={WITH_AUDIO}")
    assert 'data-has-audio="1"' in r.text
    r = await client.get(f"/hx/model-options?mode=video&air={NO_AUDIO}")
    assert 'data-has-audio="0"' in r.text


async def test_a_still_never_offers_a_sound_chip(client):
    html = (await client.get("/generate/image")).text
    assert 'data-has-audio="0"' in html
    assert _extras_audio(_row(html, "Extras", "image")) == []


def test_has_audio_is_read_off_the_swapped_params_panel():
    """Same shape as needsFirstFrame (tests/test_js_generate_form.py): declared state, so
    x-show is reactive, and re-read from the fresh markup after every swap."""
    src = _app_js()
    window = src[src.index("_syncHasAudio() {") :][:300]
    assert "document.getElementById('model-params')" in window
    assert "panel.dataset.hasAudio === '1'" in window
    assert "hasAudio: false," in src
    assert "document.addEventListener('htmx:afterSwap', () => this._syncHasAudio());" in src


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
    assert "<h2>Describe</h2>" in html
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
        "toggleIdea(field, phrase, single)",
        "hasIdea(field, phrase)",
        "copyComposed()",
        "window.vjhToggleIdea",
        "window.vjhHasIdea",
        "window.vjhSetIdea",
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
    # Typing goes through the component's own gated `autosize(el)`; calling the raw
    # helper from `@input` skipped the feature test and froze the box on Chrome/Safari.
    assert "autosize(el) {" in app_js
    builder = BUILDER_HTML.read_text()
    assert "vjhAutosize" not in builder
    assert builder.count('@input="autosize($el)"') == 4


def test_chips_carry_their_own_focus_ring():
    """Pico styles `[role=group]` as a segmented bar and paints one focus ring around
    the whole row, so a keyboard user could not tell which chip was focused."""
    css = APP_CSS.read_text()
    assert ".ideas{box-shadow:none}" in css
    assert (
        ".ideas .idea:focus-visible{outline:2px solid var(--sp-accent);outline-offset:2px}" in css
    )
    assert ".ideas .idea:focus{box-shadow:none}" in css
