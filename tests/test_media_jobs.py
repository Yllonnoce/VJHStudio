"""Music & SFX, speech and 3D jobs through the real pipeline (enqueue -> JobRunner ->
runner policy -> download -> outputs), with FakeRunware and a mock download transport."""

import io

import httpx
import pytest
from PIL import Image

from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio import boot, config, db, models
from vjhstudio.runware import results
from vjhstudio.schemas import media as M
from vjhstudio.services import assets, catalog, generate, jobs

ACE = "runware:ace-step@v1.5-turbo"
COVER = "minimax:music@cover"
XAI = "xai:tts@0"
HUNYUAN = "tencent:hunyuan-3d@3.1-rapid"
TRELLIS = "microsoft:trellis-2@4b"
TEXT_ONLY_3D = "vjh:text-3d@1"
BYTES = b"\x00" * 64


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (1, 2, 3)).save(b, "PNG")
    return b.getvalue()


def _row(f, air, kind, caps, constraints_json=None, price=None, unit=None):
    with db.session_scope(f) as s:
        m = catalog.get_by_air(s, air)
        if m is None:
            m = models.CatalogModel(air=air, name=air, kind=kind, source="curated")
            s.add(m)
        m.kind = kind
        m.capabilities_json = caps
        m.constraints_json = constraints_json
        m.price_primary = price
        m.price_unit = unit


@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    f = info.session_factory
    _row(
        f,
        ACE,
        "audio",
        ["io:text-to-audio", "io:audio-to-audio"],
        {"fields": {"positivePrompt": {}, "duration": {"min": 30, "max": 300, "default": 60}}},
        0.0001,
        "per_second",
    )
    _row(f, COVER, "audio", ["io:audio-to-audio"])
    _row(
        f, XAI, "speech", ["io:text-to-audio"], {"fields": {"speech.text": {}, "speech.voice": {}}}
    )
    _row(
        f,
        HUNYUAN,
        "3d",
        ["io:text-to-3d", "io:image-to-3d"],
        {"fields": {"positivePrompt": {}}, "inputs": {"image": {"required": False}}},
    )
    _row(f, TRELLIS, "3d", ["io:image-to-3d"], {"inputs": {"image": {"required": True}}})
    _row(f, TEXT_ONLY_3D, "3d", ["io:text-to-3d"])
    return paths, f


def _runner(env, fake):
    paths, f = env
    t = httpx.MockTransport(lambda r: httpx.Response(200, content=BYTES))
    return jobs.JobRunner(
        f,
        paths,
        client_factory=fake_factory(fake),
        api_key_getter=lambda: "key",
        transport_getter=lambda: "rest",
        concurrency=1,
        download_transport=t,
    )


def _pid(f) -> int:
    with db.session_scope(f) as s:
        return s.query(models.Project).filter_by(slug="default").one().id


async def _run(env, fake, kind, req):
    paths, f = env
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_media(f, paths, kind, req)
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    return job.id


def _asset(env) -> int:
    paths, f = env
    with db.session_scope(f) as s:
        a, _ = assets.store_upload(
            s, paths, original_name="a.png", content=_png(), mime="image/png"
        )
        return a.id


# ---- reply parsing -------------------------------------------------------------------
def test_parse_items_reads_an_audio_reply():
    items = results.parse_items([{"audioURL": "http://x/a.mp3", "audioUUID": "u1", "cost": 0.006}])
    assert (items[0].url, items[0].uuid, items[0].cost) == ("http://x/a.mp3", "u1", 0.006)


def test_parse_items_finds_the_3d_file_inside_outputs_whatever_its_shape():
    """``outputs`` is undocumented, so every plausible shape must yield the model file."""
    shapes = [
        {"files": [{"url": "https://x/m.glb", "format": "GLB"}]},
        {"glb": "https://x/m.glb"},
        {"model": {"url": "https://x/m.glb?sig=abc"}},
        {"preview": "https://x/preview.png", "files": [{"url": "https://x/m.glb"}]},
        [{"url": "https://x/m.glb"}],
    ]
    for outputs in shapes:
        items = results.parse_items([{"taskType": "3dInference", "outputs": outputs, "cost": 0.2}])
        assert items and items[0].url.startswith("https://x/m.glb"), outputs
    # no model-looking URL: the first URL is better than nothing
    assert results.outputs_url({"file": "https://x/blob/123"}) == "https://x/blob/123"
    assert results.outputs_url({"status": "ok"}) is None and results.outputs_url(None) is None
    assert results.parse_items([{"taskType": "3dInference", "outputs": {}}]) == []


# ---- jobs end to end -------------------------------------------------------------------
async def test_a_music_job_saves_an_mp3_with_its_cost_and_length(env):
    paths, f = env
    fake = FakeRunware(
        {"run": [[{"audioURL": "http://x/a.mp3", "audioUUID": "u", "cost": 0.0045}]]}
    )
    req = M.AudioRequest(project_id=_pid(f), model=ACE, prompt="rain on a tin roof", duration=45)
    job_id = await _run(env, fake, "audio", req)
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert sent["taskType"] == "audioInference" and sent["duration"] == 45
    with db.session_scope(f) as s:
        j = s.get(models.Job, job_id)
        assert j.status == "succeeded", j.error_message
        assert j.kind == "audio" and j.title == "rain on a tin roof" and j.cost == 0.0045
        o = s.query(models.Output).filter_by(job_id=job_id).one()
        assert o.kind == "audio" and o.filename.endswith(".mp3")
        assert o.thumb_rel_path is None and o.poster_rel_path is None
        assert (o.width, o.height, o.duration_s) == (None, None, 45)
        assert o.prompt_text == "rain on a tin roof" and o.cost == 0.0045
        usage = s.query(models.UsageEntry).filter_by(job_id=job_id).one()
        assert usage.task_type == "audioInference" and usage.cost == 0.0045


async def test_a_speech_job_keeps_the_text_as_its_prompt(env):
    paths, f = env
    fake = FakeRunware({"run": [[{"audioURL": "http://x/s.wav", "cost": 0.001}]]})
    req = M.SpeechRequest(
        project_id=_pid(f), model=XAI, text="Welcome aboard.", voice="eve", output_format="WAV"
    )
    job_id = await _run(env, fake, "speech", req)
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert sent["speech"] == {"text": "Welcome aboard.", "voice": "eve"}
    with db.session_scope(f) as s:
        assert s.get(models.Job, job_id).status == "succeeded"
        o = s.query(models.Output).filter_by(job_id=job_id).one()
        assert o.kind == "speech" and o.filename.endswith(".wav")
        assert o.prompt_text == "Welcome aboard." and o.duration_s is None
        assert o.params_json["voice"] == "eve"


async def test_a_3d_job_uploads_its_image_and_saves_a_glb(env):
    paths, f = env
    asset_id = _asset(env)
    fake = FakeRunware(
        {
            "media_storage": [[{"mediaUUID": "m-1"}]],
            "run": [
                [
                    {
                        "taskType": "3dInference",
                        "outputs": {"files": [{"url": "http://x/m.glb"}]},
                        "cost": 0.225,
                    }
                ]
            ],
        }
    )
    req = M.Model3DRequest(project_id=_pid(f), model=HUNYUAN, image_asset_id=asset_id)
    job_id = await _run(env, fake, "3d", req)
    sent = [p for n, p in fake.calls if n == "run"][0]
    assert sent["taskType"] == "3dInference" and sent["inputs"] == {"image": "m-1"}
    with db.session_scope(f) as s:
        j = s.get(models.Job, job_id)
        assert j.status == "succeeded", j.error_message
        o = s.query(models.Output).filter_by(job_id=job_id).one()
        assert o.kind == "3d" and o.filename.endswith(".glb") and o.cost == 0.225
        assert o.prompt_text == "3D object from an image"


async def test_a_reply_with_no_file_fails_the_job_and_says_what_came_back(env):
    paths, f = env
    fake = FakeRunware(
        {"run": [[{"taskType": "3dInference", "outputs": {"status": "done"}, "cost": 0.2}]]}
    )
    req = M.Model3DRequest(project_id=_pid(f), model=HUNYUAN, prompt="a cup")
    job_id = await _run(env, fake, "3d", req)
    with db.session_scope(f) as s:
        j = s.get(models.Job, job_id)
        assert j.status == "failed"
        assert "no file to download" in j.error_message and "outputs" in j.error_message
        assert s.query(models.Output).filter_by(job_id=job_id).count() == 0


# ---- pre-flight -------------------------------------------------------------------------
def test_preflight_refuses_before_a_job_row_exists(env):
    paths, f = env
    pid = _pid(f)
    cases = [
        (
            "audio",
            M.AudioRequest(project_id=pid, model=COVER, prompt="a cover"),
            "needs an audio track",
        ),
        (
            "audio",
            M.AudioRequest(project_id=pid, model=XAI, prompt="a song"),
            "is not a music or sound-effect model",
        ),
        ("3d", M.Model3DRequest(project_id=pid, model=ACE, prompt="a cup"), "is not a 3D model"),
        (
            "3d",
            M.Model3DRequest(project_id=pid, model=TRELLIS, prompt="a cup"),
            "builds a 3D object from an image",
        ),
        (
            "3d",
            M.Model3DRequest(project_id=pid, model=TEXT_ONLY_3D, image_asset_id=1),
            "does not take an image",
        ),
        (
            "3d",
            M.Model3DRequest(project_id=pid, model=HUNYUAN, image_asset_id=999),
            "no longer in your assets",
        ),
        (
            "speech",
            M.SpeechRequest(project_id=999, model=XAI, text="hi"),
            "project 999 does not exist",
        ),
    ]
    for kind, req, message in cases:
        with pytest.raises(ValueError, match=message):
            generate.enqueue_media(f, paths, kind, req)
    with pytest.raises(ValueError, match="not a media kind"):
        generate.enqueue_media(f, paths, "image", cases[0][1])
    with db.session_scope(f) as s:
        assert s.query(models.Job).count() == 0


# ---- a provider failure says what the provider actually said -------------------------
PROVIDER_MSG = "hunyuan responded with HTTP . Additional information below."
JOB_LIMIT = (
    "RequestLimitExceeded.JobNumExceed: The task limit has been reached. Please try again "
    "later. (RequestId: 8e994818)"
)


def _detail_runner(env, fake, reply: dict, seen: list):
    """A runner whose out-of-band HTTP (downloads, and the error-detail lookup) is mocked."""
    paths, f = env

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization"), request.content))
        return httpx.Response(400, json=reply)

    return jobs.JobRunner(
        f,
        paths,
        client_factory=fake_factory(fake),
        api_key_getter=lambda: "key",
        transport_getter=lambda: "rest",
        concurrency=1,
        download_transport=httpx.MockTransport(handler),
    )


async def test_a_provider_failure_shows_the_provider_s_own_reason(env):
    """Seen live 2026-10-01: Hunyuan refused a second job while the first (which the user
    had stopped waiting for) was still running there. RunWare's message ends "Additional
    information below" and the SDK drops the part below, so the job card said nothing
    useful. The runner asks RunWare for the stored reply (free) and shows its
    ``responseContent``, with a plain-words hint for the limit case."""
    from runware import RunwareError

    paths, f = env
    seen: list = []
    reply = {
        "data": [],
        "errors": [
            {"code": "providerError", "message": PROVIDER_MSG, "responseContent": JOB_LIMIT}
        ],
    }
    fake = FakeRunware({"run": [RunwareError("providerError", PROVIDER_MSG)]})
    r = _detail_runner(env, fake, reply, seen)
    await r.start()
    job = generate.enqueue_media(
        f, paths, "3d", M.Model3DRequest(project_id=_pid(f), model=HUNYUAN, prompt="a cup")
    )
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "failed" and j.error_code == "provider"
        assert "JobNumExceed: The task limit has been reached" in j.error_message
        assert (
            "an earlier job" in j.error_message
            and "Additional information below" not in j.error_message
        )
        task_uuid = j.runware_task_uuid
    url, auth, body = seen[0]
    assert url == "https://api.runware.ai/v1" and auth == "Bearer key"
    assert b'"getResponse"' in body and task_uuid.encode() in body


async def test_a_detail_lookup_that_fails_leaves_runware_s_message_alone(env):
    from runware import RunwareError

    paths, f = env
    fake = FakeRunware({"run": [RunwareError("providerError", PROVIDER_MSG)]})
    r = _detail_runner(env, fake, {"unexpected": True}, [])
    await r.start()
    job = generate.enqueue_media(
        f, paths, "3d", M.Model3DRequest(project_id=_pid(f), model=HUNYUAN, prompt="a cup")
    )
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        assert "Additional information below" in s.get(models.Job, job.id).error_message


async def test_stopping_a_long_job_says_it_may_still_be_running(env):
    """Cancel only stops waiting. For anything but a quick image the provider keeps
    working (and billing), and some providers refuse a new job until it is done."""
    import asyncio

    paths, f = env
    started = asyncio.Event()

    class Slow(FakeRunware):
        async def run(self, params, options=None):
            started.set()
            await options.cancel_event.wait()
            from runware import RunwareError

            raise RunwareError("aborted", "Request aborted")

    r = _runner(env, Slow())
    await r.start()
    job = generate.enqueue_media(
        f, paths, "3d", M.Model3DRequest(project_id=_pid(f), model=HUNYUAN, prompt="a cup")
    )
    r.submit(job.id)
    await asyncio.wait_for(started.wait(), 5)
    r.cancel(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "cancelled"
        assert "may still finish" in j.error_message and "billed" in j.error_message


# ---- a 3D object comes from a description OR an image, never both -----------------------
def test_a_3d_request_takes_a_description_or_an_image_not_both(env):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="a description or an image, not both"):
        M.Model3DRequest(project_id=1, model=HUNYUAN, prompt="a cup", image_asset_id=3)
    assert M.Model3DRequest(project_id=1, model=HUNYUAN, prompt="a cup").image_asset_id is None
    assert M.Model3DRequest(project_id=1, model=HUNYUAN, image_asset_id=3).prompt == ""
