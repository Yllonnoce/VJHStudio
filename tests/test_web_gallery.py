FORM = {
    "project_id": "1",
    "model": "runware:101@1",
    "subject": "castle",
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


async def test_gallery_lists_and_filters(client, fake, app):
    await _make(client, fake, app, n=3)
    r = await client.get("/gallery")
    assert r.status_code == 200 and r.text.count("/files/thumbs/") == 3
    r = await client.get("/hx/gallery?q=castle&kind=image&project_id=1")
    assert r.text.count("/files/thumbs/") == 3
    assert (await client.get("/hx/gallery?q=zzz")).text.count("/files/thumbs/") == 0


async def test_detail_favourite_delete(client, fake, app):
    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/hx/outputs/{oid}")
    assert "castle" in r.text and "Remix" in r.text and "Delete" in r.text
    assert "★" in (await client.post(f"/outputs/{oid}/favourite")).text
    assert (await client.get("/hx/gallery?favourite=1")).text.count("/files/thumbs/") == 1
    r = await client.delete(f"/outputs/{oid}")
    assert r.status_code == 200 and "close-lightbox" in r.headers.get("HX-Trigger", "")
    assert (await client.get(f"/hx/outputs/{oid}")).status_code == 404


async def test_gallery_load_more_pages(client, fake, app):
    await _make(client, fake, app, n=3)
    r = await client.get("/hx/gallery?page=1")
    assert r.text.count("/files/thumbs/") == 3
    assert "Load more" not in r.text  # only 3 outputs, well under 48/page


async def test_use_as_reference_imports_the_output_and_redirects(client, fake, app):
    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]

    card = await client.get("/hx/gallery?page=1")
    assert f'hx-post="/outputs/{oid}/as-asset"' in card.text and "Use as reference" in card.text
    detail = await client.get(f"/hx/outputs/{oid}")
    assert f'hx-post="/outputs/{oid}/as-asset"' in detail.text and "Use as reference" in detail.text

    r = await client.post(f"/outputs/{oid}/as-asset", follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/generate?ref=asset:") and "role=reference" in location

    grid = await client.get("/assets")
    assert "from-output" in grid.text and ".png" in grid.text
    # the imported asset is a real, usable chip
    assert (await client.get(location)).status_code == 200

    # htmx gets a redirect header instead of a body it would try to swap
    r = await client.post(f"/outputs/{oid}/as-asset", headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Redirect"] == location


async def test_use_as_reference_is_image_only(client, fake, app):
    from vjhstudio import db, models

    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    with db.session_scope(app.state.boot.session_factory) as s:
        s.get(models.Output, oid).kind = "video"
    r = await client.post(f"/outputs/{oid}/as-asset", follow_redirects=False)
    assert r.status_code == 415
    assert (await client.post("/outputs/999999/as-asset")).status_code == 404


async def test_output_download_redirects(client, fake, app):
    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/outputs/{oid}/download", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "download=1" in r.headers["location"]


async def test_unknown_output_404s(client):
    assert (await client.get("/hx/outputs/999999")).status_code == 404
    assert (await client.post("/outputs/999999/favourite")).status_code == 404
    assert (await client.delete("/outputs/999999")).status_code == 404


async def test_gallery_malformed_filters_dont_500(client):
    r = await client.get("/gallery?project_id=nope&date_from=not-a-date&date_to=also-bad")
    assert r.status_code == 200
    r = await client.get("/hx/gallery?project_id=nope&date_from=not-a-date")
    assert r.status_code == 200


async def test_gallery_filters_push_the_page_url_not_a_fragment(client, fake, app):
    """hx-push-url must land on /gallery?... (bookmarkable, GET renders the same filters),
    never on /hx/gallery?... (a fragment route with no filter form of its own)."""
    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    await client.post(f"/outputs/{oid}/favourite")
    r = await client.get("/gallery?q=castle&favourite=1")
    assert r.status_code == 200
    assert 'value="castle"' in r.text
    assert 'name="favourite" value="1" checked' in r.text
    assert r.text.count("/files/thumbs/") == 1
    assert 'hx-get="/gallery"' in r.text and 'hx-select="#gallery-grid"' in r.text
    assert 'hx-get="/hx/gallery"' not in r.text.split("</form>")[0]
