"""The home dashboard: recents, live queue count, spend, balance and per-project totals,
assembled from the existing services in a single read. No web imports here — the route
is what turns this into a page.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from . import account, costs, outputs, projects


def context(session: Session, runner) -> dict:
    """Everything ``routes.pages.index`` needs to render the home page.

    ``runner`` is ``request.app.state.runner``; it is ``None`` before the app has
    finished starting up, in which case nothing is reported as active.
    """
    recent, outputs_total = outputs.gallery(session, per_page=12)
    by_project = costs.totals_by_project(session)  # one grouped query, not one per project
    project_rows = [
        {"id": p.id, "name": p.name, **by_project.get(p.id, {"outputs": 0, "cost": 0.0})}
        for p in projects.list_active(session)
    ]
    return {
        "recent": recent,
        "active_count": len(runner.active_ids()) if runner is not None else 0,
        "spend_today": costs.today_spend(session),
        "balance": account.cached_balance(session),
        "outputs_total": outputs_total,
        "projects": project_rows,
    }
