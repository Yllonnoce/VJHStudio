from __future__ import annotations
from pathlib import Path
from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def is_hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    """Render a template with the standard globals merged in."""
    app = request.app
    base = {
        "request": request,
        "app_version": app.state.boot.version,
        "commit_short": app.state.boot.commit.short if app.state.boot.commit else "",
        "theme": app.state.theme(),
        "has_api_key": app.state.api_key() is not None,
        "key_source": app.state.key_source(),
    }
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status_code)
