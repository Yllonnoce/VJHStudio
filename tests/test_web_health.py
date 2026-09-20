from vjhstudio import __version__


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["app"] == "VJHStudio" and body["version"] == __version__
    assert body["schema"] and body["boot_id"] and "port" in body

async def test_index_renders(client):
    r = await client.get("/")
    assert r.status_code == 200 and "VJHStudio" in r.text and "No API key" in r.text
