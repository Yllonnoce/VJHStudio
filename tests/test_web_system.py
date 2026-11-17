import httpx

from vjhstudio.web.routes import system as system_routes


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


async def test_local_only_accepts_this_machines_own_lan_address(app, monkeypatch):
    """Behind a reverse proxy on the same machine (Nginx Proxy Manager), a browser on
    this computer arrives as the machine's LAN address, not 127.0.0.1."""
    monkeypatch.setattr(system_routes.restart_svc, "request_restart", lambda: "launcher")
    monkeypatch.setattr(system_routes.netinfo, "is_own_address", lambda h: h == "10.0.0.77")
    async with app.router.lifespan_context(app):
        for peer, expected in (("10.0.0.77", 202), ("10.0.0.50", 403)):
            transport = httpx.ASGITransport(app=app, client=(peer, 0))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.post("/api/restart")
            assert r.status_code == expected, peer


async def test_own_lan_address_is_not_local_when_listening_on_the_network(app, monkeypatch):
    """Bound beyond loopback, a Docker bridge or NAT can hand any outside client one
    of this machine's addresses, so only real loopback passes."""
    monkeypatch.setattr(system_routes.restart_svc, "request_restart", lambda: "launcher")
    monkeypatch.setattr(system_routes.netinfo, "is_own_address", lambda h: True)
    async with app.router.lifespan_context(app):
        app.state.host = "0.0.0.0"
        transport = httpx.ASGITransport(app=app, client=("10.0.0.77", 0))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/restart")
    assert r.status_code == 403
