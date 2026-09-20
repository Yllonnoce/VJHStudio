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
    r = await client.get("/hx/model-options?air=google:4@2")
    assert 'name="steps"' not in r.text and "instruction" in r.text


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
