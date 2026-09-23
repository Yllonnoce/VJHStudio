# MCP Server (Phase 9) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose VJHStudio's generation, queue, gallery and project services over the Model Context Protocol, with a bearer-token HTTP transport for remote hosts, a stdio transport for local ones, and a spend cap inside the tools.

**Architecture:** One tools module (`vjhstudio/mcp/server.py`) builds an `MCPServer` from a context object holding the session factory, paths, runner and settings. The FastAPI app mounts the SDK's streamable HTTP app at `/mcp` behind a bearer middleware; the CLI gains `vjhstudio mcp` which boots its own runner and speaks stdio. `services/automation.py` holds the cap and tagging, called from `enqueue_image`/`enqueue_video` when `source="mcp"`.

**Tech Stack:** Python 3.12, FastAPI/Starlette, SQLAlchemy 2 + Alembic, `mcp>=2,<3` (official SDK, v2 API), pytest with the existing `FakeRunware` fixtures.

**Spec:** `docs/superpowers/specs/2026-09-23-mcp-server-design.md`

## Global Constraints

- SDK v2 only: `from mcp.server.mcpserver import MCPServer`; tests use `from mcp.client import Client` and pass the `MCPServer` object directly (in-memory). `mcp.server.fastmcp` does not exist in v2.
- `runware/` and `services/` never import `web/`. `vjhstudio/mcp/` is an interface layer like `web/` and may import `web.routes.generate` helpers (`estimate_ctx`, `video_estimate_ctx`, `_model_row`) but nothing else from `web`.
- Never print or log the API key or the MCP token. stdout of `vjhstudio mcp` is the protocol channel: all logging to stderr.
- Every generate tool refuses BEFORE any Job row exists when the cap is hit; refusals are `ValueError` with one user-facing sentence.
- Settings keys exactly: `mcp.enabled` (bool, False), `mcp.daily_cap_usd` (float, 2.0), `mcp.max_jobs_per_day` (int, 20). Env: `VJHSTUDIO_MCP_ENABLED`, `VJHSTUDIO_MCP_DAILY_CAP_USD`, `VJHSTUDIO_MCP_MAX_JOBS_PER_DAY`, `VJHSTUDIO_MCP_TOKEN`.
- Job column exactly `jobs.source` VARCHAR(8) NOT NULL DEFAULT `'web'`; values `web` and `mcp`.
- Windows scripts, if any, are `.bat` only. No Node, no build step.
- Commit trailer on every commit: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Full suite `uv run --frozen pytest -q`, `uv run --frozen ruff check .`, `uv run --frozen ruff format --check .` green before a task is reported done.

---

### Task 1: Foundation — migration, settings, token stash, cap service, tagged enqueue

**Files:**
- Create: `migrations/versions/0006_mcp.py`
- Modify: `vjhstudio/models/job.py` (add `source`)
- Modify: `vjhstudio/services/settings.py` (three keys in `SPEC`)
- Modify: `vjhstudio/secrets.py` (token stash)
- Create: `vjhstudio/services/automation.py`
- Modify: `vjhstudio/services/generate.py` (`source`, `estimate_usd` kwargs)
- Test: `tests/test_automation.py`, `tests/test_migrations.py` (existing autogenerate check must stay green)

**Interfaces:**
- Produces: `Job.source: str`; `settings.get(s, "mcp.enabled") -> bool` etc.; `secrets.read_mcp_token(paths, env=None) -> str | None`, `secrets.write_mcp_token(paths, token)`, `secrets.rotate_mcp_token(paths) -> str`, `secrets.effective_mcp_token(paths, env=None) -> str | None`; `automation.CapExceeded`, `automation.CapStatus(cap_usd, spent_usd, jobs_today, max_jobs)`, `automation.status(session) -> CapStatus`, `automation.check(session, estimate_usd: float | None) -> None`, `automation.tag(job, estimate_usd)`; `generate.enqueue_image(..., source="web", estimate_usd=None)` and `generate.enqueue_video(..., source="web", estimate_usd=None)`.

- [ ] **Step 1: Failing tests**

`tests/test_automation.py`:

```python
from datetime import datetime, timedelta

import pytest

from vjhstudio import db, secrets
from vjhstudio.models.job import Job, JobStatus
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import automation, generate, settings


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
        _job(s, estimate=0.5)          # queued: estimate counts
        _job(s, cost=0.2, estimate=0.9)  # finished: real cost replaces the estimate
        _job(s, cost=5.0, source="web")  # browser jobs never count
        _job(s, cost=5.0, days_ago=1)    # yesterday never counts
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
        settings.set(s, "mcp.daily_cap_usd", 0)
        automation.check(s, None)  # 0 = no cap, unknown prices allowed
        settings.set(s, "mcp.max_jobs_per_day", 1)
        with pytest.raises(automation.CapExceeded, match="jobs"):
            automation.check(s, 0.01)


def test_enqueue_with_source_mcp_tags_and_refuses_cleanly(app):
    sf = app.state.boot.session_factory
    req = ImageRequest(project_id=1, model="runware:101@1", form=PromptForm(subject="a fox"))
    job = generate.enqueue_image(sf, app.state.paths, req, default_negative="", source="mcp", estimate_usd=0.01)
    with db.session_scope(sf) as s:
        row = s.get(Job, job.id)
        assert row.source == "mcp" and row.request_json["_estimate"] == 0.01
        settings.set(s, "mcp.daily_cap_usd", 0.005)
    with pytest.raises(automation.CapExceeded):
        generate.enqueue_image(sf, app.state.paths, req, default_negative="", source="mcp", estimate_usd=0.01)
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
```

Check `settings.set` exists (grep `def set` in `services/settings.py`); if it is named differently, use that name in the tests.

- [ ] **Step 2: Run, expect failures** — `uv run --frozen pytest -q tests/test_automation.py` fails on imports.

- [ ] **Step 3: Migration and model**

`migrations/versions/0006_mcp.py` (copy the header style of `0005_phase7.py`, revision `"0006"`, down_revision `"0005"`):

```python
def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=8), nullable=False, server_default="web"))


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("source")
```

`models/job.py`: `source: Mapped[str] = mapped_column(String(8), default="web", server_default="web", nullable=False)`.

Run `uv run --frozen pytest -q tests/test_migrations.py` — the autogenerate-is-empty test must pass (adjust `server_default` on the model if it reports a diff).

- [ ] **Step 4: Settings keys** — add to `SPEC`:

```python
"mcp.enabled": Spec(bool, False, None, "VJHSTUDIO_MCP_ENABLED"),
"mcp.daily_cap_usd": Spec(float, 2.0, None, "VJHSTUDIO_MCP_DAILY_CAP_USD"),
"mcp.max_jobs_per_day": Spec(int, 20, None, "VJHSTUDIO_MCP_MAX_JOBS_PER_DAY"),
```

Check how `Spec(bool, …)` and `Spec(float, …)` are coerced in `settings.get`/`set` (look for the existing bool/float handling; add it if only str/int exist, parsing `"1"/"true"/"on"` as True).

- [ ] **Step 5: Token stash** in `secrets.py`, mirroring the API key functions (same directory, same permission helper):

```python
MCP_TOKEN_FILE = "mcp_token"

def read_mcp_token(paths: Paths) -> str | None: ...        # like read_api_key but for MCP_TOKEN_FILE
def write_mcp_token(paths: Paths, token: str) -> None: ... # like write_api_key
def rotate_mcp_token(paths: Paths) -> str:
    token = _pysecrets.token_urlsafe(32)
    write_mcp_token(paths, token)
    return token
def effective_mcp_token(paths: Paths, env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    return (env.get("VJHSTUDIO_MCP_TOKEN") or "").strip() or read_mcp_token(paths)
```

(`import secrets as _pysecrets` at the top, since the module is itself named `secrets`.)

- [ ] **Step 6: `services/automation.py`**

```python
"""Spend cap and tagging for jobs started by an agent over MCP."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models.job import Job
from . import settings

MCP = "mcp"
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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def status(session: Session) -> CapStatus:
    rows = session.scalars(select(Job).where(Job.source == MCP, Job.created_at >= _today_start())).all()
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
        raise CapExceeded(f"The agent job limit is {st.max_jobs} jobs per day and {st.jobs_today} were already started today.")
    if st.cap_usd <= 0:
        return
    if estimate_usd is None:
        raise CapExceeded("This model has no known price, so it cannot be started under a spend cap. Raise the cap to 0 in Settings to allow it.")
    if st.spent_usd + float(estimate_usd) > st.cap_usd:
        raise CapExceeded(f"This job (about ${estimate_usd:.2f}) would take today's agent spend to ${st.spent_usd + estimate_usd:.2f}, over the ${st.cap_usd:.2f} cap.")


def tag(job: Job, estimate_usd: float | None) -> None:
    job.source = MCP
    job.request_json = dict(job.request_json or {}) | {ESTIMATE_KEY: estimate_usd}
```

- [ ] **Step 7: Enqueue kwargs** — in both `enqueue_image` and `enqueue_video` add `source: str = "web", estimate_usd: float | None = None` to the signature; right after `_require_project(...)` and before the `Job(...)` is built: `if source == automation.MCP: automation.check(s, estimate_usd)`; after `s.add(job)` and before `s.flush()`: `if source == automation.MCP: automation.tag(job, estimate_usd)`. Import `automation` in `generate.py`.

- [ ] **Step 8: Run** `uv run --frozen pytest -q tests/test_automation.py tests/test_migrations.py` → pass; then the full suite, ruff check, ruff format.

- [ ] **Step 9: Commit** — `feat(automation): jobs.source, MCP token stash, daily spend cap`.

---

### Task 2: Tools module and in-memory tests

**Files:**
- Create: `vjhstudio/mcp/__init__.py`, `vjhstudio/mcp/server.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: Task 1's `automation`, `enqueue_*(source=, estimate_usd=)`; `web.routes.generate.estimate_ctx(session, air, width, height, n)`, `web.routes.generate.video_estimate_ctx(session, air, duration, audio, resolution)` (check the exact name at `routes/generate.py:535`), `constraints.accepted_roles/accepts_summary/size_options/nearest_size/duration_spec/needs_first_frame`, `catalog.list_generate_models(session, kind)`, `catalog.get_by_air`, `catalog.family`, `outputs.gallery/get/move`, `projects.list_active/create/totals/slugify`, `assets` list function (grep `def list` in `services/assets.py`), `account.cached_balance`, `costs.today_spend`, `jobs_svc.JobRunner.submit/cancel/snapshot`.
- Produces: `MCPContext` dataclass `(session_factory, paths, runner, env, setting: Callable[[str], Any], base_url: str = "")`; `build_server(ctx: MCPContext) -> MCPServer`; `SERVER_NAME = "vjhstudio"`; tool names exactly as the spec table; every tool returns dicts/lists; `job_summary(session, job, base_url) -> dict` and `output_summary(session, output, paths, base_url) -> dict` helpers reused by Task 3.

- [ ] **Step 1: Failing tests** `tests/test_mcp_tools.py`

```python
import json
import pytest
from mcp.client import Client
from vjhstudio import db
from vjhstudio.mcp.server import MCPContext, build_server
from vjhstudio.models.job import Job
from vjhstudio.services import settings

LTX = "lightricks:ltx@2.3"
FLUX = "runware:101@1"
MINIMAX = "minimax:4@2"  # if not in the curated snapshot, seed a video row with inputs.frameImages.required = True in the test


@pytest.fixture
def ctx(app):
    return MCPContext(
        session_factory=app.state.boot.session_factory, paths=app.state.paths,
        runner=app.state.runner, env=app.state.env, setting=app.state.setting, base_url="http://test",
    )


@pytest.fixture
async def mcp(ctx, client):  # `client` keeps the app lifespan (and the runner) alive
    async with Client(build_server(ctx)) as c:
        yield c


def _data(result):
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


async def test_tools_are_listed(mcp):
    names = {t.name for t in (await mcp.list_tools()).tools}
    assert {"list_models", "model_details", "estimate", "generate_image", "generate_video", "job_status",
            "wait_for_job", "cancel_job", "list_jobs", "list_outputs", "output_details", "move_output",
            "list_projects", "create_project", "list_assets", "account"} <= names


async def test_list_models_shape(mcp):
    rows = _data(await mcp.call_tool("list_models", {"kind": "image"}))
    flux = next(r for r in rows if r["air"] == FLUX)
    assert flux["accepts"] == "seed image" and flux["price_usd"] > 0 and flux["unit"] == "per_image"


async def test_generate_image_runs_to_an_output_with_url_and_path(mcp, fake):
    r = _data(await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "a red fox", "project": "default"}))
    assert r["job_id"] and r["estimate_usd"] > 0 and r["cap"]["jobs_today"] == 1
    done = _data(await mcp.call_tool("wait_for_job", {"job_id": r["job_id"], "timeout_s": 30}))
    assert done["status"] == "succeeded", done
    out = done["outputs"][0]
    assert out["url"].startswith("http://test/files/outputs/default/") and out["path"].endswith(".png")


async def test_generate_video_needing_a_first_frame_is_refused(mcp, app):
    # seed a video model whose harvested inputs require a first frame (see MINIMAX note)
    ...
    res = await mcp.call_tool("generate_video", {"air": MINIMAX, "prompt": "x", "project": "default"})
    assert res.is_error and "first-frame" in res.content[0].text


async def test_cap_refusal_leaves_no_job(mcp, app):
    with db.session_scope(app.state.boot.session_factory) as s:
        settings.set(s, "mcp.daily_cap_usd", 0.001)
    res = await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "x"})
    assert res.is_error and "cap" in res.content[0].text
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(Job).count() == 0


async def test_project_by_name_slug_or_id_and_move(mcp):
    p = _data(await mcp.call_tool("create_project", {"name": "Campaign One"}))
    assert p["slug"] == "campaign-one"
    r = _data(await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "x", "project": "Campaign One"}))
    done = _data(await mcp.call_tool("wait_for_job", {"job_id": r["job_id"], "timeout_s": 30}))
    oid = done["outputs"][0]["id"]
    moved = _data(await mcp.call_tool("move_output", {"output_id": oid, "project": "default"}))
    assert moved["project"] == "default"
    res = await mcp.call_tool("generate_image", {"air": FLUX, "prompt": "x", "project": "nope"})
    assert res.is_error and "list_projects" in res.content[0].text


async def test_account_and_resources(mcp):
    acct = _data(await mcp.call_tool("account", {}))
    assert "cap" in acct and "spent_today_usd" in acct
    uris = {r.uri for r in (await mcp.list_resources()).resources}
    assert "vjhstudio://projects" in {str(u) for u in uris}
```

Note on `_data`: with `structured_output`, the SDK wraps a non-dict return (a list) as `{"result": [...]}`; a dict return is the dict itself. Keep the helper.

- [ ] **Step 2: Run** → fails on import.

- [ ] **Step 3: Implement `vjhstudio/mcp/server.py`**

Skeleton (fill every tool; helpers shown in full):

```python
"""MCP tools over the studio's services. Interface layer: may import web.routes.generate
for the estimate helpers, nothing else from web."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from mcp.server.mcpserver import Context, MCPServer
from .. import db
from ..models.job import Job, JobStatus
from ..models.output import Output
from ..schemas.image import ImageRequest, PromptForm
from ..schemas.video import VideoRequest
from ..services import account, automation, catalog, constraints, costs, generate, outputs, projects
from ..services import assets as assets_svc
from ..web.routes.generate import estimate_ctx, video_estimate_ctx  # confirm the video name

SERVER_NAME = "vjhstudio"
INSTRUCTIONS = (
    "VJHStudio makes images and videos through RunWare. Call list_models first, then estimate, "
    "then generate_image or generate_video; poll with wait_for_job. Jobs cost money: respect the "
    "daily cap returned in every reply and put results in a named project."
)
TERMINAL = {JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.cancelled.value}


@dataclass
class MCPContext:
    session_factory: Any
    paths: Any
    runner: Any
    env: Any
    setting: Callable[[str], Any]
    base_url: str = ""


def _project(session, ref: str | int):
    if isinstance(ref, int) or str(ref).isdigit():
        p = projects.get(session, int(ref))
    else:
        want = str(ref).strip().lower()
        p = next((x for x in projects.list_all(session) if x.slug == want or x.name.lower() == want), None)
    if p is None:
        raise ValueError(f"No project named {ref!r}; call list_projects or create_project.")
    return p


def output_summary(session, o: Output, paths, base_url: str) -> dict:
    return {
        "id": o.id, "kind": o.kind, "project": o.project.slug if o.project else None, "model": o.model_air,
        "created_at": o.created_at.isoformat(), "cost": o.cost, "width": o.width, "height": o.height,
        "duration_s": o.duration_s, "url": f"{base_url}/files/outputs/{o.rel_path}",
        "path": str(outputs.abs_path(paths, o, projects.root_override(session))),
    }


def job_summary(session, job: Job, paths, base_url: str, live: dict | None = None) -> dict:
    outs = session.query(Output).filter(Output.job_id == job.id).order_by(Output.id).all()
    return {
        "id": job.id, "kind": job.kind, "status": job.status, "progress": (live or {}).get("progress", job.progress),
        "status_text": job.status_text, "model": job.model_air, "title": job.title, "error": job.error_message,
        "cost": job.cost, "created_at": job.created_at.isoformat(), "source": job.source,
        "outputs": [output_summary(session, o, paths, base_url) for o in outs],
    }


def build_server(ctx: MCPContext) -> MCPServer:
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    sf, paths = ctx.session_factory, ctx.paths

    @server.tool()
    def list_models(kind: str, sort: str = "price", favourites_only: bool = False) -> list[dict]:
        """List the image or video models the studio can run, with price and accepted inputs."""
        ...  # catalog.list_generate_models(s, kind); rows -> dicts; sort by price desc (unknown last) or name

    @server.tool()
    def model_details(air: str) -> dict: ...
    @server.tool()
    def estimate(kind: str, air: str, width: int | None = None, height: int | None = None, number_results: int = 1,
                 duration: float | None = None, resolution: str = "", audio: bool = False) -> dict: ...

    @server.tool()
    def generate_image(air: str, prompt: str, project: str | int = "default", width: int = 1024, height: int = 1024,
                       number_results: int = 1, negative_prompt: str = "", seed: int | None = None,
                       seed_image_asset_id: int | None = None, reference_asset_ids: list[int] | None = None,
                       title: str | None = None) -> dict:
        """Queue an image job. Refuses when today's agent spend would pass the cap."""
        with db.session_scope(sf) as s:
            p = _project(s, project)
            m = catalog.get_by_air(s, air)
            if m is None or m.kind != "image":
                raise ValueError(f"{air} is not an image model; call list_models.")
            w, h = constraints.nearest_size(m.constraints_json, width, height)
            est = estimate_ctx(s, air, w, h, number_results)["total"]
            default_negative = ctx.setting("defaults.negative_prompt")
        req = ImageRequest(project_id=p.id, model=air, final_prompt=prompt,
                           form=PromptForm(subject=prompt, negative=negative_prompt), width=w, height=h,
                           number_results=number_results, seed=seed, seed_image_asset_id=seed_image_asset_id,
                           reference_asset_ids=reference_asset_ids or [], title=title)
        try:
            job = generate.enqueue_image(sf, paths, req, default_negative=default_negative, source="mcp", estimate_usd=est)
        except automation.CapExceeded as e:
            raise ValueError(str(e)) from e
        ctx.runner.submit(job.id)
        with db.session_scope(sf) as s:
            cap = automation.status(s).__dict__
        note = f"size snapped to {w}x{h}" if (w, h) != (width, height) else ""
        return {"job_id": job.id, "estimate_usd": est, "cap": cap, "note": note}

    @server.tool()
    async def wait_for_job(job_id: str, timeout_s: int = 300, mcp_ctx: Context | None = None) -> dict:
        """Wait until the job finishes (or timeout_s, max 900) and return its outputs."""
        deadline = asyncio.get_running_loop().time() + max(1, min(int(timeout_s), 900))
        while True:
            with db.session_scope(sf) as s:
                job = s.get(Job, job_id)
                if job is None:
                    raise ValueError(f"No job {job_id}.")
                summary = job_summary(s, job, paths, ctx.base_url, ctx.runner.snapshot().get(job_id))
            if job.status in TERMINAL:
                return summary
            if asyncio.get_running_loop().time() >= deadline:
                return summary | {"timed_out": True}
            if mcp_ctx is not None:
                await mcp_ctx.report_progress(summary["progress"], 100)
            await asyncio.sleep(2)
    ...
    @server.resource("vjhstudio://projects", mime_type="application/json")
    def projects_resource() -> str: ...
    @server.resource("vjhstudio://outputs/recent", mime_type="application/json")
    def recent_outputs() -> str: ...
    @server.prompt()
    def plan_shoot(subject: str) -> str:
        return (f"Plan and run a small shoot of {subject!r} in VJHStudio: list_models, pick one under $0.05 per image, "
                "estimate, create_project if needed, generate 3 variants, wait_for_job, and report the URLs and total cost. "
                "Ask before spending more than one dollar.")
    return server
```

How the SDK injects `Context`: a parameter annotated `Context` is filled automatically; confirm the parameter must be typed exactly `Context` (see `mcp.server.mcpserver.Context`), and rename if the SDK requires a specific name. `generate_video` mirrors `generate_image` with `VideoRequest`, `constraints.duration_spec` clamping, `video_estimate_ctx`, and the same `CapExceeded` → `ValueError` conversion; the first-frame and roles pre-flights raise `ValueError` already inside `enqueue_video` and pass through.

- [ ] **Step 4: Run** `tests/test_mcp_tools.py` → pass; full suite, ruff.
- [ ] **Step 5: Commit** — `feat(mcp): tools over the studio services with an in-memory test client`.

---

### Task 3: Transports — HTTP mount with bearer token, stdio command, file access with token

**Files:**
- Create: `vjhstudio/mcp/http.py` (`BearerMiddleware`, `mount_mcp(app, server, token)`)
- Modify: `vjhstudio/web/app.py` (build server, mount at `/mcp` when enabled, run `session_manager.run()` in lifespan)
- Modify: `vjhstudio/web/routes/files.py` (accept bearer token from non-loopback)
- Modify: `vjhstudio/web/routes/system.py` (`/api/health` gains `"mcp": {"enabled": bool}`)
- Modify: `vjhstudio/main.py` (`mcp` subcommand)
- Test: `tests/test_mcp_http.py`, `tests/test_mcp_stdio.py`

**Interfaces:**
- Consumes: Task 2's `build_server`, `MCPContext`; Task 1's `effective_mcp_token`, `mcp.enabled` setting.
- Produces: `app.state.mcp_server` (or `None`); `GET/POST /mcp` behind the token; `vjhstudio mcp` CLI.

- [ ] **Step 1: Failing tests**

`tests/test_mcp_http.py`:

```python
import httpx
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from vjhstudio.web.app import create_app


@pytest.fixture
def mcp_app(paths, fake, download_transport):
    return create_app(paths, client_factory=fake_factory(fake), env={"VJHSTUDIO_OFFLINE": "1", "VJHSTUDIO_MCP_ENABLED": "1", "VJHSTUDIO_MCP_TOKEN": "t0ken"},
                      auto_refresh=False, download_transport=download_transport)  # mirror conftest's create_app call


async def test_mcp_requires_the_bearer_token(mcp_app):
    async with mcp_app.router.lifespan_context(mcp_app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://test") as c:
            r = await c.post("/mcp", json={})
            assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
            r = await c.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401


async def test_mcp_initializes_over_http_with_the_token(mcp_app):
    async with mcp_app.router.lifespan_context(mcp_app):
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://test",
                                 headers={"Authorization": "Bearer t0ken"})
        async with streamable_http_client("http://test/mcp", http_client=http) as transport:
            async with Client(transport) as c:
                names = {t.name for t in (await c.list_tools()).tools}
                assert "generate_image" in names


async def test_files_accept_the_token_from_a_remote_client(mcp_app): ...  # non-loopback client host via ASGITransport(client=("10.0.0.5", 1234)); without token 403, with token 200 for an existing output


async def test_mcp_is_absent_when_disabled(client):
    r = await client.post("/mcp", json={})
    assert r.status_code == 404
```

Check how `Client` accepts a transport in this SDK version (the constructor takes `Transport | str`); if `streamable_http_client` yields streams rather than a `Transport`, use `ClientSession(read, write)` from `mcp.client.session` instead — pick whichever the SDK's own tests show, and note it in the report.

`tests/test_mcp_stdio.py`:

```python
import os, shutil, sys
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def test_stdio_command_serves_tools(paths):
    env = os.environ | {"VJHSTUDIO_DATA_DIR": str(paths.data), "VJHSTUDIO_OFFLINE": "1", "PYTHONUNBUFFERED": "1"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "vjhstudio.main", "mcp"], env=env)
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert "list_projects" in names
        res = await c.call_tool("list_projects", {})
        assert not res.is_error
```

- [ ] **Step 2: Run** → fail.

- [ ] **Step 3: `vjhstudio/mcp/http.py`**

```python
import hmac
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerMiddleware:
    def __init__(self, app: ASGIApp, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            auth = dict(scope.get("headers") or {}).get(b"authorization", b"").decode("latin-1")
            given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not (self.token and hmac.compare_digest(given, self.token)):
                resp = JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="vjhstudio"'})
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def token_ok(request, token: str | None) -> bool:
    """For /files/*: True when the request carries the MCP token."""
    auth = request.headers.get("authorization", "")
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return bool(token) and hmac.compare_digest(given, token or "")
```

`mount_mcp(app, server, token)`: `sub = server.streamable_http_app(streamable_http_path="/", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`; `app.mount("/mcp", BearerMiddleware(sub, token))`. If mounting at `/mcp` with inner path `/` produces `/mcp/` only, mount at `/` with inner path `/mcp` and let the middleware check `scope["path"].startswith("/mcp")` — verify with the 401 test and keep whichever serves `POST /mcp`.

- [ ] **Step 4: `web/app.py`** — after the runner is built: if `setting("mcp.enabled")` and a token exists: build `MCPContext(session_factory, paths, runner, env, setting, base_url="")`, `server = build_server(ctx)`, `app.state.mcp_server = server`, and inside the lifespan wrap the `yield` with `async with server.session_manager.run():` (enter after `runner.start`, leave before `runner.stop`). Because mounting must happen at app construction (not in the lifespan), build the server object at `create_app` time with a context whose `runner` is resolved lazily: give `MCPContext.runner` a property/lambda `lambda: app.state.runner` (adjust Task 2's dataclass to accept a callable and call it where used, and update its tests). Base URL: set `ctx.base_url` per request is not possible in the mount; instead compute it in the tools from `ctx.env`/`app.state.port` as `http://<host-from-setting-or-127.0.0.1>:<port>`; document that a remote host may replace the host part. When `mcp.enabled` is off and no `mcp.enabled` env, no mount and `app.state.mcp_server = None`. If enabled but no token yet, generate one with `secrets.rotate_mcp_token(paths)` at boot and log (stderr) "MCP token created; see Settings" without the value.

- [ ] **Step 5: files.py** — where each route refuses non-loopback (find the check; it may be a shared dependency), allow through when `token_ok(request, secrets.effective_mcp_token(paths, env))`.

- [ ] **Step 6: main.py `mcp` subcommand**

```python
def cmd_mcp(_args: argparse.Namespace) -> int:
    import asyncio
    from . import boot
    from .mcp.server import MCPContext, build_server
    from .services import jobs as jobs_svc, settings as settings_svc
    from .runware.client import open_client  # whatever create_app passes as client_factory by default

    logging.basicConfig(level=log_level(), stream=sys.stderr)
    paths = config.resolve_paths()
    try:
        info = boot.boot(paths)
    except migrate.MigrationFailed as e:
        print(str(e), file=sys.stderr)
        return config.MIGRATION_FAIL_EXIT_CODE

    def setting(key):
        with db.session_scope(info.session_factory) as s:
            return settings_svc.get(s, key, os.environ)

    async def main_async():
        runner = jobs_svc.JobRunner(info.session_factory, paths, client_factory=<default factory>,
                                    api_key_getter=lambda: secrets.effective_api_key(paths, os.environ),
                                    transport_getter=lambda: setting("runware.transport"),
                                    concurrency=setting("jobs.concurrency"))
        await runner.start(requeue=info.requeued_jobs)
        ctx = MCPContext(info.session_factory, paths, lambda: runner, os.environ, setting)
        try:
            await build_server(ctx).run_stdio_async()
        finally:
            await runner.stop()
            info.engine.dispose()
    asyncio.run(main_async())
    return 0
```

Look at `create_app`'s default `client_factory` to reuse the same one. Register: `sub.add_parser("mcp", help="serve the MCP tools over stdio (for Goose, Claude Desktop …)").set_defaults(func=cmd_mcp)`. Confirm nothing in the boot path prints to stdout (grep `print(` in `boot.py`, `migrate.py`); route any such print to stderr when `sys.stdout` is the protocol channel (simplest: in `cmd_mcp`, `sys.stdout = sys.stderr` is NOT acceptable because the SDK needs the real stdout — instead pass through and fix the offending prints to use `file=sys.stderr`).

- [ ] **Step 7: health** — add `"mcp": {"enabled": request.app.state.mcp_server is not None}` to `/api/health`.
- [ ] **Step 8: Run** both test files, then the full suite, ruff.
- [ ] **Step 9: Commit** — `feat(mcp): HTTP transport at /mcp behind a bearer token, and a stdio command`.

---

### Task 4: Settings card, docs, recipe, "via agent" chip, version

**Files:**
- Modify: `vjhstudio/web/routes/settings.py` (`_automation_ctx`, `POST /settings/automation`, `POST /settings/automation/token`)
- Create: `vjhstudio/web/templates/settings/_automation.html`
- Modify: `vjhstudio/web/templates/pages/settings.html` (include the card)
- Modify: queue card / queue page / gallery detail templates (chip when `job.source == 'mcp'`)
- Modify: `README.md` (new section after "Backups"), `CHANGELOG.md`, `vjhstudio/__init__.py` (`0.5.0`)
- Create: `docs/goose/vjhstudio-recipe.yaml`
- Test: `tests/test_web_settings.py` (or the existing settings test module), `tests/test_web_jobs.py`/queue tests for the chip

**Interfaces:**
- Consumes: Task 1's settings keys and token functions; `Job.source`.
- Produces: routes `POST /settings/automation` (fields `mcp_enabled`, `mcp_daily_cap_usd`, `mcp_max_jobs_per_day`) and `POST /settings/automation/token` (regenerates, re-renders the card with the new token revealed once).

- [ ] **Step 1: Failing tests**

```python
async def test_settings_shows_the_automation_card_with_snippets(client):
    r = await client.get("/settings")
    assert 'id="automation"' in r.text and "streamable_http" in r.text and "claude mcp add" in r.text
    assert "vjhstudio mcp" in r.text  # stdio block


async def test_saving_automation_settings(client, app):
    r = await client.post("/settings/automation", data={"mcp_enabled": "on", "mcp_daily_cap_usd": "3.5", "mcp_max_jobs_per_day": "7"})
    assert r.status_code == 200 and "restart" in r.text.lower()
    with db.session_scope(app.state.boot.session_factory) as s:
        assert settings.get(s, "mcp.enabled") is True and settings.get(s, "mcp.daily_cap_usd") == 3.5


async def test_regenerating_the_token_reveals_it_once_and_never_logs_it(client, app, caplog):
    r = await client.post("/settings/automation/token")
    tok = secrets.read_mcp_token(app.state.paths)
    assert tok and tok in r.text
    assert tok not in caplog.text
    r = await client.get("/settings")
    assert tok not in r.text and "••••" in r.text


async def test_queue_shows_a_via_agent_chip(client, app):
    # create a job with source="mcp" through generate.enqueue_image(..., source="mcp", estimate_usd=0.01)
    r = await client.get("/queue")
    assert "via agent" in r.text
```

- [ ] **Step 2: Run** → fail.

- [ ] **Step 3: Card template** `_automation.html`: `<article id="automation">` with a form posting to `/settings/automation` (`hx-post`, `hx-target="#automation"`, `hx-swap="outerHTML"`), the three fields, the token row (masked `••••` + `Reveal` button that toggles a `<code>` via Alpine, `Copy` via `navigator.clipboard`, `Regenerate` posting to `/settings/automation/token`), the URL line, and three `<pre>` blocks:

Goose:
```yaml
extensions:
  vjhstudio:
    type: streamable_http
    name: VJHStudio
    enabled: true
    uri: "http://{{ host }}:{{ port }}/mcp"
    headers: { "Authorization": "Bearer {{ token }}" }
    timeout: 900
```
Claude Code: `claude mcp add --transport http vjhstudio http://{{ host }}:{{ port }}/mcp --header "Authorization: Bearer {{ token }}"`
stdio (Goose or Claude Desktop): `cmd: {{ venv_bin }}/vjhstudio`, `args: [mcp]`, plus the Windows line `{{ venv_bin_win }}\vjhstudio.exe`.

`{{ host }}` = the LAN address the app is bound to if known (`VJHSTUDIO_HOST` env, else "127.0.0.1"); `{{ venv_bin }}` from `sys.executable`'s directory. The token appears in the blocks only when revealed (`x-show`), masked otherwise, so a screenshot of the page does not leak it. A note under the form: "Changes to Enabled and the token take effect after a restart." with the existing Restart control linked.

- [ ] **Step 4: Routes** in `settings.py` following `save_settings` / `save_api_key` patterns; boolean parsing of `mcp_enabled` (checkbox present = on); validation errors re-render the card with `aria-invalid`.

- [ ] **Step 5: Chip** — in the queue card, queue history row and gallery detail: `{% if job.source == 'mcp' %}<small class="chip">via agent</small>{% endif %}` (gallery detail via `output.job`).

- [ ] **Step 6: README** section "Use VJHStudio from an agent (MCP)" between "Backups" and "Uninstall": what it does in two sentences, the spend cap, enabling it in Settings, the Goose block, the Claude Code line, the stdio alternative, "treat the token as a password", "works from another computer on your network at http://<your-pc-ip>:8080/mcp". `docs/goose/vjhstudio-recipe.yaml` with `title`, `description`, `instructions` (the `plan_shoot` text), `extensions` block with placeholders. CHANGELOG 0.5.0 entry; bump version.

- [ ] **Step 7: Run** tests, full suite, ruff. **Step 8: Commit** — `feat(settings): Automation card, agent chip, README and Goose recipe; 0.5.0`.

---

## Self-review

- Spec §1 → Task 1; §2 → Task 1; §3 → Task 2; §4 → Task 3; §5 → Task 4; §6 tests spread across tasks; §7 → Task 4.
- Names used consistently: `MCPContext`, `build_server`, `automation.check/tag/status/CapExceeded`, `effective_mcp_token`, `job_summary`, `output_summary`, tool names as the spec table.
- Known open point for implementers: the exact way the SDK v2 `Client` takes a streamable HTTP transport (Task 3) and how `Context` is injected into a tool (Task 2); both are to be confirmed against the installed SDK and reported.
