"""Any page the user visits could otherwise POST to 127.0.0.1:8080 and wipe the DB."""
import httpx

from vjhstudio.web.routes import system as system_routes

CLEAR = "/settings/database/clear"


async def test_cross_site_origin_is_blocked(client):
    r = await client.post(CLEAR, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"error": "cross-site request blocked"}


async def test_sec_fetch_site_cross_site_is_blocked(client):
    r = await client.post(CLEAR, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


async def test_same_origin_request_passes(client):
    r = await client.post(CLEAR, headers={"Origin": "http://test", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200


async def test_request_without_fetch_metadata_passes(client):
    """curl, the launcher and old browsers send neither header."""
    r = await client.post(CLEAR)
    assert r.status_code == 200


async def test_cross_site_get_is_allowed(client):
    r = await client.get("/settings", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


async def test_shutdown_is_protected_from_cross_site_posts(app, monkeypatch):
    calls = []
    monkeypatch.setattr(system_routes.restart_svc, "request_shutdown", lambda: calls.append(True))
    async with (app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c):
        r = await c.post("/api/shutdown", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert calls == []
