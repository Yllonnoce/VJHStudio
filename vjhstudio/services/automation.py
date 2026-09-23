"""Spend cap and tagging for jobs started by an agent over MCP.

The cap lives here, next to the queue, and not in the agent host: every tool that
can spend money calls ``check`` before a Job row exists, so a refusal costs nothing
and leaves nothing behind. A cap of 0 means "no cap" and is the only way to let a
model whose price is unknown through."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.job import Job
from . import settings

MCP = "mcp"
WEB = "web"
ESTIMATE_KEY = "_estimate"


class CapExceeded(ValueError):
    """Raised before any job row exists; the message is shown to the agent verbatim."""


@dataclass
class CapStatus:
    cap_usd: float
    spent_usd: float
    jobs_today: int
    max_jobs: int

    @property
    def remaining_usd(self) -> float | None:
        return None if self.cap_usd <= 0 else max(0.0, self.cap_usd - self.spent_usd)


def _today_start() -> datetime:
    """Midnight UTC, naive -- ``jobs.created_at`` is stored naive UTC, like usage rows."""
    now = datetime.now(UTC).replace(tzinfo=None)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def status(session: Session) -> CapStatus:
    rows = session.scalars(
        select(Job).where(Job.source == MCP, Job.created_at >= _today_start())
    ).all()
    spent = 0.0
    for job in rows:
        if job.cost is not None:
            spent += float(job.cost)
        else:
            est = (job.request_json or {}).get(ESTIMATE_KEY)
            spent += float(est) if est is not None else 0.0
    return CapStatus(
        cap_usd=float(settings.get(session, "mcp.daily_cap_usd")),
        spent_usd=spent,
        jobs_today=len(rows),
        max_jobs=int(settings.get(session, "mcp.max_jobs_per_day")),
    )


def check(session: Session, estimate_usd: float | None) -> None:
    st = status(session)
    if st.max_jobs > 0 and st.jobs_today >= st.max_jobs:
        raise CapExceeded(
            f"The agent job limit is {st.max_jobs} jobs per day "
            f"and {st.jobs_today} were already started today."
        )
    if st.cap_usd <= 0:
        return
    if estimate_usd is None:
        raise CapExceeded(
            "This model has no known price, so it cannot be started under a spend cap. "
            "Raise the cap to 0 in Settings to allow it."
        )
    if st.spent_usd + float(estimate_usd) > st.cap_usd:
        raise CapExceeded(
            f"This job (about ${estimate_usd:.2f}) would take today's agent spend to "
            f"${st.spent_usd + estimate_usd:.2f}, over the ${st.cap_usd:.2f} cap."
        )


def tag(job: Job, estimate_usd: float | None) -> None:
    job.source = MCP
    job.request_json = dict(job.request_json or {}) | {ESTIMATE_KEY: estimate_usd}
