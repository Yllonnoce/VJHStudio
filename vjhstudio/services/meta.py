from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AppMeta


def get(session: Session, key: str) -> str | None:
    row = session.get(AppMeta, key)
    return row.value if row else None


def set(session: Session, key: str, value: str) -> None:  # noqa: A001
    row = session.get(AppMeta, key)
    if row:
        row.value = value
    else:
        session.add(AppMeta(key=key, value=value))
    session.flush()
