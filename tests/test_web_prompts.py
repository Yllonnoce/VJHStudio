import inspect
import json

from vjhstudio import db, models
from vjhstudio.web.routes import prompts as prompts_routes

PROMPT_FORM = {
    "project_id": "1",
    "mode": "image",
    "subject": "a red fox",
    "style": "oil painting",
    "final_prompt": "a red fox, oil painting",
    "title": "Fox portrait",
    "tags": "wildlife",
    "use_default_negative": "on",
    "no_text": "on",
}


def _prompt_id(headers) -> int:
    return json.loads(headers["HX-Trigger"])["prompt-saved"]["id"]


async def test_prompts_handlers_all_sync():
    """DB-only handlers stay sync `def` so Starlette threadpools them."""
    for fn in (
        prompts_routes.prompts_page,
        prompts_routes.hx_prompts,
        prompts_routes.save_prompt,
        prompts_routes.favourite_prompt,
        prompts_routes.duplicate_prompt,
        prompts_routes.set_prompt_tags,
        prompts_routes.delete_prompt,
    ):
        assert not inspect.iscoroutinefunction(fn), fn.__name__


async def test_prompts_page_renders_with_filters_and_empty_state(client):
    r = await client.get("/prompts")
    assert r.status_code == 200
    assert 'id="prompt-filters"' in r.text
    assert 'hx-get="/prompts"' in r.text
    assert 'id="prompt-empty"' in r.text


async def test_header_has_prompts_between_gallery_and_assets(client):
    r = await client.get("/")
    gallery_i = r.text.index('href="/gallery"')
    prompts_i = r.text.index('href="/prompts"')
    assets_i = r.text.index('href="/assets"')
    assert gallery_i < prompts_i < assets_i


async def test_post_prompts_creates_row_and_dedupes_on_repost(client):
    r = await client.post("/prompts", data=PROMPT_FORM)
    assert r.status_code == 200
    assert "Fox portrait" in r.text
    trigger = json.loads(r.headers["HX-Trigger"])["prompt-saved"]
    assert trigger["created"] is True
    pid = trigger["id"]

    r2 = await client.post("/prompts", data=PROMPT_FORM)
    assert r2.status_code == 200
    trigger2 = json.loads(r2.headers["HX-Trigger"])["prompt-saved"]
    assert trigger2["created"] is False
    assert trigger2["id"] == pid

    r3 = await client.get("/hx/prompts")
    assert r3.text.count('class="prompt-row"') == 1


async def test_missing_title_falls_back_to_composed_prompt(client):
    data = dict(PROMPT_FORM)
    data.pop("title")
    r = await client.post("/prompts", data=data)
    assert r.status_code == 200
    assert "a red fox, oil painting" in r.text


async def test_saved_polish_mode_renders_from_the_stored_source_key(client):
    """The hidden ``polish_json`` field the JS builds carries ``source`` (the mode), not
    ``mode`` -- the row template must fall back to it (I2), or every polished prompt
    shows a blank mode."""
    blob = {"source": "promptEnhance", "model": "", "versions": ["a fox, refined"], "cost": 0.0}
    data = {**PROMPT_FORM, "polish_json": json.dumps(blob)}
    r = await client.post("/prompts", data=data)
    assert r.status_code == 200
    assert "Polished (promptEnhance" in r.text


async def test_missing_project_is_422_with_inline_message(client):
    data = {**PROMPT_FORM, "project_id": "999"}
    r = await client.post("/prompts", data=data)
    assert r.status_code == 422
    assert "does not exist" in r.text


async def test_filters_q_kind_favourite_tag(client):
    fox = await client.post("/prompts", data=PROMPT_FORM)
    assert fox.status_code == 200
    whale = await client.post(
        "/prompts",
        data={
            **PROMPT_FORM,
            "subject": "a blue whale",
            "style": "",
            "final_prompt": "a blue whale",
            "title": "Whale surfacing",
            "mode": "video",
            "tags": "ocean",
        },
    )
    assert whale.status_code == 200
    whale_id = _prompt_id(whale.headers)
    fav = await client.post(f"/prompts/{whale_id}/favourite")
    assert fav.status_code == 200 and "★ Favourited" in fav.text

    r = await client.get("/hx/prompts?q=whale")
    assert "Whale surfacing" in r.text and "Fox portrait" not in r.text

    r = await client.get("/hx/prompts?kind=video")
    assert "Whale surfacing" in r.text and "Fox portrait" not in r.text

    r = await client.get("/hx/prompts?favourite=1")
    assert "Whale surfacing" in r.text and "Fox portrait" not in r.text

    r = await client.get("/hx/prompts?tag=wildlife")
    assert "Fox portrait" in r.text and "Whale surfacing" not in r.text


async def test_duplicate_makes_a_second_row_named_copy(client):
    r = await client.post("/prompts", data=PROMPT_FORM)
    pid = _prompt_id(r.headers)
    rd = await client.post(f"/prompts/{pid}/duplicate")
    assert rd.status_code == 200
    assert "Fox portrait (copy)" in rd.text

    rl = await client.get("/hx/prompts")
    assert rl.text.count('class="prompt-row"') == 2


async def test_duplicate_unknown_404(client):
    assert (await client.post("/prompts/999999/duplicate")).status_code == 404


async def test_favourite_toggles_twice(client):
    r = await client.post("/prompts", data=PROMPT_FORM)
    pid = _prompt_id(r.headers)
    r1 = await client.post(f"/prompts/{pid}/favourite")
    assert r1.status_code == 200 and "★ Favourited" in r1.text
    r2 = await client.post(f"/prompts/{pid}/favourite")
    assert r2.status_code == 200 and "☆ Favourite" in r2.text


async def test_favourite_unknown_404(client):
    assert (await client.post("/prompts/999999/favourite")).status_code == 404


async def test_tags_post_normalizes(client, app):
    r = await client.post("/prompts", data=PROMPT_FORM)
    pid = _prompt_id(r.headers)
    rt = await client.post(f"/prompts/{pid}/tags", data={"tags": "Fox, FOX "})
    assert rt.status_code == 200
    with db.session_scope(app.state.boot.session_factory) as s:
        p = s.get(models.Prompt, pid)
        assert p.tags == ",fox,"


async def test_tags_unknown_404(client):
    assert (await client.post("/prompts/999999/tags", data={"tags": "x"})).status_code == 404


async def test_delete_then_404(client):
    r = await client.post("/prompts", data=PROMPT_FORM)
    pid = _prompt_id(r.headers)
    rd = await client.delete(f"/prompts/{pid}")
    assert rd.status_code == 200 and rd.text == ""
    rd2 = await client.delete(f"/prompts/{pid}")
    assert rd2.status_code == 404


async def test_load_into_form_link_points_at_generate_with_prompt_id(client):
    r = await client.post("/prompts", data=PROMPT_FORM)
    pid = _prompt_id(r.headers)
    assert f'href="/generate?prompt={pid}"' in r.text


def _bulk_prompts(app, n: int) -> None:
    """Rows only, one distinct content_hash each so none of them dedupe together."""
    with db.session_scope(app.state.boot.session_factory) as s:
        for i in range(n):
            s.add(
                models.Prompt(
                    project_id=1,
                    title=f"bulk-{i}",
                    kind="image",
                    form_json={"subject": f"item {i}"},
                    composed_prompt=f"item {i}",
                    final_prompt=f"item {i}",
                    content_hash=f"{i:064d}",
                    tags=",",
                )
            )


async def test_page_two_of_31_prompts_has_no_list_wrapper(client, app):
    _bulk_prompts(app, 31)
    page1 = await client.get("/prompts")
    assert page1.status_code == 200
    assert page1.text.count('class="prompt-row"') == 30
    assert 'id="prompt-load-more"' in page1.text

    page2 = await client.get("/hx/prompts?page=2")
    assert page2.status_code == 200
    assert page2.text.count('class="prompt-row"') == 1
    assert 'id="prompt-list"' not in page2.text
    assert 'id="prompt-load-more"' not in page2.text
    assert "bulk-0" in page2.text and "bulk-30" not in page2.text
