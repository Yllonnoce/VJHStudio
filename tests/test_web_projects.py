async def test_projects_crud(client):
    r = await client.get("/projects")
    assert r.status_code == 200 and "default" in r.text
    r = await client.post("/projects", data={"name": "Book covers"})
    assert r.status_code == 200 and "book-covers" in r.text
    pid = [p for p in (await client.get("/api/projects")).json() if p["slug"] == "book-covers"][0][
        "id"
    ]
    r = await client.post(f"/projects/{pid}", data={"name": "Book Covers 2"})
    assert "Book Covers 2" in r.text
    r = await client.post(f"/projects/{pid}/archive")
    assert r.status_code == 200
    assert all(p["slug"] != "book-covers" for p in (await client.get("/api/projects")).json())
    r = await client.post("/projects?return=select", data={"name": "Quick"})
    assert "<select" in r.text and "selected" in r.text and "Quick" in r.text


async def test_notify_toggle_saves(client):
    r = await client.post(
        "/settings", data={"_form": "general", "ui.notify_desktop": "on", "jobs.concurrency": "3"}
    )
    assert r.status_code == 200
    r = await client.get("/settings")
    # the brief's own literal ('name="..." checked') can never match: value="on" always
    # sits between the two attributes, so it would pass vacuously either way. Assert the
    # real rendered order instead, which actually distinguishes checked from unchecked.
    assert 'name="ui.notify_desktop" value="on" checked' in r.text
    await client.post("/settings", data={"_form": "general", "jobs.concurrency": "3"})
    assert 'name="ui.notify_desktop" value="on" checked' not in (await client.get("/settings")).text


async def test_projects_page_shows_folder_path_and_copy_button(client):
    r = await client.get("/projects")
    assert "<code>" in r.text and "navigator.clipboard.writeText" in r.text


async def test_unknown_project_404s(client):
    assert (await client.post("/projects/999999", data={"name": "x"})).status_code == 404
    assert (await client.post("/projects/999999/archive")).status_code == 404


async def test_hx_projects_select(client):
    r = await client.get("/hx/projects/select")
    assert "<select" in r.text and "Default" in r.text
