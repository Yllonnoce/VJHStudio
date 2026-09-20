import httpx

from runwarestudio.web.routes import system as system_routes


async def test_restart_endpoint(client, monkeypatch):
    monkeypatch.setattr(system_routes.restart_svc, "request_restart", lambda: "launcher")
    r = await client.post("/api/restart")
    assert r.status_code == 202
    assert r.json() == {"restarting": True, "strategy": "launcher"}


async def test_shutdown_endpoint(client, monkeypatch):
    calls = []
    monkeypatch.setattr(system_routes.restart_svc, "request_shutdown", lambda: calls.append(True))
    r = await client.post("/api/shutdown")
    assert r.status_code == 202
    assert r.json() == {"stopping": True}
    assert calls == [True]


async def test_restart_and_shutdown_refuse_non_local_client(app, monkeypatch):
    monkeypatch.setattr(system_routes.restart_svc, "request_restart", lambda: "launcher")
    monkeypatch.setattr(system_routes.restart_svc, "request_shutdown", lambda: None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("10.0.0.5", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r1 = await c.post("/api/restart")
            r2 = await c.post("/api/shutdown")
    assert r1.status_code == 403
    assert r2.status_code == 403
