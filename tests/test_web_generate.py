import inspect
import json
import re

from runware import RunwareError

from vjhstudio import db, models
from vjhstudio.web.routes import generate as generate_routes

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


async def test_generate_page_layout(client):
    r = await client.get("/generate/video")
    assert r.status_code == 200
    text = r.text
    assert '<header class="page-head">' in text
    assert "Describe what you want; VJHStudio writes the prompt." in text
    assert 'class="gen-layout"' in text
    rail_idx = text.index('<aside class="gen-rail')
    assert rail_idx != -1
    rail_section = text[rail_idx : text.index("</aside>", rail_idx)]
    assert "Generate" in rail_section and "gen-submit" in rail_section
    assert 'class="estimate"' in rail_section or "estimate" in rail_section
    assert text.index("gen-results") > text.index("gen-rail")
    assert text.count('id="queue-panel"') == 1
    assert 'id="mode-tab-image"' in text and 'id="mode-tab-video"' in text


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


async def test_generate_form_has_no_dead_polish_versions_attribute(client):
    """M6/promoted-T4: Task 5 never wired ``polishVersions`` into ``generateForm``, so
    ``data-polish-versions`` was dead -- the version select's value comes straight from
    the template's ``polish_versions`` context var, read live via ``hx-include``."""
    r = await client.get("/generate")
    assert "data-polish-versions" not in r.text
    assert 'id="polish-versions-select"' in r.text


async def test_polish_route_is_async_but_compose_stays_sync():
    assert inspect.iscoroutinefunction(generate_routes.hx_polish) is True
    assert inspect.iscoroutinefunction(generate_routes.hx_compose) is False


async def test_polish_requires_key_and_a_composed_prompt(client):
    r = await client.post("/hx/prompt/polish", data={"project_id": "1"})
    assert r.status_code == 422 and "API key" in r.text
    assert r.headers.get("HX-Retarget") == "#polish-results"

    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    r = await client.post("/hx/prompt/polish", data={"project_id": "1"})
    assert r.status_code == 422 and "Write a prompt first." in r.text
    assert r.headers.get("HX-Retarget") == "#polish-results"


async def test_polish_runware_error_shows_classified_message(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [RunwareError("quotaExceeded", "no balance")]
    r = await client.post(
        "/hx/prompt/polish",
        data={"project_id": "1", "subject": "a red fox", "polish_mode": "promptEnhance"},
    )
    assert r.status_code == 422 and "top up" in r.text.lower()
    assert r.headers.get("HX-Retarget") == "#polish-results"


async def test_polish_promptenhance_success_renders_cards_and_records_usage(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [
        [{"text": "a fox, refined", "cost": 0.0002}, {"text": "a fox, alt", "cost": 0.0002}]
    ]
    r = await client.post(
        "/hx/prompt/polish",
        data={
            "project_id": "1",
            "subject": "a red fox",
            "polish_mode": "promptEnhance",
            "polish_versions": "2",
        },
    )
    assert r.status_code == 200 and r.text.count("Use this") == 2
    assert 'data-text="a fox, refined"' in r.text and 'data-text="a fox, alt"' in r.text
    with db.session_scope(app.state.boot.session_factory) as s:
        entry = s.query(models.UsageEntry).one()
        assert entry.task_type == "promptEnhance" and entry.project_id == 1
        assert abs(entry.cost - 0.0004) < 1e-9


# ---- ?prompt= prefill and auto-history -----------------------------------
PROMPT_SAVE = {
    "project_id": "1",
    "mode": "image",
    "subject": "a red fox",
    "style": "oil painting",
    "final_prompt": "a red fox, oil painting",
    "title": "Fox portrait",
    "use_default_negative": "on",
    "no_text": "on",
}


async def _save_prompt(client, **extra) -> int:
    r = await client.post("/prompts", data={**PROMPT_SAVE, **extra})
    assert r.status_code == 200
    return json.loads(r.headers["HX-Trigger"])["prompt-saved"]["id"]


async def test_prompt_query_prefills_the_form_and_posts_the_id(client, app):
    pid = await _save_prompt(client)
    r = await client.get(f"/generate?prompt={pid}")
    assert r.status_code == 200
    assert f'"prompt_id": {pid}' in r.text  # the initial blob (Task 5's draft guard reads it)
    assert '"subject": "a red fox"' in r.text and '"style": "oil painting"' in r.text
    # the saved final carries the no-text suffix the submit path would have added
    assert '"final_prompt": "a red fox, oil painting Pure artwork only' in r.text
    # Subject is a text area now, so its prefill is the element's text, not @value
    assert ">a red fox</textarea>" in r.text
    assert ">a red fox, oil painting Pure artwork only" in r.text
    assert _has_attr_pair(r.text, "prompt_id", str(pid))
    with db.session_scope(app.state.boot.session_factory) as s:
        # opening a prompt is not using it: only a submit bumps use_count
        assert s.get(models.Prompt, pid).use_count == 0


async def test_prompt_query_404s_on_an_unknown_id(client):
    assert (await client.get("/generate?prompt=999999")).status_code == 404
    assert (await client.get("/generate?prompt=nonsense")).status_code == 404
    assert (await client.get("/generate")).status_code == 200


async def test_prompt_query_opens_the_video_tab(client):
    pid = await _save_prompt(client, mode="video", final_prompt="a fox running, slow pan")
    r = await client.get(f"/generate?prompt={pid}")
    assert r.status_code == 200 and '"mode": "video"' in r.text
    assert re.search(r'id="mode-tab-video"[^>]*\saria-selected="true"', r.text)
    assert 'name="duration"' in r.text  # the video parameter pane, not the image one


async def test_loaded_prompt_page_still_renders_the_ref_chips_block(client):
    """A saved prompt carries no assets, so ``refs`` is empty and nothing is re-derived."""
    pid = await _save_prompt(client)
    r = await client.get(f"/generate?prompt={pid}")
    assert '"refs": []' in r.text and 'id="gen-refs"' in r.text


async def test_prompt_id_and_polish_controls_are_outside_the_mode_fieldsets(client):
    """The inactive mode's fieldset is ``disabled``, which would stop its inputs posting."""
    html = (await client.get("/generate")).text
    first_pane = html.index('<fieldset class="gen-pane"')
    for name in ("prompt_id", "polish_json", "polish_mode", "polish_versions", "polish_model"):
        assert f'name="{name}"' in html, name
        assert html.index(f'name="{name}"') < first_pane, name
    assert 'id="polish-results"' in html and 'id="save-prompt"' in html


async def test_polish_model_select_is_hidden_until_the_setting_asks_for_it(client):
    html = (await client.get("/generate")).text
    field = html[html.index('id="polish-model-field"') : html.index('name="polish_model"')]
    assert "display:none" in field
    assert "(use promptEnhance)" in html and "Claude Sonnet 4.6" in html

    await client.post("/settings", data={"prompt.polish_mode": "textInference"})
    html = (await client.get("/generate")).text
    field = html[html.index('id="polish-model-field"') : html.index('name="polish_model"')]
    assert "display:none" not in field


async def test_submit_records_the_prompt_and_links_the_job(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png"}]]
    assert (await client.post("/generate/image", data=FORM)).status_code == 200
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.kind == "image" and prompt.use_count == 1
        assert prompt.composed_prompt == "a red fox, oil painting"
        assert "low quality" in prompt.negative_prompt  # the built negative, as /prompts does
        assert s.query(models.Job).one().prompt_id == prompt.id


async def test_submitting_the_same_text_twice_keeps_one_prompt_row(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png"}], [{"imageURL": "http://x/2.png"}]]
    assert (await client.post("/generate/image", data=FORM)).status_code == 200
    assert (await client.post("/generate/image", data=FORM)).status_code == 200
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.use_count == 2
        jobs = s.query(models.Job).all()
        assert len(jobs) == 2 and {j.prompt_id for j in jobs} == {prompt.id}


async def test_submit_with_a_prompt_id_reuses_that_row(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png"}]]
    pid = await _save_prompt(client, final_prompt="")  # same text the FORM submit builds
    r = await client.post("/generate/image", data={**FORM, "prompt_id": str(pid)})
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.id == pid and prompt.use_count == 1 and prompt.title == "Fox portrait"
        assert s.query(models.Job).one().prompt_id == pid


async def test_submit_stores_the_polish_blob_and_ignores_junk(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png"}], [{"imageURL": "http://x/2.png"}]]
    blob = {"source": "promptEnhance", "model": "m", "versions": ["a fox"], "chosen_index": 0}
    r = await client.post("/generate/image", data={**FORM, "polish_json": json.dumps(blob)})
    assert r.status_code == 200
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(models.Prompt).one().polish_json == blob

    r = await client.post(
        "/generate/image", data={**FORM, "subject": "a blue hare", "polish_json": "not json"}
    )
    assert r.status_code == 200  # junk is dropped, never a 500
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        row = (
            s.query(models.Prompt).filter(models.Prompt.composed_prompt.like("a blue hare%")).one()
        )
        assert row.polish_json is None


async def test_submit_stores_the_polish_blob_on_a_reused_row(client, fake, app):
    """Reusing the row ``prompt_id`` names must still record a polish blob the submit
    carries -- ``prompts.upsert`` treats an existing row the same way."""
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png"}]]
    pid = await _save_prompt(client, final_prompt="")
    blob = {"source": "textInference", "model": "openai:gpt@5.5", "versions": ["a fox"]}
    r = await client.post(
        "/generate/image",
        data={**FORM, "prompt_id": str(pid), "polish_json": json.dumps(blob)},
    )
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.id == pid and prompt.polish_json == blob


async def test_a_saved_prompt_dedupes_against_the_generated_one(client, fake, app):
    """A prompt saved from the dialog and the same prompt submitted must be one row --
    both sides build (composed, final, negative) through ``prompts.saved_texts``. No
    ``prompt_id`` is posted here on purpose: the match has to come from the hash."""
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png"}]]
    pid = await _save_prompt(client)  # a typed final prompt, no_text on
    r = await client.post(
        "/generate/image", data={**FORM, "final_prompt": "a red fox, oil painting"}
    )
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    with db.session_scope(app.state.boot.session_factory) as s:
        prompt = s.query(models.Prompt).one()
        assert prompt.id == pid and prompt.use_count == 1  # the save itself never counted
        assert s.query(models.Job).one().prompt_id == pid


async def test_save_dialog_sits_outside_the_generate_form(client):
    """Its title/tags inputs would otherwise post with every Generate submit, and the
    typed prompt title would quietly become the job title."""
    html = (await client.get("/generate")).text
    start = html.index('<form id="generate-form"')
    end = html.index("</form>", start)
    assert html.index('<dialog id="save-prompt"') > end
    assert 'hx-include="#generate-form, #save-prompt"' in html
    form_html = html[start:end]
    assert 'name="title"' not in form_html and 'name="tags"' not in form_html
    assert 'id="save-prompt-open"' in form_html  # the opener stays in the prompt column
