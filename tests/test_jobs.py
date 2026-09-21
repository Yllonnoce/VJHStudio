import asyncio
import json

import httpx
import pytest
from PIL import Image
from runware import RunwareError

import vjhstudio
from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio import boot, config, db, models
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import assets, costs, generate, jobs
from vjhstudio.services import settings as settings_svc


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
        assert side["app_version"] == vjhstudio.__version__ and side["project"] == "default"
        assert "negative_prompt" in side and "negative" not in side
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


async def test_job_with_seed_image_uploads_and_sends_media_uuid(env):
    paths, f, _ = env
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="seed.png", content=_png(), mime="image/png"
        )
        aid = asset.id
        pid = s.query(models.Project).filter_by(slug="default").one().id
    fake = FakeRunware(
        {
            "media_storage": [[{"mediaUUID": "uuid-seed", "mediaURL": "http://x/seed"}]],
            "run": [[{"imageURL": "http://x/1.png", "cost": 0.01}]],
        }
    )
    r = _runner(env, fake, concurrency=1)
    await r.start()
    req = ImageRequest(
        project_id=pid,
        model="runware:101@1",
        form=PromptForm(subject="fox"),
        seed_image_asset_id=aid,
    )
    job = generate.enqueue_image(f, paths, req, default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "succeeded", j.error_message
    run_params = [p for name, p in fake.calls if name == "run"]
    assert run_params[0]["inputs"]["seedImage"] == "uuid-seed"
    with db.session_scope(f) as s:
        a = assets.get(s, aid)
        assert a.media_uuid == "uuid-seed"


async def test_job_upload_failure_fails_with_upload_code(env):
    paths, f, _ = env
    with db.session_scope(f) as s:
        asset, _ = assets.store_upload(
            s, paths, original_name="bad-ref.png", content=_png(), mime="image/png"
        )
        aid = asset.id
        pid = s.query(models.Project).filter_by(slug="default").one().id
    fake = FakeRunware({"media_storage": [RunwareError("invalidApiKey", "bad key")]})
    r = _runner(env, fake, concurrency=1)
    await r.start()
    req = ImageRequest(
        project_id=pid,
        model="runware:101@1",
        form=PromptForm(subject="fox"),
        seed_image_asset_id=aid,
    )
    job = generate.enqueue_image(f, paths, req, default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "failed" and j.error_code == "upload"
        assert "bad-ref.png" in j.error_message
        assert "Could not upload" in j.error_message


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


async def test_unparseable_request_row_fails_cleanly(env):
    """A row whose request_json no longer validates must fail, not hang in `running`."""
    paths, f, _ = env
    r = _runner(env, FakeRunware({}))
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    with db.session_scope(f) as s:
        s.get(models.Job, job.id).request_json = {"nonsense": True}
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "failed" and j.error_code == "unknown"


class _GatedFake(FakeRunware):
    """Blocks in run() until its gate opens, then honours the cancel event like the SDK does."""

    def __init__(self, gate, script=None):
        super().__init__(script or {})
        self.gate = gate

    async def run(self, params, options=None):
        await self.gate.wait()
        if options and options.cancel_event and options.cancel_event.is_set():
            raise RunwareError("aborted", "Request aborted")
        return [{"imageURL": "http://x/1.png"}]


async def test_cancel_from_a_worker_thread(env):
    """Sync routes run in a threadpool: cancel() must touch the Event on the runner's loop."""
    paths, f, _ = env
    gate = asyncio.Event()
    r = _runner(env, _GatedFake(gate), concurrency=1)
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await asyncio.sleep(0.05)
    assert r.active_ids() == [job.id]
    ev = r._events[job.id]
    hops = []
    real_hop = r._loop.call_soon_threadsafe
    r._loop.call_soon_threadsafe = lambda fn, *a: (hops.append(fn), real_hop(fn, *a))[1]
    try:
        assert await asyncio.to_thread(r.cancel, job.id) is True
    finally:
        r._loop.call_soon_threadsafe = real_hop
    # to_thread hops back on its own, so look for the Event itself among the callbacks
    assert ev.set in hops, "cancel() must set the Event on the loop, not from the worker thread"
    gate.set()
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        assert s.get(models.Job, job.id).status == "cancelled"


async def test_cancel_racing_event_registration_is_not_lost(env):
    """cancel() lands between `running` and the cancel event existing: the row stays cancelled
    even though the provider went on to return images."""
    paths, f, _ = env
    fake = FakeRunware({"run": [[{"imageURL": "http://x/1.png", "cost": 0.01}]]})
    r = _runner(env, fake, concurrency=1)
    real_begin = r._begin

    def begin(job_id):
        plan = real_begin(job_id)  # the row is `running`, no event registered yet
        assert r.cancel(job_id) is True
        r._cancelled.discard(job_id)  # worst case: even the in-memory flag is lost
        return plan

    r._begin = begin
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        assert s.get(models.Job, job.id).status == "cancelled"


async def test_outputs_dir_change_mid_job_does_not_break_persist(env, tmp_path):
    """The plan's root is used at persist time, not the setting as it stands by then."""
    paths, f, _ = env
    fake = FakeRunware({"run": [[{"imageURL": "http://x/1.png", "cost": 0.01}]]})

    def handler(request):
        with db.session_scope(f) as s:  # the setting moves while the job is downloading
            settings_svc.set_many(s, {"paths.outputs_dir": str(tmp_path / "moved")})
        return httpx.Response(200, content=_png())

    r = jobs.JobRunner(
        f,
        paths,
        client_factory=fake_factory(fake),
        api_key_getter=lambda: "key",
        transport_getter=lambda: "rest",
        concurrency=1,
        download_transport=httpx.MockTransport(handler),
    )
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "succeeded", j.error_message
        out = s.query(models.Output).filter_by(job_id=job.id).one()
        assert out.rel_path.startswith("default/") and (paths.outputs / out.rel_path).exists()


def test_estimate_progress():
    assert jobs.estimate_progress(0, 10000) == 0
    assert 55 <= jobs.estimate_progress(10000, 10000) <= 65
    assert jobs.estimate_progress(10**9, 10000) == 90


async def test_stage_sequence_includes_rendering(env):
    """Spec: queued -> submitting -> rendering -> downloading -> done."""
    paths, f, _ = env
    holder: dict = {}
    seen: list[str] = []

    class Recording(FakeRunware):
        async def run(self, params, options=None):
            jid = holder["job_id"]
            with db.session_scope(f) as s:
                seen.append(s.get(models.Job, jid).status_text)
            seen.append(holder["runner"].snapshot()[jid]["stage"])
            return await FakeRunware.run(self, params, options)

    fake = Recording({"run": [[{"imageURL": "http://x/1.png", "cost": 0.01}]]})
    r = _runner(env, fake)
    holder["runner"] = r
    stages: list[str] = []
    original = r._stage

    def spy(job_id, stage, progress=None):
        stages.append(stage)
        original(job_id, stage, progress)

    r._stage = spy
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    holder["job_id"] = job.id
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    assert seen == ["rendering", "rendering"]  # the row and the snapshot agree, mid-render
    assert stages[0] == "rendering" and "downloading" in stages and "saving" in stages


async def test_observed_latency_excludes_retry_backoff(env, monkeypatch):
    """I-6: one rate-limited job used to shift the model's average by seconds."""
    paths, f, _ = env
    slept: list[float] = []
    real_policy = jobs.policy.run_with_policy

    async def slow_sleep(seconds):
        slept.append(seconds)
        await asyncio.sleep(0.25)  # stands in for the real 2s rateLimit backoff

    async def with_stub_sleep(*args, **kwargs):
        kwargs["sleep"] = slow_sleep
        return await real_policy(*args, **kwargs)

    monkeypatch.setattr(jobs.policy, "run_with_policy", with_stub_sleep)
    fake = FakeRunware(
        {
            "run": [
                RunwareError("rateLimitExceeded", "slow down"),
                [{"imageURL": "http://x/1.png", "cost": 0.01}],
            ]
        }
    )
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    assert slept == [2]  # the backoff really happened, and really took wall-clock time
    with db.session_scope(f) as s:
        assert s.get(models.Job, job.id).status == "succeeded"
        observed = s.query(models.CatalogModel).filter_by(air="runware:101@1").one()
        avg_ms = observed.price_tiers_json["observed"]["avg_ms"]
    assert avg_ms < 100  # the 250ms backoff is not in there, let alone a real 2s one
