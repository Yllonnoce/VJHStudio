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


async def _second_project(client, name="Book covers"):
    await client.post("/projects", data={"name": name})
    return [p for p in (await client.get("/api/projects")).json() if p["name"] == name][0]


async def test_move_an_output_to_another_project(client, fake, app):
    from vjhstudio import db, models

    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    other = await _second_project(client)

    detail = await client.get(f"/hx/outputs/{oid}")
    assert f'hx-post="/outputs/{oid}/move"' in detail.text
    assert '<option value="1" selected>' in detail.text  # the project it is in now
    assert f'<option value="{other["id"]}" >' in detail.text

    r = await client.post(f"/outputs/{oid}/move", data={"project_id": str(other["id"])})
    assert r.status_code == 200 and "Moved." in r.text
    trigger = r.headers.get("HX-Trigger", "")
    assert "outputs-changed" in trigger and "jobs-changed" in trigger
    # the panel stays open on the same output, now pointing at the new project
    assert f'hx-post="/outputs/{oid}/move"' in r.text
    assert f'<option value="{other["id"]}" selected>' in r.text
    assert other["slug"] in r.text

    with db.session_scope(app.state.boot.session_factory) as s:
        o = s.get(models.Output, oid)
        assert o.project_id == other["id"]
        assert (app.state.paths.outputs / o.rel_path).is_file()
        assert not (app.state.paths.outputs / "default" / o.filename).exists()

    assert f'id="output-{oid}"' in (await client.get(f"/hx/gallery?project_id={other['id']}")).text
    assert f'id="output-{oid}"' not in (await client.get("/hx/gallery?project_id=1")).text
    # the project table follows: one output and the job's spend under the new project
    row = (await client.get("/projects")).text.split(f'id="project-{other["id"]}"')[1]
    assert "<td>1</td>" in row
    totals = {p["id"]: p for p in (await client.get("/api/projects")).json()}
    assert totals[other["id"]]["outputs"] == 1 and totals[1]["outputs"] == 0
    assert totals[other["id"]]["cost"] > 0 and totals[1]["cost"] == 0.0


async def test_move_to_a_bad_project_is_a_422_with_a_message(client, fake, app):
    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]

    r = await client.post(f"/outputs/{oid}/move", data={"project_id": "999999"})
    assert r.status_code == 422 and "Could not move this output" in r.text
    assert "HX-Trigger" not in r.headers

    r = await client.post(f"/outputs/{oid}/move", data={"project_id": ""})
    assert r.status_code == 422 and "Pick a project" in r.text

    shelved = await _second_project(client, "Shelved")
    await client.post(f"/projects/{shelved['id']}/archive")
    r = await client.post(f"/outputs/{oid}/move", data={"project_id": str(shelved["id"])})
    assert r.status_code == 422 and "archived" in r.text

    assert (await client.post("/outputs/999999/move", data={"project_id": "1"})).status_code == 404
    # nothing moved: the output is still in its own project
    assert f'id="output-{oid}"' in (await client.get("/hx/gallery?project_id=1")).text


async def test_the_gallery_grid_refetches_when_an_output_is_moved(client):
    r = await client.get("/gallery")
    assert 'hx-trigger="close-lightbox from:body, outputs-changed from:body"' in r.text
