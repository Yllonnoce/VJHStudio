"""Cross-site request protection for the mutating routes.

VJHStudio listens on 127.0.0.1, so any page the user happens to visit can POST
to it: the browser is the delivery vehicle and the peer address is still local,
which is why a client-IP check is no defence at all. There is no login to
protect, so the browser-supplied hints are enough and cost nothing:

* ``Sec-Fetch-Site: cross-site`` — sent by current browsers and not settable
  from page script.
* ``Origin`` — sent on every cross-origin state-changing request; compared
  against the ``Host`` the request was addressed to.

Both headers are absent from curl, from the launcher's own requests and from
very old browsers, so a *missing* header is allowed: this closes the
browser-driven hole without breaking local scripting.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..mcp.http import is_mcp_path

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
BLOCKED_MESSAGE = "cross-site request blocked"


def is_cross_site(method: str, headers: Headers) -> bool:
    """True when a state-changing request visibly comes from another origin."""
    if method.upper() not in UNSAFE_METHODS:
        return False
    if headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        return True
    origin = headers.get("origin")
    if origin:
        host = headers.get("host", "")
        if urlsplit(origin.strip()).netloc.lower() != host.strip().lower():
            return True
    return False


class CrossSiteBlockMiddleware:
    """Reject cross-site POST/PUT/PATCH/DELETE with 403 before routing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # /mcp is guarded by its bearer token (mcp.http.BearerMiddleware, outermost),
        # and an agent host may legitimately send an Origin the browser rule rejects.
        if (
            scope["type"] == "http"
            and not is_mcp_path(scope.get("path", ""))
            and is_cross_site(scope["method"], Headers(scope=scope))
        ):
            response = JSONResponse({"error": BLOCKED_MESSAGE}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
