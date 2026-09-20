import asyncio
from contextlib import asynccontextmanager

FORM = {
    "project_id": "1",
    "model": "runware:101@1",
    "subject": "a red fox",
    "width": "1024",
    "height": "1024",
    "number_results": "1",
    "output_format": "PNG",
    "no_text": "on",
    "use_default_negative": "on",
}


async def test_active_panel_polls_only_when_active(client):
    r = await client.get("/hx/jobs/active")
    assert 'hx-trigger="jobs-changed from:body"' in r.text and "every 2s" not in r.text


async def test_cancel_unknown_404(client):
    assert (await client.post("/jobs/nope/cancel")).status_code == 404


async def test_jobs_badge_oob(client):
    r = await client.get("/hx/jobs/active")
    assert 'id="jobs-badge"' in r.text and 'hx-swap-oob="true"' in r.text


async def test_reported_progress_is_real_and_drops_the_eta(client, app):
    """A percentage from RunWare means the bar is measured: no "~Ns left" guess."""
    reported, gate = asyncio.Event(), asyncio.Event()

    class ProgressFake:
        async def run(self, params, options=None):
            options.on_progress({"progress": 42})
            reported.set()
            await gate.wait()
            return [{"imageURL": "http://x/1.png", "cost": 0.001}]

    @asynccontextmanager
    async def factory(api_key, transport="rest"):
        yield ProgressFake()

    app.state.runner.client_factory = factory
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    await client.post("/generate/image", data=FORM)
    await asyncio.wait_for(reported.wait(), 5)
    view = (await client.get("/api/jobs")).json()[0]
    assert view["status"] == "running" and view["progress"] >= 42
    assert view["estimated"] is False and view["eta_ms"] == 0
    card = await client.get(f"/hx/jobs/{view['id']}")
    assert card.status_code == 200 and "s left" not in card.text and "rendering" in card.text
    gate.set()
    await app.state.runner.wait_idle()


async def _finish_one_job(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png", "seed": 5, "cost": 0.004}]]
    await client.post("/generate/image", data=FORM)
    await app.state.runner.wait_idle()


async def test_badge_claims_finished_jobs_app_wide(client, fake, app):
    """The badge polls from every page; it - not the Generate-only panel - must notify."""
    await _finish_one_job(client, fake, app)
    r = await client.get("/hx/jobs/badge")
    assert r.status_code == 200 and "job-finished" in r.headers.get("HX-Trigger", "")
    assert "1 done" in r.text and 'href="/generate"' in r.text
    r2 = await client.get("/hx/jobs/badge")
    assert "job-finished" not in r2.headers.get("HX-Trigger", "") and "1 done" not in r2.text
    r3 = await client.get("/hx/jobs/active")
    assert "job-finished" not in r3.headers.get("HX-Trigger", "")


async def test_unseen_count_shows_on_any_page_before_the_poll(client, fake, app):
    await _finish_one_job(client, fake, app)
    r = await client.get("/gallery")
    assert "1 done" in r.text


async def test_racing_pollers_claim_a_job_only_once(client, fake, app):
    await _finish_one_job(client, fake, app)
    a, b = await asyncio.gather(
        client.get("/hx/jobs/badge"), client.get("/hx/jobs/active"), return_exceptions=False
    )
    triggers = [r.headers.get("HX-Trigger", "") for r in (a, b)]
    assert sum("job-finished" in t for t in triggers) == 1
