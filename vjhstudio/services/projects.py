"""Projects: unique slugs, output directories, archive flag, per-project totals."""

from __future__ import annotations

from pathlib import Path

from slugify import slugify as _slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Paths
from ..models import Project
from . import costs
from . import settings as settings_svc

SLUG_MAX = 60


def slugify(name: str) -> str:
    return _slugify(name or "", max_length=SLUG_MAX) or "project"


def outputs_root(paths: Paths, outputs_dir: str = "") -> Path:
    """The directory every project folder lives in. A blank override means the default."""
    return Path(outputs_dir).expanduser() if outputs_dir else paths.outputs


def root_override(session: Session) -> str:
    return str(settings_svc.get(session, "paths.outputs_dir") or "")


def root_for(session: Session, paths: Paths) -> Path:
    return outputs_root(paths, root_override(session))


def dir_for(paths: Paths, slug: str, outputs_dir: str = "") -> Path:
    return outputs_root(paths, outputs_dir) / slug


def unique_slug(session: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 1
    while session.execute(select(Project.id).where(Project.slug == slug)).first() is not None:
        n += 1
        slug = f"{base[: SLUG_MAX - len(str(n)) - 1]}-{n}"
    return slug


def create(session: Session, paths: Paths, name: str, description: str | None = None) -> Project:
    p = Project(
        name=name.strip() or "Untitled", slug=unique_slug(session, name), description=description
    )
    session.add(p)
    session.flush()
    dir_for(paths, p.slug, root_override(session)).mkdir(parents=True, exist_ok=True)
    return p


def get(session: Session, project_id: int) -> Project | None:
    return session.get(Project, project_id)


def list_active(session: Session) -> list[Project]:
    q = (
        select(Project)
        .where(Project.is_archived.is_(False))
        .order_by(Project.created_at, Project.id)
    )
    return list(session.execute(q).scalars())


def list_all(session: Session) -> list[Project]:
    return list(session.execute(select(Project).order_by(Project.created_at, Project.id)).scalars())


def rename(session: Session, project_id: int, name: str, description: str | None = None) -> Project:
    """The slug (and therefore the output directory) is deliberately stable across renames."""
    p = session.get(Project, project_id)
    if p is None:
        raise LookupError(project_id)
    p.name = name.strip() or p.name
    p.description = description
    session.flush()
    return p


def set_archived(session: Session, project_id: int, flag: bool) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise LookupError(project_id)
    p.is_archived = bool(flag)
    session.flush()
    return p


def totals(session: Session, project_id: int) -> dict:
    return costs.totals_by_project(session).get(project_id, {"outputs": 0, "cost": 0.0})
