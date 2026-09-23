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
from ..runware.tasks import nearest, resolution_wh, split_orientation
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
    _tier_name,
    estimate_ctx,
    video_estimate_ctx,
    with_portrait,
)

SERVER_NAME = "vjhstudio"
INSTRUCTIONS = (
    "VJHStudio makes images and videos through RunWare. Call list_models first, then estimate, "
    "then generate_image or generate_video; poll with wait_for_job. Jobs cost money: respect the "
    "daily cap returned in every reply and put results in a named project. Every argument "
    "except the prompt has a sensible default: leave out (or send null for) anything you do "
    "not know, and the studio's own defaults are used."
)
TERMINAL = (JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.cancelled.value)
KINDS = ("image", "video")
TIMEOUT_MAX = 900
# The poll backs off to two seconds; the first few passes are quicker so a job that
# finishes straight away is reported straight away.
POLL_FIRST = 0.1
POLL_MAX = 2.0
RECENT_OUTPUTS = 24
# ``ImageRequest`` takes nothing else, whatever a model's own size list says.
IMAGE_SIZE_STEP, IMAGE_SIZE_MIN, IMAGE_SIZE_MAX = 64, 128, 2048
SIZES_SUGGESTED = 8
SIZED_MODES = ("list", "rule")


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
def _kind(kind: str | None) -> str:
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        raise ValueError(f"kind must be 'image' or 'video', not {kind!r}.")
    return k


def _text(value: object) -> str:
    """A string argument as the agent meant it: ``None`` and ``"null"`` are empty."""
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("null", "none") else text


def _num(value: object, default: float) -> float:
    if value is None or _text(value) == "":
        return default
    return float(value)


def _default_air(ctx: MCPContext, kind: str) -> str:
    """The model the Generate page would start with, for agents that name none."""
    key = "defaults.video_model" if kind == "video" else "defaults.image_model"
    air = _text(ctx.setting(key))
    if not air:
        raise ValueError(f"No default {kind} model is set; name a model (call list_models).")
    return air


def _latest_job(session) -> Job:
    """The newest job an agent started, for the job tools called with no id."""
    job = (
        session.query(Job)
        .filter(Job.source == automation.MCP)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .first()
    )
    if job is None:
        raise ValueError("No agent job yet; pass a job_id or call generate_image first.")
    return job


def _job_ref(session, job_id: str | None) -> Job:
    ref = _text(job_id)
    if not ref:
        return _latest_job(session)
    job = session.get(Job, ref)
    if job is None:
        raise ValueError(f"No job {ref!r}; call list_jobs.")
    return job


def _output_ref(session, output_id: int | str | None) -> Output:
    ref = _text(output_id)
    if not ref:
        o = session.query(Output).order_by(Output.created_at.desc(), Output.id.desc()).first()
        if o is None:
            raise ValueError("No outputs yet; call generate_image first.")
        return o
    o = outputs.get(session, int(ref))
    if o is None:
        raise ValueError(f"No output {ref}; call list_outputs.")
    return o


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


def _size_mode(m: CatalogModel) -> str:
    """``list``, ``rule`` or ``unknown``. A harvested row carries ``{"mode": "unknown"}``
    by default, which says the opposite of "we know its sizes"."""
    mode = str(_dims_block(m).get("mode") or "unknown")
    return mode if mode in SIZED_MODES else "unknown"


def _valid_image_size(w: int, h: int) -> bool:
    return all(v % IMAGE_SIZE_STEP == 0 and IMAGE_SIZE_MIN <= v <= IMAGE_SIZE_MAX for v in (w, h))


def _require_image_size(m: CatalogModel, size: tuple[int, int]) -> tuple[int, int]:
    """A ``list`` model can offer sizes ``ImageRequest`` will not take (google:4@2 lists
    1376x768 and 6336x2688). Refuse with the sizes that *do* work rather than let pydantic
    answer with a number the agent never sent."""
    if _valid_image_size(*size):
        return size
    usable = [
        f"{o['w']}x{o['h']}"
        for o in constraints.size_options(m.constraints_json, "image", list(SIZE_PRESETS))
        if _valid_image_size(o["w"], o["h"])
    ][:SIZES_SUGGESTED]
    tail = f" Try one of: {', '.join(usable)}." if usable else ""
    raise ValueError(
        f"{m.air} cannot be run at {size[0]}x{size[1]}: sizes must be a multiple of "
        f"{IMAGE_SIZE_STEP} between {IMAGE_SIZE_MIN} and {IMAGE_SIZE_MAX}.{tail}"
    )


def _video_tiers(m: CatalogModel) -> dict:
    tiers = (m.price_tiers_json or {}).get("video")
    return dict(tiers) if isinstance(tiers, dict) else {}


def _resolutions(m: CatalogModel) -> list[str]:
    tiers = _video_tiers(m)
    named = [str(r) for r in (tiers.get("resolutions") or [])]
    return with_portrait(named or list(FALLBACK_RESOLUTIONS))


def _default_resolution(m: CatalogModel) -> str:
    """The tier the Generate page opens on for this model: its first named resolution,
    else 720p. An agent that names no tier gets the model's own, not a global guess --
    a 4K-only model has no 720p to run at."""
    return _resolutions(m)[0]


def _duration_for(m: CatalogModel, duration: float | None) -> float:
    """The duration this model will actually run for, so the price quoted is the price
    billed: the harvested spec first, then the curated ``tiers.video.durations`` that
    ``build_video_task`` snaps to, then the schema's 1--30 s bounds."""
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
    offered = nearest(value, _video_tiers(m).get("durations") or [])
    if offered is not None:
        value = float(offered)
    return max(DURATION_MIN, min(DURATION_MAX, value))


def _provider_settings(m: CatalogModel, sent: dict | None) -> dict:
    """What the job will really carry. The form never omits a schema-declared boolean
    (``parse_provider_settings`` fills it from its default), and neither may a tool: Veo's
    ``generateAudio`` defaults to true and doubles the rate, so an estimate taken from the
    agent's arguments alone would be half the bill."""
    out = {
        str(e["key"]): bool(e.get("default"))
        for e in (m.provider_settings_schema or [])
        if e.get("key") and str(e.get("type") or "").lower() == "bool"
    }
    out.update(dict(sent or {}))
    return out


def _video_size(
    m: CatalogModel, width: int | None, height: int | None, resolution: str = ""
) -> tuple[int, int] | None:
    """Pixels are posted only when the model advertised sizes, exactly as the video panel
    does; otherwise the resolution preset decides and raw pixels would be a guess.

    A ``list`` model always gets one of its own sizes: the panel opens on a listed size
    and never posts anything else, so an agent that sends none is given the listed size
    nearest the preset it asked for rather than the preset's own nominal pixels, which
    that model may never have offered."""
    mode = _size_mode(m)
    if mode == "unknown":
        return None
    if width is None or height is None:
        if mode != "list":
            return None
        return constraints.nearest_size(
            m.constraints_json, *resolution_wh(resolution, _video_tiers(m))
        )
    return constraints.nearest_size(m.constraints_json, int(width), int(height))


def _preset_tier(m: CatalogModel, size: tuple[int, int]) -> str:
    """The resolution name whose own pixels are the ones being sent, or ``""``.

    ``rule`` mode carries no per-size labels and ``_tier_name`` reads the shorter side,
    which calls LTX's 1080p (1920x1088, its curated ``video.dims``) "4K" -- a tier it has
    no rate for. The presets say plainly which tier those pixels are. The last match
    wins: the names are listed cheapest first, so a tie is priced at the dearer one."""
    tiers = _video_tiers(m)
    found = ""
    for name in _resolutions(m):
        w, h = resolution_wh(name, tiers)
        if constraints.nearest_size(m.constraints_json, w, h) == size:
            found = split_orientation(name)[0]  # a portrait twin bills at its tier's rate
    return found


def _video_tier(m: CatalogModel, size: tuple[int, int] | None, resolution: str) -> str:
    """The tier the size will really be billed under. ``build_video_task`` lets the pixels
    win over the preset name in *every* dims mode, so the price has to follow the pixels:
    through the size's own label in ``list`` mode (the panel posts it as the hidden
    ``resolution`` -- see ``_default_resolution``), through the curated preset the size
    was snapped from in ``rule`` mode, and only then by the shorter side. With no size at
    all the preset name is what the task builder will use, so it is what is priced."""
    if size is None:
        return resolution
    label = (_dims_block(m).get("labels") or {}).get(f"{size[0]}x{size[1]}", "")
    if label:
        return _tier_name(size[0], size[1], label)
    return _preset_tier(m, size) or _tier_name(size[0], size[1], "")


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
        "sizes_known": _size_mode(m) in SIZED_MODES,
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
    def list_models(
        kind: str | None = None, sort: str | None = "price", favourites_only: bool | None = False
    ) -> list[dict]:
        """List the models the studio can run, dearest first, with price and accepted
        inputs. ``kind`` is "image" or "video"; leave it out for both. Only models this
        studio can actually drive are listed."""
        wanted = _text(kind).lower()
        kinds = KINDS if wanted in ("", "all", "both") else (_kind(wanted),)
        order = sort if sort in catalog.SORTS else "price"
        with db.session_scope(sf) as s:
            rows = [
                m
                for k in kinds
                for m in catalog.list_models(s, k, sort=order)
                if constraints.is_generate_capable(k, m.capabilities_json or [], m.constraints_json)
                and (not favourites_only or m.is_favourite)
            ]
            return [model_summary(m) for m in rows]

    @server.tool()
    @_guard
    def model_details(air: str | None = None) -> dict:
        """Everything one model accepts: reference roles, sizes, durations, resolutions
        and its provider settings schema. With no ``air``, the default image model."""
        with db.session_scope(sf) as s:
            m = _model(s, _text(air) or _default_air(ctx, "image"))
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
                "defaults": {
                    "width": m.default_width,
                    "height": m.default_height,
                    "duration": _duration_for(m, None) if m.kind == "video" else None,
                    "resolution": _default_resolution(m) if m.kind == "video" else None,
                },
            }

    @server.tool()
    @_guard
    def estimate(
        kind: str | None = None,
        air: str | None = None,
        width: int | None = None,
        height: int | None = None,
        number_results: int | None = 1,
        duration: float | None = None,
        resolution: str | None = "",
        audio: bool | None = None,
    ) -> dict:
        """What one run would cost, in US dollars -- the same number the cap is checked
        against, for the size, duration and settings the job would really use.
        ``estimate_usd`` is null when the model has no known price; those cannot run under
        a spend cap. Leave ``audio`` unset to price the model's own default. With no
        ``air`` the default model of ``kind`` (image unless said) is priced; with an
        ``air`` and no ``kind``, the model's own kind is used."""
        air_ref, kind_ref = _text(air), _text(kind)
        resolution = _text(resolution)
        number_results = int(_num(number_results, 1))
        with db.session_scope(sf) as s:
            if not air_ref:
                k = _kind(kind_ref or "image")
                air_ref = _default_air(ctx, k)
                m = _model(s, air_ref, k)
            elif kind_ref:
                k = _kind(kind_ref)
                m = _model(s, air_ref, k)
            else:
                m = _model(s, air_ref)
                k = m.kind
            air = air_ref
            if k == "video" and not resolution:
                resolution = _default_resolution(m)
            if k == "image":
                asked = (
                    int(width or m.default_width or 1024),
                    int(height or m.default_height or 1024),
                )
                used = _require_image_size(m, constraints.nearest_size(m.constraints_json, *asked))
                data = estimate_ctx(s, air, used[0], used[1], number_results)
                return {
                    "air": air,
                    "kind": k,
                    "estimate_usd": data["total"],
                    "rate": m.price_primary,
                    "note": _snap_note(asked, used),
                    "width": used[0],
                    "height": used[1],
                    "number_results": data["number_results"],
                }
            seconds = _duration_for(m, duration)
            size = _video_size(m, width, height, resolution)
            tier = _video_tier(m, size, resolution)
            sound = _audio_on(_provider_settings(m, None)) if audio is None else bool(audio)
            data = video_estimate_ctx(s, air, seconds, sound, tier)
            notes = []
            if duration is not None and seconds != float(duration):
                notes.append(f"duration snapped to {seconds:g}s")
            if tier != resolution:
                notes.append(f"priced at the {tier} rate")
            return {
                "air": air,
                "kind": k,
                "estimate_usd": data["total"],
                "rate": data["rate"],
                "note": "; ".join(notes),
                "duration": seconds,
                "resolution": tier,
                "audio": sound,
            }

    # ---- generation ------------------------------------------------------
    @server.tool()
    @_guard
    def generate_image(
        prompt: str,
        air: str | None = None,
        project: str | int | None = "default",
        width: int | None = 1024,
        height: int | None = 1024,
        number_results: int | None = 1,
        negative_prompt: str | None = "",
        seed: int | None = None,
        seed_image_asset_id: int | None = None,
        reference_asset_ids: list[int] | None = None,
        title: str | None = None,
    ) -> dict:
        """Queue an image job. Only ``prompt`` is needed: the default image model, the
        Default project and a 1024x1024 size fill in the rest. Refuses, before anything is
        queued or billed, when today's agent spend would pass the cap."""
        if not _text(prompt):
            raise ValueError("A prompt is required: say what the image should show.")
        negative_prompt = _text(negative_prompt)
        number_results = max(1, int(_num(number_results, 1)))
        runner()  # before the row exists: a refusal must leave nothing to requeue
        with db.session_scope(sf) as s:
            p = _project(s, _text(project) or "default")
            air = _text(air) or _default_air(ctx, "image")
            m = _model(s, air, "image")
            asked = (
                int(_num(width, m.default_width or 1024)),
                int(_num(height, m.default_height or 1024)),
            )
            used = _require_image_size(m, constraints.nearest_size(m.constraints_json, *asked))
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
        prompt: str,
        air: str | None = None,
        project: str | int | None = "default",
        duration: float | None = DEFAULT_DURATION,
        resolution: str | None = None,
        width: int | None = None,
        height: int | None = None,
        first_frame_asset_id: int | None = None,
        last_frame_asset_id: int | None = None,
        reference_asset_ids: list[int] | None = None,
        provider_settings: dict | None = None,
        title: str | None = None,
    ) -> dict:
        """Queue a video job. Only ``prompt`` is needed: the default video model, the
        Default project, the model's own duration and resolution tier fill in the rest. Same cap, and the same
        refusal-before-billing rule.

        Everything that moves the price is resolved here -- the settings the provider will
        really see, the duration the task builder will really send, the size and its rate
        tier -- so the number the cap is checked against is the number that gets billed."""
        if not _text(prompt):
            raise ValueError("A prompt is required: say what the clip should show.")
        resolution = _text(resolution)
        duration = _num(duration, DEFAULT_DURATION)
        runner()  # before the row exists: a refusal must leave nothing to requeue
        with db.session_scope(sf) as s:
            p = _project(s, _text(project) or "default")
            air = _text(air) or _default_air(ctx, "video")
            m = _model(s, air, "video")
            resolution = resolution or _default_resolution(m)
            seconds = _duration_for(m, duration)
            settings_sent = _provider_settings(m, provider_settings)
            size = _video_size(m, width, height, resolution)
            tier = _video_tier(m, size, resolution)
            est = video_estimate_ctx(s, air, seconds, _audio_on(settings_sent), tier)["total"]
            project_id = p.id
            sized = _size_mode(m) != "unknown"
        req = VideoRequest(
            project_id=project_id,
            model=air,
            final_prompt=prompt,
            form=PromptForm(subject=prompt, no_text=False),
            duration=seconds,
            resolution=tier,
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
        asked = (int(width), int(height)) if width is not None and height is not None else None
        if size is not None and asked is None:
            notes.append(f"size set to {size[0]}x{size[1]}")
        elif size is not None and size != asked:
            notes.append(f"size snapped to {size[0]}x{size[1]}")
        if not sized and (width is not None or height is not None):
            notes.append(f"this model lists no sizes: {tier} decides the dimensions")
        elif size is None and (width is None) != (height is None):
            notes.append(f"width and height go together: {tier} decides the dimensions")
        if tier != resolution:
            notes.append(f"priced at the {tier} rate")
        return {
            "job_id": job.id,
            "estimate_usd": est,
            "cap": cap,
            "note": "; ".join(notes),
            "duration": seconds,
            "resolution": tier,
            "provider_settings": settings_sent,
        }

    # ---- queue -----------------------------------------------------------
    @server.tool()
    @_guard
    def job_status(job_id: str | None = None) -> dict:
        """One job with its outputs, progress and error, if any. With no ``job_id``,
        the newest job an agent started."""
        with db.session_scope(sf) as s:
            job = _job_ref(s, job_id)
            return job_summary(s, job, paths, ctx.base_url, live_for(job.id))

    @server.tool()
    @_guard
    async def wait_for_job(
        job_id: str | None = None, timeout_s: int | None = 300, mcp_ctx: Context | None = None
    ) -> dict:
        """Wait until the job finishes (or ``timeout_s``, at most 900) and return its
        outputs. A reply carrying ``timed_out`` means the job is still running. With no
        ``job_id``, the newest job an agent started."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1, min(int(_num(timeout_s, 300)), TIMEOUT_MAX))
        delay = POLL_FIRST
        with db.session_scope(sf) as s:
            job_id = _job_ref(s, job_id).id
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
    def cancel_job(job_id: str | None = None) -> dict:
        """Ask the queue to drop or abort a job (the newest agent job when no id is
        given). False means it had already finished."""
        with db.session_scope(sf) as s:
            job_id = _job_ref(s, job_id).id
        return {"cancelled": bool(runner().cancel(job_id))}

    @server.tool()
    @_guard
    def list_jobs(status: str | None = "", limit: int | None = 20) -> list[dict]:
        """Recent jobs, newest first, optionally filtered by status."""
        n = max(1, min(int(_num(limit, 20)), 100))
        status = _text(status)
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
        project: str | int | None = "",
        kind: str | None = "",
        search: str | None = "",
        limit: int | None = 24,
    ) -> list[dict]:
        """Finished files, newest first, with a URL and an absolute path for each."""
        n = max(1, min(int(_num(limit, 24)), 100))
        with db.session_scope(sf) as s:
            project_id = _project(s, project).id if _text(project) else None
            rows, _ = outputs.gallery(
                s,
                project_id=project_id,
                kind=_text(kind) or None,
                q=_text(search) or None,
                per_page=n,
            )
            return [output_summary(s, o, paths, ctx.base_url) for o in rows]

    @server.tool()
    @_guard
    def output_details(output_id: int | None = None) -> dict:
        """One output with the prompt, the negative, the parameters actually sent, and
        where the file is. With no ``output_id``, the newest output."""
        with db.session_scope(sf) as s:
            o = _output_ref(s, output_id)
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
    def move_output(project: str | int, output_id: int | None = None) -> dict:
        """File an output (the newest one when no id is given) under another project;
        the media file moves with the row."""
        with db.session_scope(sf) as s:
            target = _project(s, project)
            output_id = _output_ref(s, output_id).id
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
    def create_project(name: str, description: str | None = "") -> dict:
        """Make a project. The slug is derived from the name and is stable afterwards."""
        if not _text(name):
            raise ValueError("A project needs a name.")
        with db.session_scope(sf) as s:
            p = projects.create(s, paths, _text(name), _text(description) or None)
            return {"id": p.id, "name": p.name, "slug": p.slug}

    @server.tool()
    @_guard
    def list_assets(
        kind: str | None = "", search: str | None = "", limit: int | None = 50
    ) -> list[dict]:
        """Uploaded images and videos, by id, for the seed/first-frame/reference slots."""
        n = max(1, min(int(_num(limit, 50)), 200))
        with db.session_scope(sf) as s:
            rows, _ = assets_svc.list_assets(
                s, kind=_text(kind) or None, q=_text(search) or None, per_page=n
            )
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
