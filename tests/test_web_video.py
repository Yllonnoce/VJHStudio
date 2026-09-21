"""The Generate page's Video mode, the video estimate, reference chips and the
gallery's video cards. The fake client returns a ``videoURL``; conftest's download
MockTransport serves bytes for any URL, so the runner saves a real (if tiny) file."""

import io
import json
import re

from PIL import Image
from starlette.datastructures import FormData

from vjhstudio import db, models
from vjhstudio.web.routes.generate import parse_video_request

VEO = "google:3@2"
LTX = "lightricks:ltx@2.3"
AUDIO_SCHEMA = [
    {"key": "generateAudio", "label": "Generate audio", "type": "bool", "default": True}
]

FORM = {
    "project_id": "1",
    "model": VEO,
    "subject": "a fox running",
    "duration": "5",
    "resolution": "720p",
    "output_format": "MP4",
}


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (32, 32), (9, 9, 9)).save(b, "PNG")
    return b.getvalue()


async def _key(client):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})


async def _run_video(client, fake, app, **extra):
    await _key(client)
    fake.script["run"] = [[{"videoURL": "http://x/v.mp4", "cost": 0.8}]]
    r = await client.post("/generate/video", data={**FORM, **extra})
    await app.state.runner.wait_idle()
    return r


# ---- page ----------------------------------------------------------------
async def test_video_mode_page_renders_video_models_and_duration(client):
    r = await client.get("/generate?mode=video")
    assert r.status_code == 200
    assert r.text.count('name="duration"') == 1
    assert "Veo 3.1" in r.text and "LTX-2.3" in r.text
    assert 'name="steps"' not in r.text  # image-only parameter
    assert 'id="ref-picker"' in r.text
    # video flips PromptForm's no_text default, so the box renders unticked
    assert 'name="no_text" value="on" x-model="noText" checked' not in r.text


async def test_image_mode_still_ticks_no_text(client):
    r = await client.get("/generate")
    assert 'name="no_text" value="on" x-model="noText" checked' in r.text


async def test_form_keeps_a_default_submit_button_for_the_enter_key(client):
    """The per-mode submits are type="button", so without this the form has no default
    button and pressing Enter would do nothing at all."""
    hidden = '<button type="submit" hidden tabindex="-1" aria-hidden="true"></button>'
    assert hidden in (await client.get("/generate")).text
    assert hidden in (await client.get("/generate?mode=video")).text


async def test_both_mode_submits_ask_htmx_to_validate(client):
    for url in ("/generate", "/generate?mode=video"):
        text = (await client.get(url)).text
        assert text.count('hx-validate="true"') == 2
        assert 'hx-post="/generate/image"' in text and 'hx-post="/generate/video"' in text


async def test_no_text_checkbox_is_hidden_in_video_mode(client):
    text = (await client.get("/generate")).text
    assert '<label x-show="mode === \'image\'"><input type="hidden" name="no_text"' in text


async def test_generate_video_alias_renders_the_video_tab(client):
    r = await client.get("/generate/video")
    assert r.status_code == 200 and r.text.count('name="duration"') == 1


async def test_image_mode_is_still_the_default_and_carries_ref_chips(client):
    r = await client.get("/generate")
    assert 'name="steps"' in r.text and 'name="duration"' not in r.text
    assert 'id="ref-picker"' in r.text and "seed_image_asset_id" in r.text
    assert "reference_asset_ids" in r.text


async def test_model_options_video_vs_image(client):
    r = await client.get(f"/hx/model-options?mode=video&air={LTX}")
    assert 'name="fps"' in r.text and 'name="duration"' in r.text
    assert 'id="model-params"' in r.text
    r = await client.get(f"/hx/model-options?mode=video&air={VEO}")
    assert 'name="ps_generateAudio"' in r.text and 'name="fps"' not in r.text
    r = await client.get("/hx/model-options?air=runware:101@1")
    assert 'name="steps"' in r.text and 'name="duration"' not in r.text


# ---- estimate ------------------------------------------------------------
async def test_video_estimate_uses_duration_and_the_audio_rate(client):
    r = await client.get(f"/hx/generate/estimate?mode=video&air={VEO}&duration=5")
    assert "$1.00" in r.text
    r = await client.get(
        f"/hx/generate/estimate?mode=video&air={VEO}&duration=5"
        "&ps_generateAudio=off&ps_generateAudio=on"
    )
    assert "$2.00" in r.text
    r = await client.get("/hx/generate/estimate?mode=video&air=runware:100@1&duration=5")
    assert "n/a" in r.text


async def test_first_render_prices_the_duration_the_select_defaults_to(client):
    """Veo offers 4/6/8 s, so its select opens on 4 — the estimate beside it must not
    keep quoting the schema's generic 5 s."""
    await client.post("/settings", data={"defaults.video_model": VEO})
    r = await client.get("/generate?mode=video")
    assert 'value="4" selected' in r.text
    # Veo's generateAudio defaults to on, so the ticked box's $0.40/s rate is the one
    # the form would actually submit: 4 s x $0.40. The pre-fix bug quoted 5 s ($2.00).
    assert "$1.60" in r.text and "$2.00" not in r.text
    r = await client.get(f"/hx/generate/estimate?mode=video&air={VEO}&duration=4")
    assert "$0.80" in r.text  # the same 4 s, with the audio box cleared


async def test_estimate_uses_the_resolution_tier(client):
    r = await client.get(f"/hx/generate/estimate?mode=video&air={LTX}&duration=5&resolution=1080p")
    assert "$0.40" in r.text  # LTX is $0.04/s at 720p and $0.08/s at 1080p
    r = await client.get(f"/hx/generate/estimate?mode=video&air={LTX}&duration=5&resolution=720p")
    assert "$0.20" in r.text
    # Veo prices one rate for "720p / 1080p", split by audio instead
    r = await client.get(
        f"/hx/generate/estimate?mode=video&air={VEO}&duration=5&resolution=1080p"
        "&ps_generateAudio=off&ps_generateAudio=on"
    )
    assert "$2.00" in r.text


async def test_video_estimate_tolerates_a_blank_duration(client):
    r = await client.get(f"/hx/generate/estimate?mode=video&air={VEO}&duration=")
    assert r.status_code == 200 and 'id="estimate"' in r.text
    r = await client.get("/hx/generate/estimate?air=runware:101@1&width=1024&height=1024")
    assert "$0.0038" in r.text  # the image path is untouched


# ---- form parsing --------------------------------------------------------
def test_parse_video_request_reads_provider_settings_and_refs():
    form = FormData(
        [
            ("project_id", "1"),
            ("model", VEO),
            ("subject", "x"),
            ("ps_generateAudio", "off"),
            ("ps_generateAudio", "on"),
            ("first_frame_asset_id", "3"),
            ("last_frame_asset_id", "4"),
            ("reference_asset_ids", "5"),
            ("reference_asset_ids", ""),
            ("reference_asset_ids", "6"),
        ]
    )
    req = parse_video_request(form, AUDIO_SCHEMA)
    assert req.provider_settings == {"generateAudio": True}
    assert req.first_frame_asset_id == 3 and req.last_frame_asset_id == 4
    assert req.reference_asset_ids == [5, 6]
    assert req.form.no_text is False  # video flips PromptForm's default


def test_parse_video_request_unchecked_bool_posts_false():
    form = FormData([("project_id", "1"), ("model", VEO), ("ps_generateAudio", "off")])
    assert parse_video_request(form, AUDIO_SCHEMA).provider_settings == {"generateAudio": False}
    form = FormData([("project_id", "1"), ("model", VEO)])
    assert parse_video_request(form, AUDIO_SCHEMA).provider_settings == {"generateAudio": True}


# ---- submit --------------------------------------------------------------
async def test_submit_video_without_a_key_shows_the_no_key_banner(client):
    r = await client.post("/generate/video", data=FORM)
    assert r.status_code == 422 and "API key" in r.text
    assert r.headers.get("HX-Retarget") == "#gen-errors"


async def test_submit_video_invalid_duration_retargets_the_params(client):
    await _key(client)
    r = await client.post("/generate/video", data={**FORM, "duration": "99"})
    assert r.status_code == 422 and r.headers.get("HX-Retarget") == "#model-params"
    assert "between 1 and 30" in r.text and 'name="duration"' in r.text


async def test_submit_video_rejects_an_image_model(client):
    await _key(client)
    r = await client.post("/generate/video", data={**FORM, "model": "runware:101@1"})
    assert r.status_code == 422 and "is not a video model" in r.text


async def test_submit_video_runs_the_job_and_saves_an_mp4(client, fake, app):
    r = await _run_video(client, fake, app)
    assert r.status_code == 200 and "jobs-changed" in r.headers.get("HX-Trigger", "")
    rows = (await client.get("/api/jobs")).json()
    assert rows[0]["status"] == "succeeded" and rows[0]["kind"] == "video"
    url = rows[0]["outputs"][0]["url"]
    assert url.endswith(".mp4")
    assert rows[0]["outputs"][0]["thumb_url"] is None
    got = await client.get(url + "?download=1")
    assert got.status_code == 200 and "attachment" in got.headers.get("content-disposition", "")


async def test_video_job_card_says_stop_waiting(client, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        s.add(
            models.Job(
                id="job-video-queued",
                project_id=1,
                kind="video",
                status=models.JobStatus.queued.value,
                model_air=VEO,
                request_json={"project_id": 1, "model": VEO},
                title="a fox running",
            )
        )
    r = await client.get("/hx/jobs/active")
    assert "Stop waiting" in r.text and "Cancel</button>" not in r.text


# ---- gallery -------------------------------------------------------------
async def test_gallery_video_card_and_detail(client, fake, app):
    await _run_video(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get("/hx/gallery?kind=video")
    assert "<video" in r.text and "video-poster.svg" in r.text
    assert (await client.get("/hx/gallery?kind=image")).text.count("<video") == 0
    r = await client.get(f"/hx/outputs/{oid}")
    assert "<video" in r.text and "Remix" in r.text and "Download" in r.text
    assert "Use as reference" not in r.text


# ---- remix ---------------------------------------------------------------
async def test_remix_of_a_video_output_prefills_the_video_tab(client, fake, app):
    await _run_video(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/generate?remix={oid}")
    assert r.status_code == 200
    assert '"mode": "video"' in r.text and '"duration": 5' in r.text
    assert r.text.count('name="duration"') == 1


async def test_remix_marks_the_remixed_duration_selected(client, fake, app):
    """Regression: the full page used to hand the params partial ``params.values`` via a
    Jinja ``{% with %}``; since ``params`` is a plain dict, attribute-first lookup found
    the dict's built-in ``.values`` method instead of the ``"values"`` key, so remixed
    values (including duration) never reached the rendered form -- only the
    /hx/model-options partial (rendered directly, no dict wrapping) was unaffected."""
    r = await _run_video(client, fake, app, model=LTX, duration="5")
    assert r.status_code == 200
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/generate?remix={oid}")
    assert r.status_code == 200
    assert re.search(r'<option value="5"[^>]*\bselected\b[^>]*>', r.text)


async def test_remix_resolves_frame_assets_into_ref_chips(client, fake, app):
    up = await client.post("/assets/upload", files=[("files", ("first.png", _png(), "image/png"))])
    assert up.status_code == 200
    await _run_video(client, fake, app)
    with db.session_scope(app.state.boot.session_factory) as s:
        asset_id = s.query(models.Asset).one().id
        job = s.query(models.Job).one()
        job.request_json = dict(job.request_json) | {"first_frame_asset_id": asset_id}
        oid = s.query(models.Output).one().id
    r = await client.get(f"/generate?remix={oid}")
    assert '"role": "first"' in r.text and "first.png" in r.text
    assert "/files/asset-thumbs/" in r.text


async def test_video_submit_links_a_video_prompt_row(client, fake, app):
    r = await _run_video(client, fake, app)
    assert r.status_code == 200
    with db.session_scope(app.state.boot.session_factory) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.kind == "video" and prompt.use_count == 1
        assert prompt.composed_prompt == "a fox running"
        # video requests omit negativePrompt, so the row records no negative either
        assert prompt.negative_prompt == ""
        assert s.query(models.Job).one().prompt_id == prompt.id


async def test_a_saved_video_prompt_dedupes_against_the_generated_one(client, fake, app):
    """The save route records no negative for a video prompt (the request omits one) and
    defaults ``no_text`` the way ``parse_video_request`` does, so one row serves both."""
    save = {
        "project_id": "1",
        "mode": "video",
        "subject": "a fox running",
        "title": "Fox clip",
    }
    r = await client.post("/prompts", data=save)
    assert r.status_code == 200
    pid = json.loads(r.headers["HX-Trigger"])["prompt-saved"]["id"]
    await _run_video(client, fake, app)
    with db.session_scope(app.state.boot.session_factory) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.id == pid and prompt.kind == "video" and prompt.use_count == 1
        assert s.query(models.Job).one().prompt_id == pid
