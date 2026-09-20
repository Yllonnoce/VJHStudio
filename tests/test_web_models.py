import json

from runware import RunwareError


async def test_models_page_lists_three_kinds_sorted(client):
    r = await client.get("/models")
    assert (
        r.status_code == 200
        and 'id="models-image"' in r.text
        and 'id="models-video"' in r.text
        and 'id="models-text"' in r.text
    )
    body = r.text
    assert (
        body.index("GPT-5.5") < body.index("Claude Sonnet 4.6") < body.index("Gemini 3.5 Flash")
    )  # dearest first
    assert "Veo 3.1 — $0.200/s ($1.00/5 s)" in body


async def test_hx_models_partial_hides_hidden_by_default(client):
    r = await client.get("/hx/models?kind=image")
    assert r.status_code == 200 and "FLUX.1 [dev]" in r.text


async def test_api_models_json(client):
    r = await client.get("/api/models?kind=video")
    rows = r.json()
    assert (
        rows[0]["air"] == "google:3@2"
        and rows[0]["family"] == "video"
        and rows[0]["label"].startswith("Veo 3.1")
    )
    assert (
        rows[-1]["price_primary"] is None or rows[-1]["price_primary"] <= rows[0]["price_primary"]
    )


async def test_favourite_and_hide_toggle(client):
    rows = (await client.get("/api/models?kind=image")).json()
    flux = next(r for r in rows if r["air"] == "runware:101@1")
    r = await client.post(f"/models/{flux['id']}/favourite")
    assert r.status_code == 200 and "★" in r.text
    r = await client.post(f"/models/{flux['id']}/hide")
    assert r.status_code == 200
    assert all(
        x["air"] != "runware:101@1" for x in (await client.get("/api/models?kind=image")).json()
    )
    assert any(
        x["air"] == "runware:101@1"
        for x in (await client.get("/api/models?kind=image&hidden=1")).json()
    )
    assert (await client.post("/models/999999/hide")).status_code == 404


async def test_search_requires_key(client):
    r = await client.post("/hx/models/search", data={"q": "x", "kind": "image"})
    assert r.status_code == 422 and "API key" in r.text


async def test_search_and_add(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["model_search"] = [
        [
            {
                "results": [
                    {
                        "air": "civitai:1@2",
                        "name": "Dreamy",
                        "category": "checkpoint",
                        "architecture": "sdxl",
                        "provider": "civitai",
                        "capabilities": [],
                    }
                ]
            }
        ]
    ]
    r = await client.post("/hx/models/search", data={"q": "dream", "kind": "image"})
    assert r.status_code == 200 and "Dreamy" in r.text and 'name="record"' in r.text
    record = json.dumps(
        {
            "air": "civitai:1@2",
            "name": "Dreamy",
            "architecture": "sdxl",
            "provider": "civitai",
            "capabilities": [],
        }
    )
    r = await client.post("/models/add", data={"kind": "image", "record": record})
    assert r.status_code == 200 and "Dreamy" in r.text and "Added" in r.text
    assert any(
        x["air"] == "civitai:1@2" for x in (await client.get("/api/models?kind=image")).json()
    )


async def test_search_error_422(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["model_search"] = [RunwareError("invalidApiKey", "bad")]
    r = await client.post("/hx/models/search", data={"q": "x", "kind": "image"})
    assert r.status_code == 422 and "rejected the API key" in r.text


async def test_refresh_prices_uses_injected_api(app, client, monkeypatch):
    from vjhstudio.models import utcnow
    from vjhstudio.services import catalog

    async def fake_refresh(session_factory, api, concurrency=5):
        return catalog.RefreshResult(models=3, priced=2, errors=["x: boom"], finished_at=utcnow())

    monkeypatch.setattr(catalog, "refresh_from_content_api", fake_refresh)
    r = await client.post("/models/refresh-prices")
    assert (
        r.status_code == 200
        and "3 models" in r.text
        and "2 priced" in r.text
        and "1 error" in r.text
    )
    r = await client.get("/models")
    assert "Last refreshed" in r.text


async def test_settings_default_models_are_selects(client):
    r = await client.get("/settings")
    assert (
        '<select name="defaults.image_model">' in r.text
        and '<select name="defaults.video_model">' in r.text
    )
    assert '<select name="defaults.polish_model">' in r.text and "(use promptEnhance)" in r.text
    assert 'value="runware:101@1" selected' in r.text
    # a saved value that is not in the catalog is preserved
    await client.post("/settings", data={"defaults.image_model": "runware:101@1"})
    r = await client.post("/settings", data={"defaults.video_model": "someone:custom@1"})
    assert r.status_code == 200 and "someone:custom@1 (not in catalog)" in r.text
