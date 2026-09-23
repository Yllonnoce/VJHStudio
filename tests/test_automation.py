from datetime import datetime, timedelta

import pytest

from vjhstudio import boot as _boot
from vjhstudio import db, secrets
from vjhstudio.models.job import Job, JobStatus
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import automation, generate, settings


@pytest.fixture
def app(app):
    """These tests never enter the lifespan, which is where ``app.state.boot`` is set."""
    app.state.boot = _boot.boot(app.state.paths)
    return app


def _job(session, cost=None, estimate=None, source="mcp", days_ago=0):
    job = Job(
        id=f"j-{len(session.query(Job).all())}",
        project_id=1,
        kind="image",
        status=JobStatus.succeeded.value if cost is not None else JobStatus.queued.value,
        model_air="runware:101@1",
        request_json={"_estimate": estimate} if estimate is not None else {},
        source=source,
        cost=cost,
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    session.add(job)
    session.flush()
    return job


def test_status_counts_estimates_until_a_cost_is_known(app):
    with db.session_scope(app.state.boot.session_factory) as s:
        _job(s, estimate=0.5)  # queued: estimate counts
        _job(s, cost=0.2, estimate=0.9)  # finished: real cost replaces the estimate
        _job(s, cost=5.0, source="web")  # browser jobs never count
        _job(s, cost=5.0, days_ago=1)  # yesterday never counts
        st = automation.status(s)
        assert st.spent_usd == pytest.approx(0.7)
        assert st.jobs_today == 2
        assert st.cap_usd == 2.0 and st.max_jobs == 20


def test_check_refuses_past_the_cap_and_unknown_estimates(app):
    with db.session_scope(app.state.boot.session_factory) as s:
        _job(s, estimate=1.9)
        automation.check(s, 0.05)
        with pytest.raises(automation.CapExceeded, match="cap"):
            automation.check(s, 0.2)
        with pytest.raises(automation.CapExceeded, match="price"):
            automation.check(s, None)
        settings.set_many(s, {"mcp.daily_cap_usd": "0"})
        automation.check(s, None)  # 0 = no cap, unknown prices allowed
        settings.set_many(s, {"mcp.max_jobs_per_day": "1"})
        with pytest.raises(automation.CapExceeded, match="jobs"):
            automation.check(s, 0.01)


def test_enqueue_with_source_mcp_tags_and_refuses_cleanly(app):
    sf = app.state.boot.session_factory
    req = ImageRequest(project_id=1, model="runware:101@1", form=PromptForm(subject="a fox"))
    job = generate.enqueue_image(
        sf, app.state.paths, req, default_negative="", source="mcp", estimate_usd=0.01
    )
    with db.session_scope(sf) as s:
        row = s.get(Job, job.id)
        assert row.source == "mcp" and row.request_json["_estimate"] == 0.01
        settings.set_many(s, {"mcp.daily_cap_usd": "0.005"})
    with pytest.raises(automation.CapExceeded):
        generate.enqueue_image(
            sf, app.state.paths, req, default_negative="", source="mcp", estimate_usd=0.01
        )
    with db.session_scope(sf) as s:
        assert s.query(Job).count() == 1  # the refusal left no row behind
    job2 = generate.enqueue_image(sf, app.state.paths, req, default_negative="")
    with db.session_scope(sf) as s:
        assert s.get(Job, job2.id).source == "web"


def test_mcp_token_stash(paths, monkeypatch):
    assert secrets.read_mcp_token(paths) is None
    tok = secrets.rotate_mcp_token(paths)
    assert len(tok) >= 40 and secrets.read_mcp_token(paths) == tok
    assert secrets.rotate_mcp_token(paths) != tok
    assert secrets.effective_mcp_token(paths, {"VJHSTUDIO_MCP_TOKEN": "env-wins"}) == "env-wins"
