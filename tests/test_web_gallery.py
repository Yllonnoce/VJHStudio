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
