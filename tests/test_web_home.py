"""The home dashboard: create cards, recent outputs, live queue, quick stats."""

import asyncio
from contextlib import asynccontextmanager

from vjhstudio import db
from vjhstudio.models import Job

FORM = {
    "project_id": "1",
    "model": "runware:101@1",
    "subject": "a red fox",
    "width": "1024",
    "height": "1024",
    "number_results": "1",
    "output_format": "PNG",
}


async def _make(client, fake, app, n=1):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [
        [{"imageURL": f"http://x/{i}.png", "seed": i, "cost": 0.001} for i in range(n)]
    ]
    await client.post("/generate/image", data={**FORM, "number_results": str(n)})
    await app.state.runner.wait_idle()


async def test_home_empty_state(client):
    r = await client.get("/")
    assert r.status_code == 200
    text = r.text
    assert "Create an image" in text and "Create a video" in text
    assert 'href="/generate/image"' in text and 'href="/generate/video"' in text
    assert "Nothing generated yet. Create an image to get started." in text
    assert "Spent today" in text and "Outputs" in text
    assert "arrive in the next phases" not in text


async def test_home_recent_strip_newest_first_and_projects(client, fake, app):
    await _make(client, fake, app, n=3)
    outs = (await client.get("/api/jobs")).json()[0]["outputs"]
    oids = sorted(o["id"] for o in outs)

    r = await client.get("/")
    assert r.status_code == 200
    text = r.text
    assert text.count("gallery-card") >= 3
    assert 'href="/gallery"' in text and "See all" in text
    # newest (highest id) first
    assert text.index(f"open={oids[-1]}") < text.index(f"open={oids[0]}")
    assert "<td>Default</td><td>3</td>" in text


async def test_home_shows_active_job_and_queue_panel(client, app):
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

    r = await client.get("/")
    assert r.status_code == 200
    assert "In progress" in r.text and 'id="queue-panel"' in r.text

    gate.set()
    await app.state.runner.wait_idle()


async def test_home_no_active_job_hides_in_progress(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "In progress" not in r.text


async def test_home_in_progress_follows_the_database_not_the_runner(client, app):
    """The section and the panel it wraps must agree. The panel renders `jobs_active`,
    read from the database; gating the section on the runner's in-memory ids instead
    hid a queued job the panel would happily have shown (and, after a restart, every
    job in the queue)."""
    with db.session_scope(app.state.boot.session_factory) as s:
        s.add(
            Job(
                id="queued-but-unclaimed",
                project_id=1,
                kind="image",
                status="queued",
                model_air="runware:101@1",
                request_json={},
            )
        )
    assert app.state.runner.active_ids() == []  # nothing in flight in memory
    r = await client.get("/")
    assert r.status_code == 200
    assert "In progress" in r.text and 'id="queue-panel"' in r.text


async def test_gallery_open_id_addresses_the_lightbox_opener(client, fake, app):
    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/gallery?open={oid}")
    assert r.status_code == 200
    assert f"output-{oid}" in r.text
    # htmx and app.js are `defer` scripts, so they register their own DOMContentLoaded
    # listeners *after* this inline one. Clicking straight from that listener fired
    # before `hx-get` was wired and opened an empty lightbox, so the click has to be
    # deferred a turn (setTimeout) or hung off htmx's own `htmx:load`.
    opener = r.text[r.text.rindex("<script>") : r.text.rindex("</script>")]
    assert "DOMContentLoaded" in opener or "htmx:load" in opener
    assert "setTimeout" in opener or "htmx:load" in opener
