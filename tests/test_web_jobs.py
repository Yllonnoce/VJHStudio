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
    assert "1 done" in r.text and 'href="/queue"' in r.text
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


async def test_dropped_params_render_field_and_action(client, fake, app):
    """apply_fallback records {"field", "action"} - the card used to print the raw dict."""
    from vjhstudio import db, models

    await _finish_one_job(client, fake, app)
    jid = (await client.get("/api/jobs")).json()[0]["id"]
    with db.session_scope(app.state.boot.session_factory) as s:
        s.get(models.Job, jid).dropped_params_json = [{"field": "steps", "action": "dropped"}]
    r = await client.get(f"/hx/jobs/{jid}")
    assert r.status_code == 200 and "Dropped params" in r.text
    assert "steps" in r.text and "dropped" in r.text and "'field'" not in r.text


async def test_job_card_reports_a_size_correction_as_an_adjustment(client, fake, app):
    from vjhstudio import db, models

    await _finish_one_job(client, fake, app)
    jid = (await client.get("/api/jobs")).json()[0]["id"]
    with db.session_scope(app.state.boot.session_factory) as s:
        s.get(models.Job, jid).dropped_params_json = [
            {
                "field": "width/height",
                "action": "corrected",
                "from": [1280, 720],
                "to": [3840, 2160],
                "dims": {},
            },
            {"field": "seed", "action": "dropped"},
        ]
    r = await client.get(f"/hx/jobs/{jid}")
    assert r.status_code == 200
    assert "Adjusted size 1280×720 → 3840×2160" in r.text
    assert "Dropped params: seed (dropped)" in r.text and "width/height (corrected)" not in r.text


async def test_queue_page_shows_the_panel_and_a_history_table(client, fake, app):
    r = await client.get("/queue")
    assert r.status_code == 200
    assert 'id="queue-panel"' in r.text and "<h1>Queue</h1>" in r.text
    assert "No finished jobs yet." in r.text
    await _finish_one_job(client, fake, app)
    r = await client.get("/queue")
    assert "History" in r.text and 'id="history-' in r.text
    assert "succeeded" in r.text and "/gallery?open=" in r.text
    # the header chip points at this page and marks it current
    assert 'href="/queue"' in r.text
    nav = r.text.split("</nav>")[0]
    assert nav.count('aria-current="page"') == 1


async def test_a_badge_poll_marks_nothing_current(client):
    """The poll cannot know which page it was fired from -- htmx froze its URL when it
    processed the chip -- so it stops guessing: the response is rendered for
    /hx/jobs/badge, which is not under /queue, and app.js marks the chip instead.
    A leftover `?at=` from an older cached page is ignored rather than trusted."""
    r = await client.get("/hx/jobs/badge")
    assert r.status_code == 200 and 'aria-current="page"' not in r.text
    assert 'aria-current="page"' not in (await client.get("/hx/jobs/badge?at=/queue")).text


async def test_clear_finished_hides_done_jobs_until_a_newer_one_finishes(client, fake, app):
    await _finish_one_job(client, fake, app)
    r = await client.get("/hx/jobs/active")
    assert 'id="job-' in r.text and "Clear finished" in r.text
    r = await client.post("/jobs/seen")
    assert r.status_code == 200 and 'id="job-' not in r.text
    assert "Nothing queued yet." in r.text
    # the Queue page's history still lists it
    r = await client.get("/queue")
    assert 'id="history-' in r.text
    # a job that finishes afterwards shows up again
    await _finish_one_job(client, fake, app)
    r = await client.get("/hx/jobs/active")
    assert r.text.count('id="job-') == 1


async def _agent_job(app, status=None):
    """One job as an agent would have queued it, without going through the runner."""
    from vjhstudio import db
    from vjhstudio.models import Job, utcnow
    from vjhstudio.schemas.image import ImageRequest, PromptForm
    from vjhstudio.services import generate

    req = ImageRequest(project_id=1, model="runware:101@1", form=PromptForm(subject="a red fox"))
    job = generate.enqueue_image(
        app.state.boot.session_factory,
        app.state.paths,
        req,
        default_negative="",
        source="mcp",
        estimate_usd=0.01,
    )
    if status is not None:
        with db.session_scope(app.state.boot.session_factory) as s:
            row = s.get(Job, job.id)
            row.status, row.finished_at = status, utcnow()
    return job


async def test_queue_panel_shows_a_via_agent_chip(client, app):
    await _agent_job(app)
    r = await client.get("/queue")
    assert "via agent" in r.text
    panel = await client.get("/hx/jobs/active")
    assert "via agent" in panel.text


async def test_queue_history_shows_a_via_agent_chip(client, app):
    from vjhstudio.models import JobStatus

    await _agent_job(app, status=JobStatus.succeeded.value)
    r = await client.get("/queue")
    history = r.text.split('class="queue-history"', 1)[1]
    assert "via agent" in history


async def test_browser_jobs_have_no_chip(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    await client.post("/generate/image", data=FORM)
    r = await client.get("/queue")
    assert "via agent" not in r.text
    await app.state.runner.wait_idle()
