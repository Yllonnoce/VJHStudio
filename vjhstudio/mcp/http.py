"""The HTTP transport: the SDK's streamable-HTTP endpoint at ``/mcp``, behind a token.

The SDK builds a Starlette app holding exactly one route. Mounting that app under
``/mcp`` with an inner path of ``/`` makes Starlette answer ``POST /mcp`` with a 307
to ``/mcp/`` -- which no MCP client follows with its body -- so the route is asked
for at ``/mcp`` directly and appended to the FastAPI router instead. The guard is a
pure-ASGI middleware over the whole app that only looks at ``/mcp`` paths; every
other route is passed through untouched, so browser users see no change.
"""

from __future__ import annotations

import hmac

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MCP_PATH = "/mcp"
REALM = 'Bearer realm="vjhstudio"'
UNAUTHORIZED = "a valid bearer token is required"


def bearer(headers) -> str:
    """The token out of an ``Authorization: Bearer <token>`` header, or ``""``."""
    value = headers.get("authorization") or ""
    return value[7:].strip() if value[:7].lower() == "bearer " else ""


def token_matches(given: str, token: str | None) -> bool:
    """True when ``given`` is exactly the configured token.

    Constant-time, and compared as bytes: ``hmac.compare_digest`` refuses non-ASCII
    ``str`` with a TypeError, and a stray high byte in a header must be a plain 401,
    never a 500.
    """
    if not token or not given:
        return False
    return hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8"))


def is_mcp_path(path: str) -> bool:
    return path == MCP_PATH or path.startswith(MCP_PATH + "/")


class BearerMiddleware:
    """401 every ``/mcp`` request that does not carry the token. Nothing else."""

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and is_mcp_path(scope.get("path", "")):
            given = bearer(Headers(scope=scope))
            if not token_matches(given, self.token):
                response = JSONResponse(
                    {"error": UNAUTHORIZED},
                    status_code=401,
                    headers={"WWW-Authenticate": REALM},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def mount_mcp(app, server, token: str | None) -> None:
    """Serve ``server`` at ``/mcp`` on ``app``, guarded by ``token``.

    DNS-rebinding protection is off: it checks ``Host`` against a fixed allow-list,
    and this app is reached by whatever name the LAN gives it. The bearer token is
    the actual guard, and it is checked before the SDK sees the request.
    """
    sub = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app.router.routes.extend(sub.routes)
    app.add_middleware(BearerMiddleware, token=token)
