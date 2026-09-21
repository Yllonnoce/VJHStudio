from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

DARK_THEMES = ("midnight", "crimson", "ember", "royal", "steel")


_DEFAULT_GLOBALS = {
    "theme": "midnight",
    "notify_desktop": False,
    "active_jobs": 0,
    "unseen_jobs": 0,
    "update_behind": 0,
}


def is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _globals(request: Request) -> dict:
    """Theme, notify flag and the two badge counts, computed once per request in one
    session instead of once per value in three."""
    cached = getattr(request.state, "vjh_globals", None)
    if cached is None:
        source = getattr(request.app.state, "render_globals", None)
        cached = dict(_DEFAULT_GLOBALS) | (source() if source is not None else {})
        request.state.vjh_globals = cached
    return cached


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    """Render a template with the standard globals merged in."""
    app = request.app
    base = {
        "request": request,
        "app_version": app.state.boot.version,
        "commit_short": app.state.boot.commit.short if app.state.boot.commit else "",
        "dark_themes": DARK_THEMES,
        "has_api_key": app.state.api_key() is not None,
        "key_source": app.state.key_source(),
        **_globals(request),
    }
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status_code)


async def _form(request: Request) -> FormData:
    """Read the body in the event loop so the handler itself can be a plain
    ``def``: FastAPI then runs it in the threadpool, where blocking SQLAlchemy
    calls cannot stall every other request."""
    return await request.form()


Form = Annotated[FormData, Depends(_form)]
