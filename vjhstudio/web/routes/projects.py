"""Projects: table view, inline create/rename/archive, and the JSON list Generate reads from.

The inline creator on the Generate page posts here too (``?return=select``): instead of a
row, it gets back ``generate/_project_select.html`` re-rendered with the new project
selected, which is what ``#project-select-wrap`` expects to be replaced with.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from ... import db
from ...services import costs, projects
from .. import deps

router = APIRouter()


def _row_ctx(
    session, paths, project, *, show_archived: bool = False, toast: str | None = None
) -> dict:
    return {
        "p": project,
        "totals": projects.totals(session, project.id),
        "folder": projects.root_for(session, paths) / project.slug,
        "show_archived": show_archived,
        "toast": toast,
    }


@router.get("/projects")
def projects_page(request: Request, show_archived: int = 0):
    paths = request.app.state.paths
    with db.session_scope(request.app.state.boot.session_factory) as s:
        rows = projects.list_all(s) if show_archived else projects.list_active(s)
        ctx_rows = [_row_ctx(s, paths, p, show_archived=bool(show_archived)) for p in rows]
    return deps.render(
        request,
        "pages/projects.html",
        {"rows": ctx_rows, "show_archived": bool(show_archived)},
    )


@router.post("/projects")
def create_project(request: Request, form: deps.Form, return_: str = Query("", alias="return")):
    # the Generate page's inline creator posts its own field name (see _project_select.html)
    name = str(form.get("name", "") or form.get("new_project_name", "") or "").strip()
    description = str(form.get("description", "") or "").strip() or None
    paths = request.app.state.paths
    with db.session_scope(request.app.state.boot.session_factory) as s:
        p = projects.create(s, paths, name, description)
        if return_ == "select":
            ctx = {"projects": projects.list_active(s), "selected_project": p.id}
            return deps.render(request, "generate/_project_select.html", ctx)
        ctx = _row_ctx(s, paths, p, toast=f'Project "{p.name}" created.')
        return deps.render(request, "projects/_row.html", ctx)


@router.post("/projects/{project_id}")
def rename_project(request: Request, project_id: int, form: deps.Form):
    name = str(form.get("name", "") or "").strip()
    paths = request.app.state.paths
    with db.session_scope(request.app.state.boot.session_factory) as s:
        existing = projects.get(s, project_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="unknown project")
        # a bare rename (no description field posted) must not blank the existing one
        description = str(form.get("description", existing.description or "")).strip() or None
        p = projects.rename(s, project_id, name, description)
        ctx = _row_ctx(s, paths, p)
        return deps.render(request, "projects/_row.html", ctx)


@router.post("/projects/{project_id}/archive")
def archive_project(request: Request, project_id: int, show_archived: int = 0):
    paths = request.app.state.paths
    with db.session_scope(request.app.state.boot.session_factory) as s:
        existing = projects.get(s, project_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="unknown project")
        p = projects.set_archived(s, project_id, not existing.is_archived)
        if p.is_archived and not show_archived:
            # the row disappears from a hide-archived view: nothing left to swap in
            return Response(status_code=200)
        ctx = _row_ctx(s, paths, p, show_archived=bool(show_archived))
        return deps.render(request, "projects/_row.html", ctx)


@router.get("/hx/projects/select")
def hx_projects_select(request: Request, selected: str = ""):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        active = projects.list_active(s)
        ctx = {"projects": active, "selected_project": selected or None}
        return deps.render(request, "generate/_project_select.html", ctx)


@router.get("/api/projects")
def api_projects(request: Request, archived: int = 0):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        rows = projects.list_all(s) if archived else projects.list_active(s)
        totals = costs.totals_by_project(s)
        data = [
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "outputs": totals.get(p.id, {}).get("outputs", 0),
                "cost": totals.get(p.id, {}).get("cost", 0.0),
                "is_archived": p.is_archived,
            }
            for p in rows
        ]
        return JSONResponse(data)
