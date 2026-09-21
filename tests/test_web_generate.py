import re

from runware import RunwareError

FORM = {
    "project_id": "1",
    "model": "runware:101@1",
    "subject": "a red fox",
    "style": "oil painting",
    "width": "1024",
    "height": "1024",
    "number_results": "1",
    "output_format": "PNG",
    "no_text": "on",
    "use_default_negative": "on",
}


async def test_generate_page_renders(client):
    r = await client.get("/generate")
    assert r.status_code == 200 and 'id="generate-form"' in r.text and 'id="queue-panel"' in r.text
    assert (
        'name="model"' in r.text and "FLUX.1 [dev]" in r.text and 'id="generate-initial"' in r.text
    )


async def test_model_options_diffusion_vs_instruction(client):
    r = await client.get("/hx/model-options?air=runware:101@1")
    assert 'name="steps"' in r.text and 'value="28"' in r.text
    assert 'name="strength"' in r.text
    r = await client.get("/hx/model-options?air=google:4@2")
    assert 'name="steps"' not in r.text and "instruction" in r.text
    assert 'name="strength"' not in r.text


async def test_estimate(client):
    r = await client.get(
        "/hx/generate/estimate?air=runware:101@1&width=1024&height=1024&number_results=2"
    )
    assert "$0.0076" in r.text
    r = await client.get(
        "/hx/generate/estimate?air=runware:100@1&width=1024&height=1024&number_results=1"
    )
    assert "n/a" in r.text


async def test_submit_requires_key_and_validates(client):
    r = await client.post("/generate/image", data=FORM)
    assert r.status_code == 422 and "API key" in r.text
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    r = await client.post("/generate/image", data={**FORM, "width": "1000"})
    assert r.status_code == 422
    assert "multiple of 64" in r.text and r.headers.get("HX-Retarget") == "#model-params"


async def test_submit_runs_job_and_queue_shows_result(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png", "seed": 5, "cost": 0.004}]]
    r = await client.post("/generate/image", data=FORM)
    assert r.status_code == 200 and "jobs-changed" in r.headers.get("HX-Trigger", "")
    assert "a red fox" in r.text
    await app.state.runner.wait_idle()
    r = await client.get("/hx/jobs/active")
    assert "succeeded" in r.text and "/files/thumbs/" in r.text and "$0.0040" in r.text
    assert "job-finished" in r.headers.get("HX-Trigger", "")
    r2 = await client.get("/hx/jobs/active")
    assert "job-finished" not in r2.headers.get("HX-Trigger", "")  # seen now
    jobs = (await client.get("/api/jobs")).json()
    assert jobs[0]["status"] == "succeeded"
    assert jobs[0]["outputs"][0]["thumb_url"].startswith("/files/thumbs/")
    thumb = await client.get(jobs[0]["outputs"][0]["thumb_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"].startswith("image/")
    full = await client.get(jobs[0]["outputs"][0]["url"] + "?download=1")
    assert "attachment" in full.headers.get("content-disposition", "")


async def test_failed_job_card_and_retry(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [RunwareError("invalidApiKey", "bad"), [{"imageURL": "http://x/1.png"}]]
    await client.post("/generate/image", data=FORM)
    await app.state.runner.wait_idle()
    r = await client.get("/hx/jobs/active")
    assert "failed" in r.text and "rejected the API key" in r.text and "Retry" in r.text
    jid = (await client.get("/api/jobs")).json()[0]["id"]
    r = await client.post(f"/jobs/{jid}/retry")
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    assert (await client.get("/api/jobs")).json()[0]["status"] == "succeeded"


async def test_files_traversal_guarded(client):
    assert (await client.get("/files/outputs/default/..%2F..%2Fvjh.db")).status_code in (400, 404)
    assert (await client.get("/files/thumbs/../vjh.db")).status_code in (400, 404)


async def test_remix_prefills_initial(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png", "seed": 77}]]
    await client.post("/generate/image", data=FORM)
    await app.state.runner.wait_idle()
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/generate?remix={oid}")
    assert '"seed": 77' in r.text and '"subject": "a red fox"' in r.text


async def test_strength_is_parsed_and_remixed(client, fake, app):
    from sqlalchemy import select

    from vjhstudio import db, models

    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png", "seed": 77}]]
    r = await client.post("/generate/image", data={**FORM, "strength": "0.6"})
    assert r.status_code == 200
    await app.state.runner.wait_idle()

    with db.session_scope(app.state.boot.session_factory) as s:
        job = s.execute(select(models.Job)).scalars().first()
        assert job.request_json["strength"] == 0.6

    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/generate?remix={oid}")
    assert '"strength": 0.6' in r.text


def _has_attr_pair(html: str, name: str, value: str) -> bool:
    """True if some tag carries both a ``name`` and a ``value`` attribute with these
    exact values, regardless of which comes first."""
    forward = rf'name="{re.escape(name)}"[^>]*\bvalue="{re.escape(value)}"'
    backward = rf'value="{re.escape(value)}"[^>]*\bname="{re.escape(name)}"'
    return bool(re.search(forward, html) or re.search(backward, html))


async def test_remix_values_reach_the_full_page_inputs(client, fake, app):
    """Regression: the params partial used to be handed ``params.values`` via a Jinja
    ``{% with %}``, and since ``params`` is a plain dict, attribute-first lookup returned
    the dict's built-in ``.values`` method instead of the ``"values"`` key -- so on the
    full page (unlike the /hx/model-options partial, rendered directly) remix values
    never reached the width/height/seed inputs."""
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png", "seed": 5}]]
    r = await client.post("/generate/image", data={**FORM, "width": "512", "height": "512"})
    assert r.status_code == 200
    await app.state.runner.wait_idle()

    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/generate?remix={oid}")
    assert r.status_code == 200
    assert _has_attr_pair(r.text, "width", "512")
    assert _has_attr_pair(r.text, "height", "512")
    assert _has_attr_pair(r.text, "seed", "5")


async def test_ref_query_prefills_one_chip(client):
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (32, 32), (4, 5, 6)).save(b, "PNG")
    up = await client.post(
        "/assets/upload", files=[("files", ("ref.png", b.getvalue(), "image/png"))]
    )
    assert up.status_code == 200

    r = await client.get("/generate?ref=asset:1&role=reference")
    assert r.status_code == 200
    assert '"refs"' in r.text and '"id": 1' in r.text
    assert '"role": "reference"' in r.text and '"name": "ref.png"' in r.text
    assert '"thumb": "/files/asset-thumbs/' in r.text
    assert '"mode": "image"' in r.text

    # a frame role opens the video mode instead, and fills the frame field
    r = await client.get("/generate?ref=asset:1&role=first")
    assert '"mode": "video"' in r.text and '"role": "first"' in r.text
    assert '"first_frame_asset_id": 1' in r.text

    r = await client.get("/generate?ref=asset:1&role=seed")
    assert '"mode": "image"' in r.text and '"seed_image_asset_id": 1' in r.text


async def test_ref_query_404s_on_a_bad_reference(client):
    assert (await client.get("/generate?ref=asset:999")).status_code == 404
    assert (await client.get("/generate?ref=output:1")).status_code == 404
    assert (await client.get("/generate?ref=nonsense")).status_code == 404
    assert (await client.get("/generate")).status_code == 200


async def test_estimate_tolerates_blank_numbers(client):
    """A cleared Width box must re-render the estimate, not swap FastAPI's 422 JSON in."""
    r = await client.get("/hx/generate/estimate?air=runware:101@1&width=&height=&number_results=")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "≈" in r.text or "n/a" in r.text
    r = await client.get(
        "/hx/generate/estimate?air=runware:101@1&width=abc&height=-3&number_results=x"
    )
    assert r.status_code == 200 and 'id="estimate"' in r.text


async def test_retry_errors_render_html_not_json(client, fake, app):
    from vjhstudio import db, models

    assert (await client.post("/jobs/nope/retry")).status_code == 404
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [RunwareError("invalidApiKey", "bad")]
    await client.post("/generate/image", data=FORM)
    await app.state.runner.wait_idle()
    jid = (await client.get("/api/jobs")).json()[0]["id"]
    with db.session_scope(app.state.boot.session_factory) as s:
        job = s.get(models.Job, jid)
        job.request_json = {**dict(job.request_json or {}), "width": 7}
    r = await client.post(f"/jobs/{jid}/retry")
    assert r.status_code == 422 and "Retry failed" in r.text
    assert r.headers["content-type"].startswith("text/html") and 'id="queue-panel"' in r.text
