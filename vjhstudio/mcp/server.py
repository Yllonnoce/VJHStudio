"""MCP tools over the studio's services.

An interface layer, like ``web/``: it may import the estimate helpers from
``web.routes.generate`` and nothing else from ``web``. Every tool returns
JSON-serialisable dicts or lists and refuses with a single user-facing sentence.
A refusal is a ``ValueError`` (``automation.CapExceeded`` is one) converted to the
SDK's ``ToolError`` so the sentence reaches the model instead of being swallowed
behind a bare "Error executing tool ...".
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .. import db
from ..models import CatalogModel, Job, JobStatus, Output, Project, utcnow
from ..schemas.image import ImageRequest, PromptForm
from ..schemas.video import DURATION_MAX, DURATION_MIN, VideoRequest
from ..services import account as account_svc
from ..services import assets as assets_svc
from ..services import automation, catalog, constraints, costs, generate, outputs, projects
from ..web.routes.generate import (
    AUDIO_KEYS,
    DEFAULT_DURATION,
    FALLBACK_RESOLUTIONS,
    SIZE_PRESETS,
    estimate_ctx,
    video_estimate_ctx,
    with_portrait,
)

SERVER_NAME = "vjhstudio"
INSTRUCTIONS = (
    "VJHStudio makes images and videos through RunWare. Call list_models first, then estimate, "
    "then generate_image or generate_video; poll with wait_for_job. Jobs cost money: respect the "
    "daily cap returned in every reply and put results in a named project."
)
TERMINAL = (JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.cancelled.value)
KINDS = ("image", "video")
TIMEOUT_MAX = 900
# The poll backs off to two seconds; the first few passes are quicker so a job that
# finishes straight away is reported straight away.
POLL_FIRST = 0.1
POLL_MAX = 2.0
RECENT_OUTPUTS = 24


@dataclass
class MCPContext:
    """Everything the tools need from the app. ``runner`` is a zero-argument callable
    because the server is built at app construction, before the runner exists."""

    session_factory: Any
    paths: Any
    runner: Callable[[], Any]
    env: Any
    setting: Callable[[str], Any]
    base_url: str = ""


def _guard(fn):
    """Turn a refusal into a ``ToolError`` so the sentence survives the round trip."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except ValueError as e:
                raise ToolError(str(e)) from e

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise ToolError(str(e)) from e

    return wrapper


# ---- lookups -------------------------------------------------------------
def _kind(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        raise ValueError(f"kind must be 'image' or 'video', not {kind!r}.")
    return k


def _model(session, air: str, kind: str | None = None) -> CatalogModel:
    m = catalog.get_by_air(session, air)
    if m is None:
        raise ValueError(f"No model {air!r} in the catalog; call list_models.")
    if kind is not None and m.kind != kind:
        article = "an" if kind[0] in "aeiou" else "a"
        raise ValueError(f"{air} is not {article} {kind} model; call list_models.")
    return m


def _project(session, ref: str | int) -> Project:
    if isinstance(ref, int) or str(ref).strip().isdigit():
        p = projects.get(session, int(ref))
    else:
        want = str(ref).strip().lower()
        p = next(
            (x for x in projects.list_all(session) if x.slug == want or x.name.lower() == want),
            None,
        )
    if p is None:
        raise ValueError(f"No project named {ref!r}; call list_projects or create_project.")
    return p


def _roles(m: CatalogModel) -> dict[str, dict]:
    return constraints.accepted_roles(
        m.kind, m.capabilities_json or [], m.constraints_json, catalog.family(m)
    )


def _dims_block(m: CatalogModel) -> dict:
    block = (m.constraints_json or {}).get("dims")
    return block if isinstance(block, dict) else {}


def _video_tiers(m: CatalogModel) -> dict:
    tiers = (m.price_tiers_json or {}).get("video")
    return dict(tiers) if isinstance(tiers, dict) else {}


def _resolutions(m: CatalogModel) -> list[str]:
    tiers = _video_tiers(m)
    named = [str(r) for r in (tiers.get("resolutions") or [])]
    return with_portrait(named or list(FALLBACK_RESOLUTIONS))


def _duration_for(m: CatalogModel, duration: float | None) -> float:
    """The duration this model will actually take: its own list or range wins, and the
    schema's 1--30 s bounds are the last word."""
    spec = constraints.duration_spec(m.constraints_json)
    default = spec.get("default")
    value = float(duration) if duration is not None else float(default or DEFAULT_DURATION)
    choices = [float(v) for v in (spec.get("values") or []) if isinstance(v, (int, float))]
    if choices:
        value = min(choices, key=lambda v: abs(v - value))
    else:
        if spec.get("min") is not None:
            value = max(float(spec["min"]), value)
        if spec.get("max") is not None:
            value = min(float(spec["max"]), value)
    return max(DURATION_MIN, min(DURATION_MAX, value))


def _audio_on(provider_settings: dict) -> bool:
    return any(bool(provider_settings.get(key)) for key in AUDIO_KEYS)


def _snap_note(asked: tuple[int, int], used: tuple[int, int]) -> str:
    return "" if asked == used else f"size snapped to {used[0]}x{used[1]}"


def _cap(session) -> dict:
    st = automation.status(session)
    return asdict(st) | {"remaining_usd": st.remaining_usd}


# ---- summaries reused by the transports ----------------------------------
def model_summary(m: CatalogModel) -> dict:
    caps = m.capabilities_json or []
    return {
        "air": m.air,
        "name": m.name,
        "kind": m.kind,
        "price_label": catalog.label(m),
        "price_usd": m.price_primary,
        "unit": m.price_unit,
        "accepts": constraints.accepts_summary(_roles(m)),
        "needs_first_frame": (
            constraints.needs_first_frame(caps, m.constraints_json) if m.kind == "video" else False
        ),
        "sizes_known": bool(_dims_block(m).get("mode")),
        "favourite": bool(m.is_favourite),
    }


def output_summary(session, o: Output, paths, base_url: str) -> dict:
    project = session.get(Project, o.project_id)
    try:
        path = str(outputs.abs_path(paths, o, projects.root_override(session)))
    except LookupError:  # a row pointing outside the outputs root has no servable file
        path = ""
    return {
        "id": o.id,
        "kind": o.kind,
        "project": project.slug if project else None,
        "model": o.model_air,
        "created_at": o.created_at.isoformat(),
        "cost": o.cost,
        "width": o.width,
        "height": o.height,
        "duration_s": o.duration_s,
        "is_favourite": bool(o.is_favourite),
        "is_missing": bool(o.is_missing),
        "url": f"{base_url}/files/outputs/{o.rel_path}",
        "path": path,
    }


def job_summary(session, job: Job, paths, base_url: str, live: dict | None = None) -> dict:
    rows = session.query(Output).filter(Output.job_id == job.id).order_by(Output.id).all()
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": int((live or {}).get("progress", job.progress) or 0),
        "status_text": job.status_text,
        "model": job.model_air,
        "title": job.title,
        "error": job.error_message,
        "cost": job.cost,
        "created_at": job.created_at.isoformat(),
        "source": job.source,
        "outputs": [output_summary(session, o, paths, base_url) for o in rows],
    }


# Every tool is a closure over ``ctx``, so the server is one object with no globals.
def build_server(ctx: MCPContext) -> MCPServer:
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    sf, paths = ctx.session_factory, ctx.paths

    def runner():
        r = ctx.runner()
        if r is None:
            raise ValueError("The job runner is not running; start VJHStudio first.")
        return r

    def live_for(job_id: str) -> dict:
        r = ctx.runner()
        return (r.snapshot() if r is not None else {}).get(job_id) or {}

    # ---- catalog ---------------------------------------------------------
    @server.tool()
    @_guard
    def list_models(kind: str, sort: str = "price", favourites_only: bool = False) -> list[dict]:
        """List the image or video models the studio can run, dearest first, with price
        and accepted inputs. Only models this studio can actually drive are listed."""
        k = _kind(kind)
        order = sort if sort in catalog.SORTS else "price"
        with db.session_scope(sf) as s:
            rows = [
                m
                for m in catalog.list_models(s, k, sort=order)
                if constraints.is_generate_capable(k, m.capabilities_json or [], m.constraints_json)
                and (not favourites_only or m.is_favourite)
            ]
            return [model_summary(m) for m in rows]

    @server.tool()
    @_guard
    def model_details(air: str) -> dict:
        """Everything one model accepts: reference roles, sizes, durations, resolutions
        and its provider settings schema."""
        with db.session_scope(sf) as s:
            m = _model(s, air)
            c = m.constraints_json
            fallback = list(SIZE_PRESETS) if m.kind == "image" else []
            dims = _dims_block(m)
            return model_summary(m) | {
                "family": catalog.family(m),
                "capabilities": list(m.capabilities_json or []),
                "roles": _roles(m),
                "sizes": constraints.size_options(c, m.kind, fallback),
                "size_rule": dims if dims.get("mode") == "rule" else None,
                "durations": constraints.duration_spec(c),
                "resolutions": _resolutions(m) if m.kind == "video" else [],
                "provider_settings": list(m.provider_settings_schema or []),
                "defaults": {"width": m.default_width, "height": m.default_height},
            }

    @server.tool()
    @_guard
    def estimate(
        kind: str,
        air: str,
        width: int | None = None,
        height: int | None = None,
        number_results: int = 1,
        duration: float | None = None,
        resolution: str = "",
        audio: bool = False,
    ) -> dict:
        """What one run would cost, in US dollars. ``estimate_usd`` is null when the
        model has no known price -- those cannot run under a spend cap."""
        k = _kind(kind)
        with db.session_scope(sf) as s:
            m = _model(s, air, k)
            if k == "image":
                asked = (
                    int(width or m.default_width or 1024),
                    int(height or m.default_height or 1024),
                )
                used = constraints.nearest_size(m.constraints_json, *asked)
                data = estimate_ctx(s, air, used[0], used[1], number_results)
                return {
                    "estimate_usd": data["total"],
                    "rate": m.price_primary,
                    "note": _snap_note(asked, used),
                    "width": used[0],
                    "height": used[1],
                    "number_results": data["number_results"],
                }
            seconds = _duration_for(m, duration)
            data = video_estimate_ctx(s, air, seconds, audio, resolution)
            note = (
                ""
                if duration is None or seconds == float(duration)
                else (f"duration snapped to {seconds:g}s")
            )
            return {
                "estimate_usd": data["total"],
                "rate": data["rate"],
                "note": note,
                "duration": seconds,
                "resolution": resolution,
                "audio": audio,
            }

    # ---- generation ------------------------------------------------------
    @server.tool()
    @_guard
    def generate_image(
        air: str,
        prompt: str,
        project: str | int = "default",
        width: int = 1024,
        height: int = 1024,
        number_results: int = 1,
        negative_prompt: str = "",
        seed: int | None = None,
        seed_image_asset_id: int | None = None,
        reference_asset_ids: list[int] | None = None,
        title: str | None = None,
    ) -> dict:
        """Queue an image job. Refuses, before anything is queued or billed, when
        today's agent spend would pass the cap."""
        with db.session_scope(sf) as s:
            p = _project(s, project)
            m = _model(s, air, "image")
            asked = (int(width), int(height))
            used = constraints.nearest_size(m.constraints_json, *asked)
            est = estimate_ctx(s, air, used[0], used[1], number_results)["total"]
            project_id = p.id
        req = ImageRequest(
            project_id=project_id,
            model=air,
            final_prompt=prompt,
            # the agent writes its own prompt: no composed fields, and no "no text"
            # boilerplate appended to it -- only the default negative still applies
            form=PromptForm(subject=prompt, negative=negative_prompt, no_text=False),
            width=used[0],
            height=used[1],
            number_results=number_results,
            seed=seed,
            seed_image_asset_id=seed_image_asset_id,
            reference_asset_ids=list(reference_asset_ids or []),
            title=title,
        )
        job = generate.enqueue_image(
            sf,
            paths,
            req,
            default_negative=ctx.setting("defaults.negative_prompt"),
            source=automation.MCP,
            estimate_usd=est,
        )
        runner().submit(job.id)
        with db.session_scope(sf) as s:
            cap = _cap(s)
        return {
            "job_id": job.id,
            "estimate_usd": est,
            "cap": cap,
            "note": _snap_note(asked, used),
            "width": used[0],
            "height": used[1],
        }

    @server.tool()
    @_guard
    def generate_video(
        air: str,
        prompt: str,
        project: str | int = "default",
        duration: float = DEFAULT_DURATION,
        resolution: str = "720p",
        width: int | None = None,
        height: int | None = None,
        first_frame_asset_id: int | None = None,
        last_frame_asset_id: int | None = None,
        reference_asset_ids: list[int] | None = None,
        provider_settings: dict | None = None,
        title: str | None = None,
    ) -> dict:
        """Queue a video job. Same cap, and the same refusal-before-billing rule."""
        settings_sent = dict(provider_settings or {})
        with db.session_scope(sf) as s:
            p = _project(s, project)
            m = _model(s, air, "video")
            seconds = _duration_for(m, duration)
            size: tuple[int, int] | None = None
            if width is not None and height is not None:
                size = constraints.nearest_size(m.constraints_json, int(width), int(height))
            est = video_estimate_ctx(s, air, seconds, _audio_on(settings_sent), resolution)["total"]
            project_id = p.id
        req = VideoRequest(
            project_id=project_id,
            model=air,
            final_prompt=prompt,
            form=PromptForm(subject=prompt, no_text=False),
            duration=seconds,
            resolution=resolution,
            width=size[0] if size else None,
            height=size[1] if size else None,
            first_frame_asset_id=first_frame_asset_id,
            last_frame_asset_id=last_frame_asset_id,
            reference_asset_ids=list(reference_asset_ids or []),
            provider_settings=settings_sent,
            title=title,
        )
        job = generate.enqueue_video(sf, paths, req, source=automation.MCP, estimate_usd=est)
        runner().submit(job.id)
        with db.session_scope(sf) as s:
            cap = _cap(s)
        notes = []
        if seconds != float(duration):
            notes.append(f"duration snapped to {seconds:g}s")
        if size is not None and size != (int(width), int(height)):
            notes.append(f"size snapped to {size[0]}x{size[1]}")
        return {
            "job_id": job.id,
            "estimate_usd": est,
            "cap": cap,
            "note": "; ".join(notes),
            "duration": seconds,
            "resolution": resolution,
        }

    # ---- queue -----------------------------------------------------------
    @server.tool()
    @_guard
    def job_status(job_id: str) -> dict:
        """One job with its outputs, progress and error, if any."""
        with db.session_scope(sf) as s:
            job = s.get(Job, job_id)
            if job is None:
                raise ValueError(f"No job {job_id!r}; call list_jobs.")
            return job_summary(s, job, paths, ctx.base_url, live_for(job_id))

    @server.tool()
    @_guard
    async def wait_for_job(job_id: str, timeout_s: int = 300, mcp_ctx: Context = None) -> dict:
        """Wait until the job finishes (or ``timeout_s``, at most 900) and return its
        outputs. A reply carrying ``timed_out`` means the job is still running."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1, min(int(timeout_s), TIMEOUT_MAX))
        delay = POLL_FIRST
        while True:
            with db.session_scope(sf) as s:
                job = s.get(Job, job_id)
                if job is None:
                    raise ValueError(f"No job {job_id!r}; call list_jobs.")
                status = job.status
                summary = job_summary(s, job, paths, ctx.base_url, live_for(job_id))
            if status in TERMINAL:
                return summary
            if loop.time() >= deadline:
                return summary | {"timed_out": True}
            if mcp_ctx is not None:
                await mcp_ctx.report_progress(summary["progress"], 100)
            await asyncio.sleep(delay)
            delay = min(delay * 2, POLL_MAX)

    @server.tool()
    @_guard
    def cancel_job(job_id: str) -> dict:
        """Ask the queue to drop or abort a job. False means it had already finished."""
        with db.session_scope(sf) as s:
            if s.get(Job, job_id) is None:
                raise ValueError(f"No job {job_id!r}; call list_jobs.")
        return {"cancelled": bool(runner().cancel(job_id))}

    @server.tool()
    @_guard
    def list_jobs(status: str = "", limit: int = 20) -> list[dict]:
        """Recent jobs, newest first, optionally filtered by status."""
        n = max(1, min(int(limit), 100))
        with db.session_scope(sf) as s:
            q = s.query(Job)
            if status:
                q = q.filter(Job.status == status)
            rows = q.order_by(Job.created_at.desc(), Job.id.desc()).limit(n).all()
            return [job_summary(s, j, paths, ctx.base_url, live_for(j.id)) for j in rows]

    # ---- gallery ---------------------------------------------------------
    @server.tool()
    @_guard
    def list_outputs(
        project: str | int = "", kind: str = "", search: str = "", limit: int = 24
    ) -> list[dict]:
        """Finished files, newest first, with a URL and an absolute path for each."""
        n = max(1, min(int(limit), 100))
        with db.session_scope(sf) as s:
            project_id = _project(s, project).id if str(project).strip() else None
            rows, _ = outputs.gallery(
                s,
                project_id=project_id,
                kind=kind or None,
                q=search or None,
                per_page=n,
            )
            return [output_summary(s, o, paths, ctx.base_url) for o in rows]

    @server.tool()
    @_guard
    def output_details(output_id: int) -> dict:
        """One output with the prompt, the negative, the parameters actually sent, and
        where the file is."""
        with db.session_scope(sf) as s:
            o = outputs.get(s, int(output_id))
            if o is None:
                raise ValueError(f"No output {output_id}; call list_outputs.")
            return output_summary(s, o, paths, ctx.base_url) | {
                "prompt": o.prompt_text,
                "negative": o.negative_prompt,
                "params": dict(o.params_json or {}),
                "seed": o.seed,
                "job_id": o.job_id,
                "file_size": o.file_size,
            }

    @server.tool()
    @_guard
    def move_output(output_id: int, project: str | int) -> dict:
        """File an output under another project; the media file moves with the row."""
        with db.session_scope(sf) as s:
            target = _project(s, project)
            try:
                o = outputs.move(s, paths, int(output_id), target.id)
            except LookupError as e:
                raise ValueError(f"No output {output_id}; call list_outputs.") from e
            except outputs.OutputMoveError as e:
                raise ValueError(str(e)) from e
            return {"id": o.id, "project": target.slug}

    # ---- projects and assets ---------------------------------------------
    @server.tool()
    @_guard
    def list_projects() -> list[dict]:
        """The projects outputs can be filed under, with their totals."""
        with db.session_scope(sf) as s:
            totals = costs.totals_by_project(s)
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "slug": p.slug,
                    "description": p.description,
                    **totals.get(p.id, {"outputs": 0, "cost": 0.0}),
                }
                for p in projects.list_active(s)
            ]

    @server.tool()
    @_guard
    def create_project(name: str, description: str = "") -> dict:
        """Make a project. The slug is derived from the name and is stable afterwards."""
        if not str(name).strip():
            raise ValueError("A project needs a name.")
        with db.session_scope(sf) as s:
            p = projects.create(s, paths, name, description or None)
            return {"id": p.id, "name": p.name, "slug": p.slug}

    @server.tool()
    @_guard
    def list_assets(kind: str = "", search: str = "", limit: int = 50) -> list[dict]:
        """Uploaded images and videos, by id, for the seed/first-frame/reference slots."""
        n = max(1, min(int(limit), 200))
        with db.session_scope(sf) as s:
            rows, _ = assets_svc.list_assets(s, kind=kind or None, q=search or None, per_page=n)
            return [
                {
                    "id": a.id,
                    "name": a.original_name,
                    "kind": a.kind,
                    "width": a.width,
                    "height": a.height,
                    "tags": assets_svc.tags_list(a.tags),
                }
                for a in rows
            ]

    @server.tool()
    @_guard
    def account() -> dict:
        """The cached RunWare balance, today's spend and the agent's cap."""
        with db.session_scope(sf) as s:
            info = account_svc.cached_balance(s)
            return {
                "balance_usd": info.amount if info else None,
                "balance_age_s": ((utcnow() - info.fetched_at).total_seconds() if info else None),
                "currency": info.currency if info else "USD",
                "spent_today_usd": costs.today_spend(s),
                "cap": _cap(s),
            }

    # ---- resources and prompts -------------------------------------------
    @server.resource("vjhstudio://projects", mime_type="application/json")
    def projects_resource() -> str:
        return json.dumps(list_projects())

    @server.resource("vjhstudio://outputs/recent", mime_type="application/json")
    def recent_outputs() -> str:
        return json.dumps(list_outputs(limit=RECENT_OUTPUTS))

    @server.prompt()
    def plan_shoot(subject: str) -> str:
        """Plan and run a small shoot inside VJHStudio."""
        return (
            f"Plan and run a small shoot of {subject!r} in VJHStudio: list_models, pick one under "
            "$0.05 per image, estimate, create_project if needed, generate 3 variants, "
            "wait_for_job, and report the URLs and total cost. "
            "Ask before spending more than one dollar."
        )

    return server


__all__ = [
    "SERVER_NAME",
    "MCPContext",
    "build_server",
    "job_summary",
    "model_summary",
    "output_summary",
]
