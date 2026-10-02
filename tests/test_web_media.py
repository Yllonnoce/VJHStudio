"""The Music & SFX, Speech and 3D tabs: pages, model-driven fields, submit, estimates."""

import io
import re

import pytest
from PIL import Image

from vjhstudio import db, models
from vjhstudio.services import assets, catalog

ACE = "runware:ace-step@v1.5-turbo"
MUSIC = "minimax:music@2.6"
COVER = "minimax:music@cover"
XAI = "xai:tts@0"
INWORLD = "inworld:tts@2"
HUNYUAN = "tencent:hunyuan-3d@3.1-rapid"
TRELLIS = "microsoft:trellis-2@4b"
TRIPO = "tripo:v3.1@0"
BARE = "vjh:unharvested-music@1"

ROWS = [
    (
        ACE,
        "ACE-Step v1.5 Turbo",
        "audio",
        ["io:text-to-audio"],
        0.0001,
        "per_second",
        {
            "fields": {
                "positivePrompt": {"required": True, "max": 3000},
                "duration": {"type": "float", "min": 30, "max": 300, "default": 60},
                "seed": {"type": "integer"},
                "settings.lyrics": {"type": "string", "max": 3000},
                "settings.bpm": {"type": "integer", "min": 30, "max": 300},
                "settings.timeSignature": {"type": "integer", "values": [2, 3, 4, 6]},
                "settings.nested.deep": {"type": "integer"},
            }
        },
    ),
    (
        MUSIC,
        "MiniMax Music 2.6",
        "audio",
        ["io:text-to-audio"],
        0.15,
        "per_output",
        {
            "fields": {
                "positivePrompt": {"required": True},
                "negativePrompt": {},
                "seed": {"type": "integer"},
                "settings.instrumental": {"type": "boolean"},
                "settings.lyrics": {"type": "string"},
                "settings.lyricsOptimizer": {"type": "boolean"},
            }
        },
    ),
    (COVER, "MiniMax Music Cover", "audio", ["io:audio-to-audio"], 0.15, "per_output", None),
    (BARE, "Unharvested Music", "audio", ["io:text-to-audio"], None, None, None),
    (
        XAI,
        "xAI Text-to-Speech",
        "speech",
        ["io:text-to-audio"],
        0.015,
        "per_1k_chars",
        {
            "fields": {
                "speech.text": {"required": True},
                "speech.voice": {"default": "eve", "values": ["eve", "luna", "orion"]},
                "speech.language": {"values": ["en", "fr"]},
            }
        },
    ),
    (
        INWORLD,
        "Inworld TTS-2",
        "speech",
        ["io:text-to-audio"],
        0.035,
        "per_1k_chars",
        {
            "fields": {
                "speech.text": {"required": True, "max": 2000},
                "speech.voice": {"values": ["Hank", "Loretta"]},
                "speech.speed": {
                    "type": "float",
                    "min": 0.5,
                    "max": 1.5,
                    "step": 0.1,
                    "default": 1,
                },
            }
        },
    ),
    (
        HUNYUAN,
        "Hunyuan 3D Rapid",
        "3d",
        ["io:text-to-3d", "io:image-to-3d"],
        0.225,
        "per_output",
        {
            "fields": {"positivePrompt": {"max": 200}, "settings.pbr": {"type": "boolean"}},
            "inputs": {"image": {"required": False}},
        },
    ),
    (
        TRELLIS,
        "TRELLIS.2",
        "3d",
        ["io:image-to-3d"],
        None,
        None,
        {"fields": {"seed": {}}, "inputs": {"image": {"required": True}}},
    ),
    (
        TRIPO,
        "Tripo 3D v3.1",
        "3d",
        ["io:text-to-3d", "io:image-to-3d"],
        0.3,
        "per_output",
        {
            "fields": {"positivePrompt": {}, "negativePrompt": {}},
            "inputs": {"images": {"required": False, "min_items": 1, "max_items": 4}},
        },
    ),
]
TRIPO_RATES = [
    {"amount": 0.3, "unit": "output", "label": "Text-to-3D"},
    {"amount": 0.4, "unit": "output", "label": "Image-to-3D"},
]


@pytest.fixture
def seeded(client, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        for air, name, kind, caps, price, unit, c in ROWS:
            m = catalog.get_by_air(s, air)
            if m is None:
                m = models.CatalogModel(air=air, name=name, kind=kind, source="curated")
                s.add(m)
            m.name, m.kind, m.capabilities_json, m.constraints_json = name, kind, caps, c
            m.price_primary, m.price_unit = price, unit
            m.is_hidden = False
            if air == TRIPO:
                m.price_tiers_json = {"rates": TRIPO_RATES}
    return app


async def _key(client):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (1, 2, 3)).save(b, "PNG")
    return b.getvalue()


def _asset(app) -> int:
    with db.session_scope(app.state.boot.session_factory) as s:
        a, _ = assets.store_upload(
            s, app.state.paths, original_name="cup.png", content=_png(), mime="image/png"
        )
        return a.id


def _job(app, **by):
    with db.session_scope(app.state.boot.session_factory) as s:
        j = s.query(models.Job).filter_by(**by).order_by(models.Job.created_at.desc()).first()
        return (
            None if j is None else (j.kind, j.status, dict(j.request_json), j.error_message, j.id)
        )


# ---- pages -----------------------------------------------------------------------------
async def test_each_new_tab_is_a_page_with_five_tabs_and_its_own_models(client, seeded):
    for kind, mine, not_mine in (("audio", ACE, XAI), ("speech", XAI, ACE), ("3d", HUNYUAN, ACE)):
        r = await client.get(f"/generate/{kind}")
        assert r.status_code == 200, kind
        body = r.text
        for key in ("image", "video", "audio", "speech", "3d"):
            assert f'id="mode-tab-{key}"' in body
        current = body[
            body.index(f'id="mode-tab-{kind}"') - 120 : body.index(f'id="mode-tab-{kind}"') + 200
        ]
        assert 'aria-current="page"' in current
        assert f'<option value="{mine}"' in body and f'<option value="{not_mine}"' not in body
        assert f'hx-post="/generate/{kind}"' in body and 'id="queue-panel"' in body
    # a model the app cannot drive (needs an audio clip) is not offered
    assert f'<option value="{COVER}"' not in (await client.get("/generate/audio")).text


async def test_the_image_and_video_page_links_to_the_three_new_tabs(client, seeded):
    body = (await client.get("/generate")).text
    for kind in ("audio", "speech", "3d"):
        assert f'id="mode-tab-{kind}" href="/generate/{kind}"' in body
    assert "Music &amp; SFX" in body


async def test_default_models_are_the_cheap_sensible_ones(client, seeded):
    assert (
        f'<option value="{ACE}" data-name="ACE-Step v1.5 Turbo" data-price="0.0001" selected'
        in (await client.get("/generate/audio")).text
    )
    assert f'<option value="{XAI}"' in (await client.get("/generate/speech")).text
    assert (
        f'value="{HUNYUAN}" data-name="Hunyuan 3D Rapid" data-price="0.225" selected'
        in (await client.get("/generate/3d")).text
    )


# ---- the fields follow the model --------------------------------------------------------
async def _params(client, kind, air, **extra):
    r = await client.post("/hx/media/params", data={"kind": kind, "model": air, **extra})
    assert r.status_code == 200
    return r.text


async def test_music_fields_follow_the_model(client, seeded):
    ace = await _params(client, "audio", ACE)
    assert 'name="prompt"' in ace and 'name="lyrics"' in ace
    assert (
        'name="duration"' in ace
        and 'min="30"' in ace
        and 'max="300"' in ace
        and 'value="60"' in ace
    )
    assert 'name="vocals"' not in ace and 'name="negative_prompt"' not in ace
    assert 'name="st_bpm"' in ace and 'name="st_timeSignature"' in ace
    assert "st_nested" not in ace  # nested settings stay in the extra-JSON box
    mm = await _params(client, "audio", MUSIC)
    assert 'name="vocals"' in mm and 'name="negative_prompt"' in mm
    assert 'name="duration"' not in mm  # a song's length is the model's business
    # the model's lyrics writer is one of the three Vocals options, not a loose switch
    assert 'name="st_lyricsOptimizer"' not in mm and "the model writes the lyrics" in mm
    assert '<option value="instrumental" selected>Instrumental (no vocals)</option>' in mm
    for fmt in ("MP3", "WAV", "FLAC", "OGG"):
        assert f'<option value="{fmt}"' in ace


async def test_what_was_typed_survives_a_model_change(client, seeded):
    body = await _params(client, "audio", MUSIC, prompt="lo-fi beat", lyrics="la la")
    assert ">lo-fi beat</textarea>" in body and ">la la</textarea>" in body


async def test_an_unharvested_model_still_gets_a_working_form(client, seeded):
    body = await _params(client, "audio", BARE)
    assert 'name="prompt"' in body and 'name="lyrics"' in body and 'name="duration"' in body
    assert "have not been read yet" in body


async def test_speech_fields_follow_the_model(client, seeded):
    xai = await _params(client, "speech", XAI)
    # a real dropdown with every voice in it, opened on the model's default -- the old
    # type-to-search box showed just "eve" and no list (reported live)
    assert 'name="text"' in xai and '<select name="voice" id="voice-select">' in xai
    assert '<option value="eve" selected>eve</option>' in xai
    for voice in ("luna", "orion"):
        assert f'<option value="{voice}" >{voice}</option>' in xai
    assert "3 voices" in xai and "data-filter-select" not in xai  # short list: no filter box
    assert '<select name="language" id="language-select">' in xai
    assert '<option value="fr" >fr</option>' in xai and "Model default" in xai
    assert 'name="speed"' not in xai
    inworld = await _params(client, "speech", INWORLD)
    assert 'name="speed"' in inworld and 'min="0.5"' in inworld and 'max="1.5"' in inworld
    assert 'maxlength="2000"' in inworld and 'name="language"' not in inworld


async def test_3d_fields_follow_the_model(client, seeded, app):
    asset_id = _asset(app)
    both = await _params(client, "3d", HUNYUAN)
    assert 'name="prompt"' in both and 'maxlength="200"' in both
    assert f'name="image_asset_id" value="{asset_id}"' in both
    assert 'name="st_pbr"' in both and "PBR materials" in both
    assert '<option value="GLB"' in both
    image_only = await _params(client, "3d", TRELLIS)
    assert 'name="prompt"' not in image_only and "only works from an image" in image_only


# ---- submit ----------------------------------------------------------------------------
async def test_submitting_music_queues_an_audio_job(client, seeded, app, fake):
    await _key(client)
    fake.script["run"] = [[{"audioURL": "http://x/a.mp3", "cost": 0.006}]]
    r = await client.post(
        "/generate/audio",
        data={
            "kind": "audio",
            "project_id": "1",
            "model": ACE,
            "prompt": "rain on a tin roof",
            "duration": "45",
            "st_bpm": "90",
            "st_timeSignature": "",
            "output_format": "WAV",
        },
    )
    assert r.status_code == 200 and 'id="queue-panel"' in r.text
    assert r.headers["HX-Trigger"] == "jobs-changed"
    await app.state.runner.wait_idle()
    kind, status, req, err, _ = _job(app, model_air=ACE)
    assert (kind, status) == ("audio", "succeeded"), err
    assert req["prompt"] == "rain on a tin roof" and req["duration"] == 45
    assert req["settings"] == {"bpm": 90} and req["output_format"] == "WAV"
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert sent["settings"] == {"bpm": 90} and sent["outputFormat"] == "WAV"


async def test_submitting_speech_sends_a_voice_typed_by_hand(client, seeded, app, fake):
    await _key(client)
    fake.script["run"] = [[{"audioURL": "http://x/s.mp3", "cost": 0.001}]]
    r = await client.post(
        "/generate/speech",
        data={"kind": "speech", "project_id": "1", "model": XAI, "text": "Hello.", "voice": "nova"},
    )
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert sent["speech"] == {"text": "Hello.", "voice": "nova"}
    assert _job(app, model_air=XAI)[:2] == ("speech", "succeeded")


async def test_submitting_3d_with_an_image(client, seeded, app, fake):
    await _key(client)
    asset_id = _asset(app)
    fake.script["media_storage"] = [[{"mediaUUID": "m-1"}]]
    fake.script["run"] = [[{"outputs": {"files": [{"url": "http://x/m.glb"}]}, "cost": 0.4}]]
    r = await client.post(
        "/generate/3d",
        data={
            "kind": "3d",
            "project_id": "1",
            "model": TRIPO,
            "source": "image",
            # typed earlier, before switching to "From an image": it must not be sent
            "prompt": "a cup",
            "image_asset_id": str(asset_id),
            "st_pbr": "on",
        },
    )
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert sent["inputs"] == {"images": ["m-1"]} and "positivePrompt" not in sent
    assert _job(app, model_air=TRIPO)[:2] == ("3d", "succeeded")


async def test_validation_errors_come_back_into_the_fields(client, seeded, app):
    await _key(client)
    r = await client.post(
        "/generate/audio", data={"kind": "audio", "project_id": "1", "model": ACE, "prompt": " "}
    )
    assert r.status_code == 422 and r.headers["HX-Retarget"] == "#media-params"
    assert "describe the music or sound you want" in r.text and 'id="media-params"' in r.text
    r = await client.post(
        "/generate/3d", data={"kind": "3d", "project_id": "1", "model": TRELLIS, "prompt": ""}
    )
    assert r.status_code == 422 and "describe the object or pick an image" in r.text
    r = await client.post(
        "/generate/3d",
        data={"kind": "3d", "project_id": "1", "model": HUNYUAN, "image_asset_id": "999"},
    )
    assert r.status_code == 422 and "no longer in your assets" in r.text
    r = await client.post(
        "/generate/speech",
        data={
            "kind": "speech",
            "project_id": "1",
            "model": XAI,
            "text": "hi",
            "extra_json": "{oops",
        },
    )
    assert r.status_code == 422
    assert _job(app) is None  # nothing was queued by any of them


async def test_no_api_key_shows_the_banner_and_queues_nothing(client, seeded, app):
    r = await client.post(
        "/generate/audio", data={"kind": "audio", "project_id": "1", "model": ACE, "prompt": "x y"}
    )
    assert r.status_code == 422 and r.headers["HX-Retarget"] == "#gen-errors"
    assert _job(app) is None


# ---- estimates ---------------------------------------------------------------------------
async def _estimate(client, kind, air, **extra):
    r = await client.post("/hx/media/estimate", data={"kind": kind, "model": air, **extra})
    assert r.status_code == 200 and 'id="media-estimate"' in r.text
    return " ".join(r.text.split())


async def test_estimates_follow_the_price_unit(client, seeded):
    assert "≈$0.0045" in await _estimate(client, "audio", ACE, duration="45")
    assert "≈$0.0060" in await _estimate(client, "audio", ACE)  # the model's own default, 60 s
    assert "≈$0.1500" in await _estimate(client, "audio", MUSIC)
    assert "n/a" in await _estimate(client, "audio", BARE)
    speech = await _estimate(client, "speech", XAI, text="x" * 2000)
    assert "≈$0.0300" in speech and "2000 characters" in speech
    assert "≈$0.3000" in await _estimate(client, "3d", TRIPO, prompt="a cup")
    assert "≈$0.4000" in await _estimate(client, "3d", TRIPO, image_asset_id="3")
    assert "n/a" in await _estimate(client, "3d", TRELLIS)


async def test_unknown_mode_is_a_404_not_a_crash(client, seeded):
    assert (
        await client.post("/hx/media/params", data={"kind": "image", "model": ACE})
    ).status_code == 404
    assert (await client.get("/generate/music")).status_code == 404


# ---- remix and retry ------------------------------------------------------------------------
async def test_remix_of_a_song_opens_the_music_tab_prefilled(client, seeded, app, fake):
    await _key(client)
    fake.script["run"] = [[{"audioURL": "http://x/a.mp3", "seed": 77, "cost": 0.006}]]
    await client.post(
        "/generate/audio",
        data={
            "kind": "audio",
            "project_id": "1",
            "model": MUSIC,
            "prompt": "lo-fi beat",
            "lyrics": "la la",
            "vocals": "lyrics",
        },
    )
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        output_id = s.query(models.Output).one().id
    # an old-style link lands on the right tab instead of the image form
    r = await client.get(f"/generate?remix={output_id}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/generate/audio?remix={output_id}"
    body = (await client.get(f"/generate/audio?remix={output_id}")).text
    assert ">lo-fi beat</textarea>" in body and ">la la</textarea>" in body
    assert f'<option value="{MUSIC}"' in body and 'value="77"' in body
    assert '<option value="lyrics" selected>With vocals: my lyrics below</option>' in body
    assert (await client.get(f"/generate/speech?remix={output_id}")).status_code == 404


async def test_retry_requeues_a_job_as_the_kind_it_was(client, seeded, app, fake):
    await _key(client)
    fake.script["run"] = [[{"audioURL": "http://x/s.mp3"}], [{"audioURL": "http://x/s2.mp3"}]]
    await client.post(
        "/generate/speech",
        data={"kind": "speech", "project_id": "1", "model": XAI, "text": "Hello."},
    )
    await app.state.runner.wait_idle()
    first = _job(app, model_air=XAI)[4]
    r = await client.post(f"/jobs/{first}/retry")
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        jobs = s.query(models.Job).filter_by(model_air=XAI).all()
        assert len(jobs) == 2 and {j.kind for j in jobs} == {"speech"}
        assert all(j.status == "succeeded" for j in jobs)


# ---- gallery, queue card, home strip ---------------------------------------------------
async def _make_three(client, app, fake):
    """One finished song, one spoken text and one 3D object; returns their output ids."""
    await _key(client)
    fake.script["run"] = [
        [{"audioURL": "http://x/a.mp3", "cost": 0.006}],
        [{"audioURL": "http://x/s.mp3", "cost": 0.001}],
        [{"outputs": {"files": [{"url": "http://x/m.glb"}]}, "cost": 0.225}],
    ]
    posts = (
        ("audio", {"model": ACE, "prompt": "rain on a tin roof", "duration": "45"}),
        ("speech", {"model": XAI, "text": "Welcome aboard.", "voice": "luna"}),
        ("3d", {"model": HUNYUAN, "prompt": "a brass telescope"}),
    )
    for kind, data in posts:
        r = await client.post(f"/generate/{kind}", data={"kind": kind, "project_id": "1", **data})
        assert r.status_code == 200, r.text
        await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        return {o.kind: o.id for o in s.query(models.Output).all()}


def _card(body: str, output_id: int) -> str:
    start = body.index(f'id="output-{output_id}"')
    return body[start : body.index("</article>", start)]


async def test_gallery_cards_play_audio_and_open_3d(client, seeded, app, fake):
    ids = await _make_three(client, app, fake)
    assert set(ids) == {"audio", "speech", "3d"}
    body = (await client.get("/gallery")).text
    for kind in ("audio", "speech"):
        card = _card(body, ids[kind])
        assert "<audio src=" in card and ".mp3" in card and "/static/img/audio.svg" in card
        assert "Use as reference" not in card and f'hx-get="/hx/outputs/{ids[kind]}"' in card
    assert "rain on a tin roof" in _card(body, ids["audio"])
    card3d = _card(body, ids["3d"])
    assert "/static/img/model3d.svg" in card3d and "a brass telescope" in card3d
    assert "Use as reference" not in card3d and "<audio" not in card3d


async def test_gallery_filters_by_the_new_kinds(client, seeded, app, fake):
    ids = await _make_three(client, app, fake)
    page = (await client.get("/gallery")).text
    for value in ("audio", "speech", "3d"):
        assert f'<option value="{value}"' in page
    only3d = (await client.get("/gallery?kind=3d")).text
    assert f'id="output-{ids["3d"]}"' in only3d and f'id="output-{ids["audio"]}"' not in only3d
    assert '<option value="3d" selected>3D</option>' in only3d


async def test_details_show_a_player_or_the_3d_viewer(client, seeded, app, fake):
    ids = await _make_three(client, app, fake)
    audio = (await client.get(f"/hx/outputs/{ids['audio']}")).text
    assert '<audio src="' in audio and "<th>Size</th>" not in audio
    assert (
        "<th>Length asked for</th><td>45s</td>" in audio and "<th>Format</th><td>MP3</td>" in audio
    )
    assert "Use as reference" not in audio and f'href="/generate?remix={ids["audio"]}"' in audio
    speech = (await client.get(f"/hx/outputs/{ids['speech']}")).text
    assert "<th>Voice</th><td>luna</td>" in speech and "Welcome aboard." in speech
    model = (await client.get(f"/hx/outputs/{ids['3d']}")).text
    assert "<model-viewer src=" in model and ".glb" in model and "camera-controls" in model
    assert '<script type="module" src="/static/vendor/model-viewer.min.js"></script>' in model
    assert "<th>Format</th><td>GLB</td>" in model and "<th>Size</th>" not in model
    # the viewer is really shipped with the app, and the file itself is served
    viewer = await client.get("/static/vendor/model-viewer.min.js")
    assert viewer.status_code == 200 and len(viewer.content) > 500_000
    url = model.split('<model-viewer src="', 1)[1].split('"', 1)[0]
    assert (await client.get(url)).status_code == 200


async def test_only_images_can_become_references(client, seeded, app, fake):
    ids = await _make_three(client, app, fake)
    for kind in ("audio", "speech", "3d"):
        assert (await client.post(f"/outputs/{ids[kind]}/as-asset")).status_code == 415


async def test_queue_cards_and_the_home_strip_show_an_icon(client, seeded, app, fake):
    await _make_three(client, app, fake)
    panel = (await client.get("/hx/jobs/active")).text
    # music and speech play in the card; a 3D object shows its icon
    assert "<audio " in panel and "/static/img/model3d.svg" in panel
    home = (await client.get("/")).text
    assert "/static/img/audio.svg" in home and "/static/img/model3d.svg" in home


async def test_everything_inside_the_form_that_requests_names_its_own_target(client, seeded):
    """hx-target is inherited. The form targets #queue-panel, so an element inside it
    that issues its own request and names no target swaps its reply INTO THE QUEUE
    PANEL: the estimate did exactly that on its first refresh, the panel was gone, and
    the Generate button then failed with htmx:targetError (seen live, 2026-10-01)."""
    import re

    body = (await client.get("/generate/3d")).text
    form = body[body.index('<form id="media-form"') : body.index("</form>")]
    inner = re.findall(r"<(?!form)[a-z-]+[^>]*\shx-(?:post|get)=[^>]*>", form)
    assert len(inner) >= 2  # the estimate and the model-change wrapper
    for tag in inner:
        assert "hx-target=" in tag, tag
    estimate = (await client.post("/hx/media/estimate", data={"kind": "3d", "model": HUNYUAN})).text
    assert 'hx-target="this"' in estimate


async def test_3d_is_built_from_a_description_or_an_image_never_both(client, seeded, app, fake):
    """Hunyuan takes exactly one of the two; a job sent with both lost its image to a
    free retry and rendered the text instead (seen live). The form now asks which, shows
    only that box, and the server honours the choice whatever else was posted."""
    asset_id = _asset(app)
    both = await _params(client, "3d", HUNYUAN)
    assert 'name="source" value="text"' in both and 'name="source" value="image"' in both
    assert "From a description" in both and "From an image" in both
    # a model that only does one of the two has nothing to choose
    assert 'name="source"' not in await _params(client, "3d", TRELLIS)
    picked = await _params(client, "3d", HUNYUAN, source="image", image_asset_id=str(asset_id))
    assert 'name="source" value="image" checked' in picked

    await _key(client)
    fake.script["run"] = [[{"outputs": {"files": [{"url": "http://x/m.glb"}]}}]]
    r = await client.post(
        "/generate/3d",
        data={
            "kind": "3d",
            "project_id": "1",
            "model": HUNYUAN,
            "source": "text",
            "prompt": "a cup",
            "image_asset_id": str(asset_id),
        },
    )
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert sent["positivePrompt"] == "a cup" and "inputs" not in sent
    # no choice posted at all (a hand-made request) and both filled in: refused
    r = await client.post(
        "/generate/3d",
        data={
            "kind": "3d",
            "project_id": "1",
            "model": HUNYUAN,
            "prompt": "a cup",
            "image_asset_id": str(asset_id),
        },
    )
    assert r.status_code == 422 and "a description or an image, not both" in r.text
    # the estimate follows the choice, not whichever boxes happen to be filled
    est = await _estimate(client, "3d", TRIPO, source="text", prompt="a cup", image_asset_id="3")
    assert "≈$0.3000" in est
    est = await _estimate(client, "3d", TRIPO, source="image", prompt="a cup", image_asset_id="3")
    assert "≈$0.4000" in est


async def test_a_finished_3d_job_card_opens_the_viewer_not_the_raw_file(client, seeded, app, fake):
    """Seen live: the result icon on the job card linked to the .glb itself in a new tab,
    which a browser can only download -- "3D view is not working". It opens the Gallery's
    details (the viewer) instead. Music and speech play right in the card."""
    ids = await _make_three(client, app, fake)
    panel = (await client.get("/hx/jobs/active")).text
    assert f'href="/gallery?open={ids["3d"]}"' in panel
    link = panel[panel.index(f'href="/gallery?open={ids["3d"]}"') - 40 :][:420]
    assert "_blank" not in link and "/static/img/model3d.svg" in link
    # no link to the raw model anywhere in the queue (the snapshot marker may name the file)
    assert not re.search(r'href="[^"]*\.glb"', panel)
    assert panel.count("<audio ") == 2 and ".mp3" in panel


# ---- vocals and lyrics on a model that insists on being told -------------------------------
async def _post_music(client, **extra):
    return await client.post(
        "/generate/audio",
        data={
            "kind": "audio",
            "project_id": "1",
            "model": MUSIC,
            "prompt": "music for a combat",
            **extra,
        },
    )


async def test_music_without_lyrics_is_sent_as_instrumental(client, seeded, app, fake):
    """Seen live: this exact request (a prompt, nothing else) was rejected with "Missing
    required parameter: '[settings][lyrics]'". Lyrics are not needed for music without
    vocals, so the request is completed -- and the stored request says so."""
    await _key(client)
    fake.script["run"] = [[{"audioURL": "http://x/a.mp3", "cost": 0.15}]]
    r = await _post_music(client, vocals="instrumental", lyrics="left in the hidden box")
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert (
        sent["settings"] == {"instrumental": True}
        and sent["positivePrompt"] == "music for a combat"
    )
    kind, status, req, err, _ = _job(app, model_air=MUSIC)
    assert status == "succeeded", err
    assert req["instrumental"] is True and req["lyrics"] == ""
    # a request with no Vocals choice at all (the one that failed) is completed the same way
    fake.script["run"] = [[{"audioURL": "http://x/b.mp3"}]]
    assert (await _post_music(client)).status_code == 200
    await app.state.runner.wait_idle()
    assert [p for n, p in fake.calls if n == "run"][-1]["settings"] == {"instrumental": True}


async def test_vocals_with_my_lyrics_need_the_lyrics_and_nothing_is_sent_without(
    client, seeded, app, fake
):
    await _key(client)
    r = await _post_music(client, vocals="lyrics")
    assert r.status_code == 422 and r.headers["HX-Retarget"] == "#media-params"
    assert "Type the lyrics for a song with vocals" in r.text
    assert '<option value="lyrics" selected>' in r.text  # the choice is kept
    assert _job(app) is None and [c for c in fake.calls if c[0] == "run"] == []
    fake.script["run"] = [[{"audioURL": "http://x/a.mp3"}]]
    assert (
        await _post_music(client, vocals="lyrics", lyrics="[verse] hold the line")
    ).status_code == 200
    await app.state.runner.wait_idle()
    assert [p for n, p in fake.calls if n == "run"][0]["settings"] == {
        "lyrics": "[verse] hold the line"
    }


async def test_the_model_can_write_the_lyrics_itself(client, seeded, app, fake):
    await _key(client)
    fake.script["run"] = [[{"audioURL": "http://x/a.mp3"}]]
    assert (await _post_music(client, vocals="auto")).status_code == 200
    await app.state.runner.wait_idle()
    assert [p for n, p in fake.calls if n == "run"][0]["settings"] == {"lyricsOptimizer": True}
    body = (await client.get("/generate/audio")).text
    assert "Music &amp; SFX" in body


async def test_a_seed_too_big_for_the_model_is_folded_and_the_request_says_so(
    client, seeded, app, fake
):
    """Seen live: 148681975 came along from an ACE-Step run and MiniMax Music (0..1000000)
    rejected it. The job goes out with the seed folded into range and stores that seed."""
    with db.session_scope(app.state.boot.session_factory) as s:
        m = catalog.get_by_air(s, MUSIC)
        c = dict(m.constraints_json)
        c["fields"] = {**c["fields"], "seed": {"type": "integer", "min": 0, "max": 1000000}}
        m.constraints_json = c
    await _key(client)
    fake.script["run"] = [[{"audioURL": "http://x/a.mp3"}]]
    assert (await _post_music(client, vocals="instrumental", seed="148681975")).status_code == 200
    await app.state.runner.wait_idle()
    folded = 148681975 % 1000001
    assert [p for n, p in fake.calls if n == "run"][0]["seed"] == folded
    assert _job(app, model_air=MUSIC)[2]["seed"] == folded
    # the form says what the model's seeds look like
    assert "0–1000000" in await _params(client, "audio", MUSIC)


async def test_the_job_card_words_a_corrected_number_as_what_it_is(client, seeded, app, fake):
    """ "Adjusted size 5×… → 6×…" was written for width/height; a corrected seed or length
    is not a size and must not be printed as one."""
    from runware import RunwareError

    await _key(client)
    e = RunwareError(
        "invalidValue", "Invalid value for 'seed'. 'seed' must be an integer between 0 and 1000000."
    )
    e.parameter = "seed"
    fake.script["run"] = [e, [{"audioURL": "http://x/a.mp3"}]]
    r = await client.post(
        "/generate/audio",
        data={
            "kind": "audio",
            "project_id": "1",
            "model": BARE,
            "prompt": "a drum loop",
            "seed": "148681975",
        },
    )
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    panel = (await client.get("/hx/jobs/active")).text
    assert f"Adjusted seed 148681975 → {148681975 % 1000001}" in panel
    assert "Adjusted size" not in panel


# ---- a picture of each 3D object -------------------------------------------------------
def _shot(size=(640, 480), mode="RGBA", colour=(200, 60, 40, 255)) -> bytes:
    b = io.BytesIO()
    Image.new(mode, size, colour).save(b, "PNG")
    return b.getvalue()


async def _one_3d(client, app, fake, **data):
    await _key(client)
    fake.script["run"] = [[{"outputs": {"files": [{"url": "http://x/m.glb"}]}, "cost": 0.225}]]
    r = await client.post(
        "/generate/3d", data={"kind": "3d", "project_id": "1", "model": HUNYUAN, **data}
    )
    assert r.status_code == 200, r.text
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        return (
            s.query(models.Output).filter_by(kind="3d").order_by(models.Output.id.desc()).first().id
        )


def _output(app, output_id):
    with db.session_scope(app.state.boot.session_factory) as s:
        o = s.get(models.Output, output_id)
        return o.thumb_rel_path, dict(o.params_json or {})


async def test_a_3d_object_made_from_text_asks_the_browser_for_its_picture(
    client, seeded, app, fake
):
    """RunWare returns only the .glb, so the picture is a snapshot the viewer takes in
    the browser. Until one exists the card shows the cube icon and carries what the
    snapshot script needs: the output id and the file to render."""
    oid = await _one_3d(client, app, fake, source="text", prompt="a brass telescope")
    assert _output(app, oid)[0] is None
    card = _card((await client.get("/gallery")).text, oid)
    assert "/static/img/model3d.svg" in card
    assert f'data-snap3d-id="{oid}"' in card and 'data-snap3d-src="/files/outputs/' in card
    panel = (await client.get("/hx/jobs/active")).text
    assert f'data-snap3d-id="{oid}"' in panel


async def test_the_viewer_s_snapshot_becomes_the_thumbnail(client, seeded, app, fake):
    oid = await _one_3d(client, app, fake, source="text", prompt="a brass telescope")
    r = await client.post(
        f"/outputs/{oid}/thumb", files={"image": ("shot.png", _shot(), "image/png")}
    )
    assert r.status_code == 200 and r.json()["kept"] is False
    url = r.json()["thumb_url"]
    assert url.startswith("/files/thumbs/") and url.endswith(".jpg")
    served = await client.get(url)
    assert served.status_code == 200
    im = Image.open(io.BytesIO(served.content))
    assert im.format == "JPEG" and max(im.size) <= 384
    rel, params = _output(app, oid)
    assert rel and params["thumb_source"] == "viewer"
    # everywhere the object appears now shows it, and nobody is asked for a snapshot again
    card = _card((await client.get("/gallery")).text, oid)
    assert url in card and "model3d.svg" not in card and "data-snap3d-id" not in card
    assert url in (await client.get("/hx/jobs/active")).text
    assert url in (await client.get("/")).text
    # a second browser arriving late does not overwrite it
    again = await client.post(
        f"/outputs/{oid}/thumb",
        files={"image": ("s.png", _shot(colour=(0, 0, 0, 255)), "image/png")},
    )
    assert again.status_code == 200 and again.json() == {"thumb_url": url, "kept": True}


async def test_an_object_built_from_a_picture_shows_that_picture_until_the_snapshot(
    client, seeded, app, fake
):
    asset_id = _asset(app)
    fake.script["media_storage"] = [[{"mediaUUID": "m-1"}]]
    oid = await _one_3d(client, app, fake, source="image", image_asset_id=str(asset_id))
    rel, params = _output(app, oid)
    assert rel and params["thumb_source"] == "input"
    card = _card((await client.get("/gallery")).text, oid)
    assert "/files/thumbs/" in card and "model3d.svg" not in card
    assert f'data-snap3d-id="{oid}"' in card  # the stand-in is still to be replaced
    r = await client.post(
        f"/outputs/{oid}/thumb", files={"image": ("shot.png", _shot(), "image/png")}
    )
    assert r.status_code == 200 and r.json()["kept"] is False
    assert _output(app, oid)[1]["thumb_source"] == "viewer"


async def test_the_snapshot_route_only_takes_a_real_image_for_a_3d_object(
    client, seeded, app, fake
):
    ids = await _make_three(client, app, fake)
    post = lambda oid, content, name="shot.png": client.post(  # noqa: E731
        f"/outputs/{oid}/thumb", files={"image": (name, content, "image/png")}
    )
    assert (await post(ids["audio"], _shot())).status_code == 415
    assert (await post(999999, _shot())).status_code == 404
    assert (await post(ids["3d"], b"not an image at all")).status_code == 400
    assert (await post(ids["3d"], b"x" * (9 * 1024 * 1024))).status_code == 413
    assert (await client.post(f"/outputs/{ids['3d']}/thumb")).status_code == 400
    assert _output(app, ids["3d"])[0] is None  # none of that left a thumbnail behind


async def test_a_long_voice_list_gets_a_filter_box_and_keeps_an_unlisted_voice(client, seeded, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        m = catalog.get_by_air(s, INWORLD)
        c = dict(m.constraints_json)
        voices = [f"Voice{i:03d}" for i in range(133)]
        c["fields"] = {**c["fields"], "speech.voice": {"values": voices}}
        m.constraints_json = c
    body = await _params(client, "speech", INWORLD)
    assert body.count('<option value="Voice') == 133 and "133 voices" in body
    assert 'data-filter-select="voice-select"' in body and "Filter 133 voices" in body
    # no default on this model: nothing is preselected, and "Model default" sends no voice
    assert '<option value="" selected>Model default</option>' in body
    # switching model drops a voice that belonged to the previous one (seen in the browser:
    # xAI's "eve" rode along into Inworld's list and would have been sent and rejected)
    switched = await _params(client, "speech", INWORLD, voice="eve", language="en")
    assert 'value="eve"' not in switched and switched.count('<option value="Voice') == 133
    assert '<option value="" selected>Model default</option>' in switched
    # ...but one that IS on the new model's list is kept
    kept = await _params(client, "speech", INWORLD, voice="Voice007")
    assert '<option value="Voice007" selected>Voice007</option>' in kept


# ---- several views for one 3D object ---------------------------------------------------
def _assets(app, n: int) -> list[int]:
    ids = []
    with db.session_scope(app.state.boot.session_factory) as s:
        for i in range(n):
            b = io.BytesIO()
            Image.new("RGB", (64, 64), (10 * i, 20, 30)).save(b, "PNG")
            a, _ = assets.store_upload(
                s,
                app.state.paths,
                original_name=f"view{i}.png",
                content=b.getvalue(),
                mime="image/png",
            )
            ids.append(a.id)
    return ids


async def test_a_multi_view_model_offers_checkboxes_and_a_single_view_model_a_single_choice(
    client, seeded, app
):
    a, b = _assets(app, 2)
    multi = await _params(client, "3d", TRIPO, source="image")
    assert f'type="checkbox" name="image_asset_ids" value="{a}"' in multi
    assert 'data-max-images="4"' in multi and "up to 4 views" in multi
    assert "first one you pick is the main" in multi
    single = await _params(client, "3d", HUNYUAN, source="image")
    assert f'type="radio" name="image_asset_id" value="{a}"' in single
    assert 'name="image_asset_ids"' not in single and "up to" not in single
    # what was ticked survives a re-render, in the order it was picked
    again = await _params(
        client,
        "3d",
        TRIPO,
        source="image",
        image_order=f"{b},{a}",
        image_asset_ids=[str(a), str(b)],
    )
    assert f'name="image_order" value="{b},{a}"' in again
    assert again.count(" checked") >= 2


async def test_several_views_are_sent_in_the_order_they_were_picked(client, seeded, app, fake):
    a, b, c = _assets(app, 3)
    await _key(client)
    fake.script["media_storage"] = [[{"mediaUUID": f"m-{i}"}] for i in range(3)]
    fake.script["run"] = [[{"outputs": {"files": [{"url": "http://x/m.glb"}]}, "cost": 0.4}]]
    r = await client.post(
        "/generate/3d",
        data={
            "kind": "3d",
            "project_id": "1",
            "model": TRIPO,
            "source": "image",
            # ticked in the order c, a, b; the checkboxes themselves post in page order
            "image_order": f"{c},{a},{b}",
            "image_asset_ids": [str(a), str(b), str(c)],
        },
    )
    assert r.status_code == 200, r.text
    await app.state.runner.wait_idle()
    kind, status, req, err, _ = _job(app, model_air=TRIPO)
    assert status == "succeeded", err
    assert req["image_asset_ids"] == [c, a, b] and req["image_asset_id"] == c
    uploads = [p for n, p in fake.calls if n == "media_storage"]
    assert len(uploads) == 3
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert sent["inputs"] == {"images": ["m-0", "m-1", "m-2"]}  # uploaded, and sent, in pick order
    # the stand-in thumbnail is the first (main) view
    with db.session_scope(app.state.boot.session_factory) as s:
        o = s.query(models.Output).filter_by(kind="3d").one()
        assert o.thumb_rel_path and o.params_json["thumb_source"] == "input"


async def test_more_views_than_the_model_takes_are_refused_before_anything_is_sent(
    client, seeded, app, fake
):
    ids = _assets(app, 5)
    await _key(client)
    post = lambda model, picked: client.post(  # noqa: E731
        "/generate/3d",
        data={
            "kind": "3d",
            "project_id": "1",
            "model": model,
            "source": "image",
            "image_asset_ids": [str(i) for i in picked],
        },
    )
    r = await post(TRIPO, ids)
    assert r.status_code == 422 and "takes at most 4 images" in r.text
    r = await post(TRIPO, [ids[0], 999999])
    assert r.status_code == 422 and "no longer in your assets" in r.text
    assert _job(app) is None and [c for c in fake.calls if c[0] in ("run", "media_storage")] == []
    # one view on a multi-view model is still fine
    fake.script["media_storage"] = [[{"mediaUUID": "m-1"}]]
    fake.script["run"] = [[{"outputs": {"files": [{"url": "http://x/m.glb"}]}}]]
    assert (await post(TRIPO, [ids[0]])).status_code == 200
    await app.state.runner.wait_idle()
    assert [p for n, p in fake.calls if n == "run"][0]["inputs"] == {"images": ["m-1"]}


async def test_every_gallery_card_has_remix_and_download_whatever_its_kind(
    client, seeded, app, fake
):
    """Remix and Download used to be inside the details panel only; music, speech and 3D
    cards showed no way to get the file at all. Every card carries both now, and the
    download really serves the file as an attachment for each kind."""
    ids = await _make_three(client, app, fake)
    fake.script["run"] = [[{"imageURL": "http://x/i.png", "seed": 1, "cost": 0.001}]]
    r = await client.post(
        "/generate/image",
        data={
            "project_id": "1",
            "model": "runware:101@1",
            "subject": "a fox",
            "width": "1024",
            "height": "1024",
        },
    )
    assert r.status_code == 200, r.text
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        ids["image"] = s.query(models.Output).filter_by(kind="image").one().id
    body = (await client.get("/gallery")).text
    for kind, oid in ids.items():
        card = _card(body, oid)
        assert f'href="/generate?remix={oid}"' in card and ">Remix</a>" in card, kind
        assert f'href="/outputs/{oid}/download"' in card and ">Download</a>" in card, kind
        got = await client.get(f"/outputs/{oid}/download", follow_redirects=True)
        assert got.status_code == 200 and "attachment" in got.headers["content-disposition"], kind
    # Remix lands on the tab that made it
    r = await client.get(f"/generate?remix={ids['3d']}", follow_redirects=False)
    assert r.headers["location"] == f"/generate/3d?remix={ids['3d']}"
    # ...and each card can be deleted where it stands, after a confirmation
    for kind, oid in ids.items():
        card = _card(body, oid)
        assert f'hx-delete="/outputs/{oid}"' in card and "hx-confirm=" in card, kind
        assert f'hx-target="#output-{oid}"' in card and 'hx-swap="outerHTML"' in card
    gone = ids["audio"]
    assert (await client.delete(f"/outputs/{gone}")).status_code == 200
    after = (await client.get("/gallery")).text
    assert f'id="output-{gone}"' not in after and f'id="output-{ids["3d"]}"' in after


async def test_the_3d_viewer_has_a_full_screen_button(client, seeded, app, fake):
    """The viewer sits in a stage with a Full screen button inside it, so the button stays
    on screen (and becomes the way back) once the stage fills the display."""
    ids = await _make_three(client, app, fake)
    detail = (await client.get(f"/hx/outputs/{ids['3d']}")).text
    stage = detail[detail.index('class="model-stage"') :]
    stage = stage[: stage.index("</div>")]
    assert "<model-viewer" in stage and 'class="lightbox-model"' in stage
    assert 'data-fullscreen="model-stage"' in stage and ">Full screen</button>" in stage
    assert 'aria-pressed="false"' in stage
    # only 3D objects get it
    assert "data-fullscreen" not in (await client.get(f"/hx/outputs/{ids['audio']}")).text
    js = (await client.get("/static/js/app.js")).text
    assert "requestFullscreen" in js and "fullscreenchange" in js and "is-maximized" in js
