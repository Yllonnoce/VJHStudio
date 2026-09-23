"""The HTTP transport: the /mcp mount, the bearer token, health and file access.

The SDK client is opened and closed inside the test's own task (see
tests/test_mcp_tools.py): pytest-asyncio runs a fixture's setup and teardown in two
different tasks and the client holds an anyio task group that refuses to be left
from another one. The SDK talks to `httpx2`, not the app's `httpx`, so the ASGI
transport handed to `streamable_http_client` comes from that package.
"""

import httpx
import httpx2
import pytest
from mcp.client import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tests.fakes.fake_runware import fake_factory
from vjhstudio import secrets
from vjhstudio.mcp.http import token_matches
from vjhstudio.web.app import create_app, mcp_base_url

TOKEN = "t0ken"
REMOTE = ("10.0.0.5", 1234)


def _app(paths, fake, download_transport, **env):
    return create_app(
        paths,
        client_factory=fake_factory(fake),
        env={"VJHSTUDIO_OFFLINE": "1", **env},
        auto_refresh=False,
        download_transport=download_transport,
    )


@pytest.fixture
def mcp_app(paths, fake, download_transport):
    return _app(
        paths,
        fake,
        download_transport,
        VJHSTUDIO_MCP_ENABLED="1",
        VJHSTUDIO_MCP_TOKEN=TOKEN,
    )


async def test_mcp_requires_the_bearer_token(mcp_app):
    async with (
        mcp_app.router.lifespan_context(mcp_app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://test") as c,
    ):
        r = await c.post("/mcp", json={})
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Bearer")
        r = await c.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        # Everything else is untouched by the middleware.
        assert (await c.get("/api/health")).status_code == 200


async def test_mcp_lists_and_calls_tools_over_http(mcp_app):
    async with mcp_app.router.lifespan_context(mcp_app):
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=mcp_app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        async with (
            streamable_http_client("http://test/mcp", http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as s,
        ):
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"generate_image", "list_projects"} <= names
            result = await s.call_tool("list_projects", {})
            assert not result.is_error


async def test_mcp_is_absent_when_disabled(client, app):
    assert app.state.mcp_server is None
    r = await client.post("/mcp", json={})
    assert r.status_code == 404


async def test_health_reports_whether_mcp_is_on(client, mcp_app):
    body = (await client.get("/api/health")).json()
    assert body["mcp"] == {"enabled": False}
    assert body["app"] == "VJHStudio"  # the existing keys stay
    async with (
        mcp_app.router.lifespan_context(mcp_app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://test") as c,
    ):
        assert (await c.get("/api/health")).json()["mcp"] == {"enabled": True}


async def test_enabling_without_a_token_creates_one(paths, fake, download_transport):
    app = _app(paths, fake, download_transport, VJHSTUDIO_MCP_ENABLED="1")
    assert app.state.mcp_server is not None
    assert secrets.read_mcp_token(paths)


async def test_files_serve_a_remote_agent_carrying_the_token(mcp_app, paths):
    """An agent on another machine downloads what it made.

    ``/files/*`` has never refused a non-loopback peer -- the README's "on your
    phone" workflow depends on that -- so the token is accepted here but not
    required. See the task report."""
    async with mcp_app.router.lifespan_context(mcp_app):
        (paths.outputs / "default").mkdir(parents=True, exist_ok=True)
        (paths.outputs / "default" / "a.png").write_bytes(b"png")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_app, client=REMOTE), base_url="http://test"
        ) as c:
            r = await c.get(
                "/files/outputs/default/a.png", headers={"Authorization": f"Bearer {TOKEN}"}
            )
            assert r.status_code == 200
            assert r.content == b"png"


def test_token_matches_is_exact_and_never_raises():
    assert token_matches(TOKEN, TOKEN)
    assert not token_matches("t0", TOKEN)
    assert not token_matches("", TOKEN)
    assert not token_matches(TOKEN, None)
    assert not token_matches(TOKEN, "")
    # a stray high byte in the header (latin-1 decoded) is a wrong token, not a 500
    assert not token_matches("t\xff0ken", TOKEN)


def test_mcp_base_url_prefers_a_real_bound_host():
    assert mcp_base_url({}, 8080) == "http://127.0.0.1:8080"
    assert mcp_base_url({"VJHSTUDIO_HOST": "192.168.1.9"}, 8080) == "http://192.168.1.9:8080"
    # 0.0.0.0 is not an address a client can dial: a LAN address takes its place.
    assert mcp_base_url({"VJHSTUDIO_HOST": "0.0.0.0"}, 80).startswith("http://")
    assert not mcp_base_url({"VJHSTUDIO_HOST": "0.0.0.0"}, 80).startswith("http://0.0.0.0")


async def test_the_server_gets_the_computed_base_url(paths, fake, download_transport):
    app = _app(
        paths,
        fake,
        download_transport,
        VJHSTUDIO_MCP_ENABLED="1",
        VJHSTUDIO_MCP_TOKEN=TOKEN,
    )
    assert app.state.mcp_base_url == "http://127.0.0.1:8080"


async def test_cross_site_origin_does_not_block_a_tokened_mcp_post(mcp_app):
    """Agent hosts may send an Origin the browser rule would reject; /mcp is
    guarded by the bearer token instead, so the request reaches the SDK."""
    async with (
        mcp_app.router.lifespan_context(mcp_app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://test") as c,
    ):
        r = await c.post(
            "/mcp",
            json={},
            headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://elsewhere:9"},
        )
        assert r.status_code not in (401, 403)
        r = await c.post("/settings", data={}, headers={"Origin": "http://elsewhere:9"})
        assert r.status_code == 403  # the browser rule still holds everywhere else
