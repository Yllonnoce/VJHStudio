"""The home dashboard: recents, balance and per-project totals, assembled from the
existing services in a single read. No web imports here — the route is what turns this
into a page.

The live queue is *not* assembled here. ``routes.jobs.panel_ctx`` already reads the
active jobs and today's spend for the queue panel the page embeds, and the route merges
that in; duplicating either would mean two answers to the same question on one render —
which is exactly how the "In progress" heading came to disagree with the panel beneath it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from . import account, costs, outputs, projects


def context(session: Session) -> dict:
    """Everything ``routes.pages.index`` needs that the queue panel does not supply."""
    recent, outputs_total = outputs.gallery(session, per_page=12)
    by_project = costs.totals_by_project(session)  # one grouped query, not one per project
    project_rows = [
        {"id": p.id, "name": p.name, **by_project.get(p.id, {"outputs": 0, "cost": 0.0})}
        for p in projects.list_active(session)
    ]
    return {
        "recent": recent,
        "balance": account.cached_balance(session),
        "outputs_total": outputs_total,
        "projects": project_rows,
    }
