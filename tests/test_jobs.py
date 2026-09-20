import asyncio
import json

import httpx
import pytest
from PIL import Image
from runware import RunwareError

from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio import boot, config, db, models
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import costs, generate, jobs


def _png() -> bytes:
    import io

    b = io.BytesIO()
    Image.new("RGB", (64, 64), (1, 2, 3)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory, info


def _runner(env, fake, concurrency=2):
    paths, f, info = env
    t = httpx.MockTransport(lambda r: httpx.Response(200, content=_png()))
    return jobs.JobRunner(
        f,
        paths,
        client_factory=fake_factory(fake),
        api_key_getter=lambda: "key",
        transport_getter=lambda: "rest",
        concurrency=concurrency,
        download_transport=t,
    )


def _req(f):
    with db.session_scope(f) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
    return ImageRequest(
        project_id=pid, model="runware:101@1", form=PromptForm(subject="fox"), number_results=2
    )


async def test_job_succeeds_end_to_end(env):
    paths, f, _ = env
    fake = FakeRunware(
        {
            "run": [
                [
                    {"imageURL": "http://x/1.png", "seed": 1, "cost": 0.01},
                    {"imageURL": "http://x/2.png", "seed": 2, "cost": 0.01},
                ]
            ]
        }
    )
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "succeeded" and j.progress == 100 and abs(j.cost - 0.02) < 1e-9
        assert j.task_json["taskUUID"] == job.id and j.finished_at is not None
        outs = s.query(models.Output).filter_by(job_id=job.id).all()
        assert len(outs) == 2 and all((paths.outputs / o.rel_path).exists() for o in outs)
        assert all((paths.data / o.thumb_rel_path).exists() for o in outs)
        assert all(o.width == 64 and o.height == 64 and o.file_size > 0 for o in outs)
        side = json.loads((paths.outputs / outs[0].sidecar_rel_path).read_text())
        assert side["job_id"] == job.id and side["seed"] == 1 and side["cost"] == 0.01
        assert side["params"]["width"] == 1024 and side["task_sent"]["model"] == "runware:101@1"
        assert s.query(models.UsageEntry).count() == 2 and costs.today_spend(s) > 0
        assert (
            "observed"
            in s.query(models.CatalogModel).filter_by(air="runware:101@1").one().price_tiers_json
        )
    assert r.snapshot() == {} and r.active_ids() == []


async def test_job_failure_is_classified(env):
    paths, f, _ = env
    fake = FakeRunware({"run": [RunwareError("invalidApiKey", "bad")]})
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "failed" and j.error_code == "auth" and "Settings" in j.error_message


async def test_download_failure_is_reported(env):
    paths, f, _ = env
    fake = FakeRunware({"run": [[{"imageURL": "http://x/1.png"}]]})
    r = jobs.JobRunner(
        f,
        paths,
        client_factory=fake_factory(fake),
        api_key_getter=lambda: "key",
        transport_getter=lambda: "rest",
        concurrency=1,
        download_transport=httpx.MockTransport(lambda req: httpx.Response(500)),
    )
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "failed" and j.error_code == "download"


async def test_cancel_queued_and_running(env):
    paths, f, _ = env
    gate = asyncio.Event()

    class SlowFake(FakeRunware):
        async def run(self, params, options=None):
            await gate.wait()
            if options and options.cancel_event and options.cancel_event.is_set():
                raise RunwareError("aborted", "Request aborted")
            return [{"imageURL": "http://x/1.png"}]

    fake = SlowFake({})
    r = _runner(env, fake, concurrency=1)
    await r.start()
    j1 = generate.enqueue_image(f, paths, _req(f), default_negative="")
    j2 = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(j1.id)
    r.submit(j2.id)
    await asyncio.sleep(0.05)
    assert r.active_ids() == [j1.id]
    assert r.cancel(j2.id) is True  # still queued -> cancelled directly
    assert r.cancel(j1.id) is True  # running -> event set
    gate.set()
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        assert (
            s.get(models.Job, j1.id).status == "cancelled"
            and s.get(models.Job, j2.id).status == "cancelled"
        )


async def test_cancel_unknown_job_is_false(env):
    paths, f, _ = env
    r = _runner(env, FakeRunware({}))
    await r.start()
    assert r.cancel("nope") is False
    await r.stop()


async def test_requeued_jobs_run_on_start(env):
    paths, f, info = env
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    info2 = boot.boot(paths)
    assert job.id in info2.requeued_jobs
    fake = FakeRunware({"run": [[{"imageURL": "http://x/1.png"}]]})
    r = _runner((paths, f, info2), fake)
    await r.start(requeue=info2.requeued_jobs)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        assert s.get(models.Job, job.id).status == "succeeded"


async def test_progress_updates_reach_the_db(env):
    paths, f, _ = env
    fake = FakeRunware(
        {"run": [("progress", [10, 55], [{"imageURL": "http://x/1.png", "cost": 0.5}])]}
    )
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "succeeded" and j.progress == 100 and j.attempts == 1


def test_estimate_progress():
    assert jobs.estimate_progress(0, 10000) == 0
    assert 55 <= jobs.estimate_progress(10000, 10000) <= 65
    assert jobs.estimate_progress(10**9, 10000) == 90
