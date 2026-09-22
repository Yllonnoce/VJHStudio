from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from ... import db
from ...models import Project
from ...services import dashboard
from .. import deps
from ..urls import output_url, thumb_url
from .jobs import panel_ctx

router = APIRouter()


def _slugs(session) -> dict[int, str]:
    return dict(session.execute(select(Project.id, Project.slug)).all())


@router.get("/")
def index(request: Request):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ctx = dashboard.context(s)
        slugs = _slugs(s)
        ctx["recent"] = [
            {
                "o": o,
                "url": output_url(slugs.get(o.project_id, ""), o.filename),
                "thumb": thumb_url(o.thumb_rel_path),
            }
            for o in ctx["recent"]
        ]
    # `jobs_active` and `today_spend` both come from here, so the "In progress" heading
    # and the panel it wraps are gated on one answer and the spend is counted once.
    ctx.update(panel_ctx(request))
    return deps.render(request, "pages/index.html", ctx)
