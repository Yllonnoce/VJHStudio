"""'Find more models' on the Models page, for every kind in its dropdown.

Reply shapes are trimmed copies of RunWare's live ``modelSearch`` (2026-10-03): music and
speech share the ``audio`` category, 3D models sit under ``others`` and are told apart by
their ``-to-3d`` capability tags, and ``video`` / ``text`` have categories of their own."""

from __future__ import annotations

import json

from runware import RunwareError

from vjhstudio import db, models
from vjhstudio.services import catalog

AUDIO = [
    {
        "air": "minimax:music@2.6",
        "name": "MiniMax Music 2.6",
        "category": "audio",
        "architecture": "",
        "tags": [],
        "capabilities": ["io:text-to-audio", "form:checkpoint"],
    },
    {
        "air": "elevenlabs:1@1",
        "name": "ElevenLabs Music V1",
        "category": "audio",
        "architecture": "elevenlabs_music",
        "tags": [],
        "capabilities": [],
    },
    {
        "air": "xai:tts@0",
        "name": "xAI Text-to-Speech",
        "category": "audio",
        "architecture": "",
        "tags": [],
        "capabilities": ["io:text-to-audio", "form:checkpoint"],
    },
    # names that say neither "speech" nor "TTS": the architecture and the tags do
    {
        "air": "elevenlabs:27@1",
        "name": "Eleven v3",
        "category": "audio",
        "architecture": "elevenlabs_tts",
        "tags": ["tts", "flagship"],
        "capabilities": [],
    },
    # known to the catalog as speech, though nothing in the search record says so
    {
        "air": "fishaudio:s2.1@pro",
        "name": "Fish Audio S2.1 Pro",
        "category": "audio",
        "architecture": "",
        "tags": [],
        "capabilities": ["io:text-to-audio", "form:checkpoint"],
    },
]
OTHERS = [
    {
        "air": "runware:112@5",
        "name": "BiRefNet General",
        "category": "others",
        "capabilities": ["io:image-to-image", "op:remove-background", "form:checkpoint"],
    },
    {
        "air": "tripo:v3.1@0",
        "name": "Tripo 3D v3.1",
        "category": "others",
        "capabilities": ["io:text-to-3d", "io:image-to-3d", "form:checkpoint"],
    },
    {
        "air": "acme:mesh@1",
        "name": "Acme Mesher",
        "category": "others",
        "capabilities": ["io:image-to-3d", "form:checkpoint"],
    },
    {
        "air": "recraft:1@1",
        "name": "Recraft Vectorize",
        "category": "others",
        "capabilities": ["io:image-to-image", "op:vectorize", "form:checkpoint"],
    },
]


def _reply(results, total=None):
    return [{"results": list(results), "totalResults": len(results) if total is None else total}]


async def _key(client):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})


def _searches(fake):
    return [p for n, p in fake.calls if n == "model_search"]


def _rows(html: str) -> list[str]:
    """The AIR of each result row, in order."""
    return [chunk.split("</code>")[0] for chunk in html.split("<code>")[1:]]


# ---- the dropdown ----------------------------------------------------------------------
async def test_the_search_dropdown_offers_all_six_kinds(client):
    page = (await client.get("/models")).text
    form = page[page.index('<section id="search">') : page.index('id="search-results"')]
    for value, label in (
        ("image", "Image"),
        ("video", "Video"),
        ("text", "Text"),
        ("audio", "Music &amp; SFX"),
        ("speech", "Speech"),
        ("3d", "3D"),
    ):
        assert f'<option value="{value}">{label}</option>' in form, value
    assert "leave the box empty" in form  # the three small kinds can simply be listed


# ---- music and speech share one RunWare category ------------------------------------------
async def test_speech_search_asks_for_audio_and_keeps_only_the_voices(client, fake):
    await _key(client)
    fake.script["model_search"] = [_reply(AUDIO)]
    r = await client.post("/hx/models/search", data={"q": "eleven", "kind": "speech"})
    assert r.status_code == 200
    assert _searches(fake)[0]["category"] == "audio" and _searches(fake)[0]["search"] == "eleven"
    assert _rows(r.text) == ["xai:tts@0", "elevenlabs:27@1", "fishaudio:s2.1@pro"]


async def test_music_search_keeps_only_music_and_sound(client, fake):
    await _key(client)
    fake.script["model_search"] = [_reply(AUDIO)]
    r = await client.post("/hx/models/search", data={"q": "music", "kind": "audio"})
    assert _searches(fake)[0]["category"] == "audio"
    assert _rows(r.text) == ["minimax:music@2.6", "elevenlabs:1@1"]


def test_audio_kind_reads_the_architecture_and_tags_of_a_search_record():
    eleven = {
        "air": "elevenlabs:27@1",
        "name": "Eleven v3",
        "architecture": "elevenlabs_tts",
        "tags": ["tts"],
    }
    assert catalog.audio_kind(eleven) == "speech"
    assert catalog.audio_kind({**eleven, "tags": []}) == "speech"  # the architecture alone
    music = {
        "air": "elevenlabs:1@1",
        "name": "ElevenLabs Music V1",
        "architecture": "elevenlabs_music",
    }
    assert catalog.audio_kind(music) == "audio"
    # "tts" has to stand alone: no false hit inside another word
    assert catalog.audio_kind({"air": "acme:1@1", "name": "Bettsy Beats"}) == "audio"


# ---- 3D has no category of its own ------------------------------------------------------------
async def test_3d_search_asks_for_others_and_keeps_only_the_3d_models(client, fake):
    await _key(client)
    fake.script["model_search"] = [_reply(OTHERS)]
    r = await client.post("/hx/models/search", data={"q": "mesh", "kind": "3d"})
    assert r.status_code == 200 and _searches(fake)[0]["category"] == "others"
    assert _rows(r.text) == ["tripo:v3.1@0", "acme:mesh@1"]


async def test_3d_search_survives_runware_dropping_the_others_category(client, fake):
    """``others`` works but is not on RunWare's own list of supported categories. If it
    is ever refused, the search is repeated without a category and still filtered."""
    await _key(client)
    refused = RunwareError(
        "invalidCategory",
        "invalid value for 'category' parameter. Supported values are: 'checkpoint', 'video'.",
    )
    fake.script["model_search"] = [refused, _reply(OTHERS)]
    r = await client.post("/hx/models/search", data={"q": "tripo", "kind": "3d"})
    assert r.status_code == 200 and _rows(r.text) == ["tripo:v3.1@0", "acme:mesh@1"]
    first, second = _searches(fake)
    assert first["category"] == "others" and "category" not in second


# ---- listing a whole small category -------------------------------------------------------------
async def test_an_empty_search_lists_everything_for_the_three_small_kinds(client, fake):
    await _key(client)
    fake.script["model_search"] = [_reply(AUDIO)]
    r = await client.post("/hx/models/search", data={"q": "", "kind": "speech"})
    assert r.status_code == 200 and len(_rows(r.text)) == 3
    assert "search" not in _searches(fake)[0]  # RunWare refuses a search shorter than 2 characters
    # 3D: the others category is larger than one page, so it is read to the end
    fake.calls.clear()
    fake.script["model_search"] = [_reply(OTHERS[:2], total=150), _reply(OTHERS[2:], total=150)]
    r = await client.post("/hx/models/search", data={"q": "  ", "kind": "3d"})
    assert _rows(r.text) == ["tripo:v3.1@0", "acme:mesh@1"]
    pages = _searches(fake)
    assert [p.get("offset", 0) for p in pages] == [0, 100] and all(p["limit"] == 100 for p in pages)
    # the big kinds still want something typed, and send nothing without it
    fake.calls.clear()
    r = await client.post("/hx/models/search", data={"q": "", "kind": "image"})
    assert "Type something to search." in r.text and _searches(fake) == []


# ---- what is already in the list --------------------------------------------------------------------
async def test_results_already_in_the_list_say_so_instead_of_offering_add(client, fake):
    """Most music, speech and 3D results are models the app already ships; an Add button
    on those did nothing and still said "Added"."""
    await _key(client)
    fake.script["model_search"] = [_reply(OTHERS)]
    r = await client.post("/hx/models/search", data={"q": "", "kind": "3d"})
    known, new = r.text.split("acme:mesh@1")[0], r.text.split("acme:mesh@1")[1]
    assert "In your list" in known and 'name="record"' not in known  # Tripo ships with the app
    assert 'name="record"' in new and "In your list" not in new


async def test_adding_a_found_voice_files_it_under_speech(client, app, fake):
    await _key(client)
    record = json.dumps(
        {
            "air": "elevenlabs:27@1",
            "name": "Eleven v3",
            "architecture": "elevenlabs_tts",
            "capabilities": [],
        }
    )
    r = await client.post("/models/add", data={"kind": "speech", "record": record})
    assert r.status_code == 200 and "Added Eleven v3" in r.text
    with db.session_scope(app.state.boot.session_factory) as s:
        m = catalog.get_by_air(s, "elevenlabs:27@1")
        assert (m.kind, m.source, m.price_primary) == ("speech", "search", None)
        assert "elevenlabs:27@1" in [x.air for x in catalog.list_generate_models(s, "speech")]
    for kind in ("audio", "3d"):
        rec = json.dumps({"air": f"acme:{kind}@1", "name": f"Acme {kind}", "capabilities": []})
        assert (
            await client.post("/models/add", data={"kind": kind, "record": rec})
        ).status_code == 200
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(models.CatalogModel).filter_by(air="acme:3d@1").one().kind == "3d"
    bad = await client.post("/models/add", data={"kind": "banana", "record": record})
    assert bad.status_code == 400 and bad.json() == {"error": "bad kind"}


# ---- video and text were searching the wrong category -------------------------------------------------
async def test_video_and_text_search_their_own_category(client, fake):
    """Seen live: both asked RunWare for ``checkpoint``. A video search for "kling"
    returned Kling's *image* models (which would then be added as video models), and a
    text search for "claude" returned nothing at all."""
    await _key(client)
    fake.script["model_search"] = [_reply([]), _reply([]), _reply([])]
    for kind in ("video", "text", "image"):
        await client.post("/hx/models/search", data={"q": "kling", "kind": kind})
    assert [p["category"] for p in _searches(fake)] == ["video", "text", "checkpoint"]
    assert all(p["limit"] == 20 and p["visibility"] == "public" for p in _searches(fake))


async def test_a_search_term_runware_would_refuse_is_stopped_here(client, fake):
    """RunWare takes 2 to 48 characters; anything else came back as a raw API error."""
    await _key(client)
    for q in ("x", "y" * 49):
        r = await client.post("/hx/models/search", data={"q": q, "kind": "image"})
        assert r.status_code == 422 and "Search for 2 to 48 characters." in r.text
    assert _searches(fake) == []
