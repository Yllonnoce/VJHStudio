import io
import json

import httpx
import pytest
from PIL import Image

from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio import boot, config, db, models
from vjhstudio.schemas.image import PromptForm
from vjhstudio.schemas.video import VideoRequest
from vjhstudio.services import assets, catalog, generate, jobs

VIDEO_AIR = "google:3@2"
IMAGE_AIR = "runware:101@1"
LTX_AIR = "lightricks:ltx@2.3"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _png(colour: tuple[int, int, int] = (1, 2, 3)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory, info


def _runner(env, fake, body=MP4, concurrency=1):
    paths, f, _ = env
    t = httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    return jobs.JobRunner(
        f,
        paths,
        client_factory=fake_factory(fake),
        api_key_getter=lambda: "key",
        transport_getter=lambda: "rest",
        concurrency=concurrency,
        download_transport=t,
    )


def _pid(f) -> int:
    with db.session_scope(f) as s:
        return s.query(models.Project).filter_by(slug="default").one().id


def _req(f, **kw) -> VideoRequest:
    base = dict(project_id=_pid(f), model=VIDEO_AIR, form=PromptForm(subject="a fox running"))
    base.update(kw)
    return VideoRequest(**base)


# ---- enqueue -------------------------------------------------------------
def test_enqueue_video_creates_a_video_job(env):
    paths, f, _ = env
    job = generate.enqueue_video(f, paths, _req(f))
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.kind == "video" and j.status == "queued" and j.model_air == VIDEO_AIR
        assert j.title == "a fox running"
        assert j.request_json["duration"] == 5 and j.request_json["resolution"] == "720p"
        assert "negative" not in j.request_json
        assert j.expected_ms == 89836  # Veo's curated typical latency, ~90 s


def test_enqueue_video_rejects_an_image_model(env):
    paths, f, _ = env
    with pytest.raises(ValueError, match="is not a video model"):
        generate.enqueue_video(f, paths, _req(f, model=IMAGE_AIR))
    with pytest.raises(ValueError, match="is not a video model"):
        generate.enqueue_video(f, paths, _req(f, model="nope:0@0"))


def test_enqueue_video_rejects_an_unknown_project(env):
    paths, f, _ = env
    with pytest.raises(ValueError, match="does not exist"):
        generate.enqueue_video(f, paths, _req(f, project_id=9999))


def test_enqueue_image_still_rejects_a_video_model(env):
    from vjhstudio.schemas.image import ImageRequest

    paths, f, _ = env
    req = ImageRequest(project_id=_pid(f), model=VIDEO_AIR, form=PromptForm(subject="x"))
    with pytest.raises(ValueError, match="is not an image model"):
        generate.enqueue_image(f, paths, req, default_negative="")


# ---- catalog view --------------------------------------------------------
def test_catalog_view_carries_what_the_builder_needs(env):
    _, f, _ = env
    with db.session_scope(f) as s:
        view = catalog.view(catalog.get_by_air(s, VIDEO_AIR))
    assert view["air"] == VIDEO_AIR and view["kind"] == "video" and view["family"] == "video"
    assert view["tiers"]["video"]["durations"] == [4, 6, 8]
    assert view["provider_settings_schema"][0]["key"] == "generateAudio"
    assert "io:image-to-video" in view["capabilities"]


# ---- end to end ----------------------------------------------------------
async def test_video_job_succeeds_end_to_end(env):
    paths, f, _ = env
    fake = FakeRunware({"run": [[{"videoURL": "http://x/v.mp4", "cost": 0.8}]]})
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_video(f, paths, _req(f, provider_settings={"generateAudio": True}))
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "succeeded", j.error_message
        assert j.progress == 100 and abs(j.cost - 0.8) < 1e-9
        assert j.task_json["taskType"] == "videoInference"
        assert j.task_json["providerSettings"] == {"google": {"generateAudio": True}}
        out = s.query(models.Output).filter_by(job_id=job.id).one()
        assert out.kind == "video" and out.filename.endswith(".mp4")
        assert (paths.outputs / out.rel_path).exists() and out.file_size == len(MP4)
        # 5 s was asked for, Veo accepts 4/6/8: the row records the 4 s that was sent
        assert j.task_json["duration"] == 4
        assert out.duration_s == 4 and out.width == 1280 and out.height == 720
        assert out.thumb_rel_path is None
        assert out.prompt_text == "a fox running" and out.negative_prompt == ""
        side = json.loads((paths.outputs / out.sidecar_rel_path).read_text())
        assert side["kind"] == "video" and side["duration"] == 4
        assert side["params"]["resolution"] == "720p" and side["params"]["width"] == 1280
        assert side["task_sent"]["taskType"] == "videoInference"
        usage = s.query(models.UsageEntry).one()
        assert usage.task_type == "videoInference" and abs(usage.cost - 0.8) < 1e-9


async def test_video_job_sends_frame_images_from_assets(env):
    paths, f, _ = env
    with db.session_scope(f) as s:
        first, _ = assets.store_upload(
            s, paths, original_name="first.png", content=_png(), mime="image/png"
        )
        last, _ = assets.store_upload(
            s, paths, original_name="last.png", content=_png((9, 9, 9)), mime="image/png"
        )
        first_id, last_id = first.id, last.id
    fake = FakeRunware(
        {
            "media_storage": [
                [{"mediaUUID": "uuid-first", "mediaURL": "http://x/1"}],
                [{"mediaUUID": "uuid-last", "mediaURL": "http://x/2"}],
            ],
            "run": [[{"videoURL": "http://x/v.mp4", "cost": 0.4}]],
        }
    )
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_video(
        f, paths, _req(f, first_frame_asset_id=first_id, last_frame_asset_id=last_id)
    )
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        assert s.get(models.Job, job.id).status == "succeeded"
    sent = [p for name, p in fake.calls if name == "run"][0]
    assert sent["inputs"]["frameImages"] == [
        {"image": "uuid-first", "frame": "first"},
        {"image": "uuid-last", "frame": "last"},
    ]


async def test_video_output_records_the_dimensions_the_task_carried(env):
    """LTX-2.3's 720p is 1280x704, not the generic 1280x720: the Output row and the
    sidecar must describe the clip that was generated, not the preset it came from."""
    paths, f, _ = env
    fake = FakeRunware({"run": [[{"videoURL": "http://x/v.mp4", "cost": 0.12}]]})
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_video(f, paths, _req(f, model=LTX_AIR, duration=3, resolution="720p"))
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    sent = [p for name, p in fake.calls if name == "run"][0]
    assert (sent["width"], sent["height"]) == (1280, 704)
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "succeeded", j.error_message
        out = s.query(models.Output).filter_by(job_id=job.id).one()
        assert out.width == 1280 and out.height == 704 and out.duration_s == 3
        side = json.loads((paths.outputs / out.sidecar_rel_path).read_text())
        assert side["params"]["width"] == 1280 and side["params"]["height"] == 704
        assert side["params"]["resolution"] == "720p" and side["duration"] == 3
        assert side["task_sent"]["width"] == 1280 and side["task_sent"]["height"] == 704


async def test_video_job_webm_uses_the_requested_extension(env):
    paths, f, _ = env
    fake = FakeRunware({"run": [[{"videoURL": "http://x/v.webm", "cost": 0.1}]]})
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_video(f, paths, _req(f, output_format="WEBM", resolution="1080p"))
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        out = s.query(models.Output).filter_by(job_id=job.id).one()
        assert out.filename.endswith(".webm")
        assert out.width == 1920 and out.height == 1080


async def test_image_jobs_are_unaffected_by_the_kind_dispatch(env):
    """The image path keeps its thumbnail, dimensions and imageInference usage row."""
    from vjhstudio.schemas.image import ImageRequest

    paths, f, _ = env
    fake = FakeRunware({"run": [[{"imageURL": "http://x/1.png", "seed": 3, "cost": 0.01}]]})
    r = _runner(env, fake, body=_png())
    await r.start()
    req = ImageRequest(project_id=_pid(f), model=IMAGE_AIR, form=PromptForm(subject="fox"))
    job = generate.enqueue_image(f, paths, req, default_negative="ugly")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "succeeded", j.error_message
        out = s.query(models.Output).filter_by(job_id=job.id).one()
        assert out.kind == "image" and out.width == 64 and out.duration_s is None
        assert out.thumb_rel_path and (paths.data / out.thumb_rel_path).exists()
        assert s.query(models.UsageEntry).one().task_type == "imageInference"
