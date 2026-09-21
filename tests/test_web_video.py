"""The Generate page's Video mode, the video estimate, reference chips and the
gallery's video cards. The fake client returns a ``videoURL``; conftest's download
MockTransport serves bytes for any URL, so the runner saves a real (if tiny) file."""

import io

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
