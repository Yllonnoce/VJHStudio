"""Usage accounting and the latency estimate that drives the progress bar."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Job, Output, UsageEntry, utcnow
from . import catalog

DEFAULT_EXPECTED_MS = 20000


def record_usage(
    session: Session, *, job: Job | None, task_type: str, cost: float, model_air: str | None
) -> UsageEntry:
    entry = UsageEntry(
        job_id=job.id if job is not None else None,
        project_id=job.project_id if job is not None else None,
        task_type=task_type,
        model_air=model_air,
        cost=float(cost or 0.0),
        day=utcnow().date(),
    )
    session.add(entry)
    session.flush()
    return entry


def today_spend(session: Session) -> float:
    q = select(func.sum(UsageEntry.cost)).where(UsageEntry.day == utcnow().date())
    return float(session.execute(q).scalar() or 0.0)


def spend_since(session: Session, day) -> float:
    q = select(func.sum(UsageEntry.cost)).where(UsageEntry.day >= day)
    return float(session.execute(q).scalar() or 0.0)


def totals_by_project(session: Session) -> dict[int, dict]:
    out: dict[int, dict] = {}
    counts = session.execute(
        select(Output.project_id, func.count(Output.id)).group_by(Output.project_id)
    ).all()
    for pid, n in counts:
        out.setdefault(pid, {"outputs": 0, "cost": 0.0})["outputs"] = int(n)
    spend = session.execute(
        select(UsageEntry.project_id, func.sum(UsageEntry.cost)).group_by(UsageEntry.project_id)
    ).all()
    for pid, c in spend:
        if pid is not None:
            out.setdefault(pid, {"outputs": 0, "cost": 0.0})["cost"] = float(c or 0.0)
    return out


def observe_latency(session: Session, model_air: str, ms: float) -> None:
    """Rolling average of real round-trips, merged into the tiers blob (never replacing it)."""
    m = catalog.get_by_air(session, model_air)
    if m is None:
        return
    tiers = dict(m.price_tiers_json or {})
    observed = dict(tiers.get("observed") or {})
    n = int(observed.get("n", 0))
    avg = float(observed.get("avg_ms", 0.0))
    new_n = n + 1
    tiers["observed"] = {"avg_ms": int((avg * n + float(ms)) / new_n), "n": new_n}
    m.price_tiers_json = tiers  # reassign: JSON columns are not mutation-tracked
    session.flush()


def expected_ms(session: Session, model_air: str) -> int:
    m = catalog.get_by_air(session, model_air)
    tiers = dict((m.price_tiers_json if m else None) or {})
    observed = tiers.get("observed") or {}
    if int(observed.get("n", 0)) >= 1 and observed.get("avg_ms"):
        return int(observed["avg_ms"])
    typical = tiers.get("typical_latency_ms")
    return int(typical) if typical else DEFAULT_EXPECTED_MS
