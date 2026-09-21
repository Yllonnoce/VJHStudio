"""The Generate page: prompt builder, model parameters, cost estimate, submit.

The form is parsed by hand rather than by a FastAPI body model: HTML checkboxes post
nothing when cleared, so every boolean field is rendered with a hidden ``off`` companion
and the *last* posted value wins. Everything else is handed to pydantic, whose errors are
turned straight back into an inline 422 re-render of the parameters column.

The page serves two modes from one form. ``mode=image`` posts to ``/generate/image`` and
renders ``generate/_model_params.html``; ``mode=video`` posts to ``/generate/video`` and
renders ``generate/_video_params.html``. Both partials keep the id ``#model-params`` so a
mode switch, a model change and a 422 all swap the same slot.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ... import db
from ...models import CatalogModel
from ...runware.tasks import nearest
from ...schemas.image import ImageRequest, PromptForm
from ...schemas.video import VideoRequest
from ...services import assets as assets_svc
from ...services import catalog, generate, projects, prompts
from ...services import outputs as outputs_svc
from ...services import settings as settings_svc
from .. import deps
from ..urls import asset_thumb_url
from .jobs import panel_ctx

router = APIRouter()

PROMPT_FIELDS = (
    "subject",
    "style",
    "mood",
    "lighting",
    "camera",
    "composition",
    "colour",
    "extras",
    "negative",
)
SCALAR_FIELDS = (
    "project_id",
    "prompt_id",
    "model",
    "final_prompt",
    "width",
    "height",
    "number_results",
    "seed",
    "steps",
    "cfg_scale",
    "scheduler",
    "strength",
    "output_format",
    "title",
)
VIDEO_SCALAR_FIELDS = (
    "project_id",
    "prompt_id",
    "model",
    "final_prompt",
    "duration",
    "resolution",
    "fps",
    "seed",
    "output_format",
    "title",
)
SIZE_PRESETS = (
    (1024, 1024, "Square 1:1"),
    (1152, 896, "Landscape 4:3"),
    (896, 1152, "Portrait 3:4"),
    (1344, 768, "Wide 16:9"),
    (768, 1344, "Tall 9:16"),
)
SCHEDULERS = ("", "Default", "DPM++ 2M", "DPM++ 2M Karras", "Euler", "Euler a", "DDIM", "UniPC")
TRUTHY = ("on", "1", "true", "yes")
REF_PX = 1024 * 1024

PARAMS_TEMPLATES = {
    "image": "generate/_model_params.html",
    "video": "generate/_video_params.html",
}
PS_PREFIX = "ps_"
AUDIO_KEYS = ("generateAudio", "sound")
FALLBACK_RESOLUTIONS = ("720p", "1080p")
VIDEO_FORMATS = ("MP4", "WEBM")
DEFAULT_DURATION = 5.0
# The single-slot reference roles and the request field each one fills. ``reference`` is
# many-to-one and lives in ``reference_asset_ids``, so it is not in this map.
ROLE_FIELDS = {
    "seed": "seed_image_asset_id",
    "first": "first_frame_asset_id",
    "last": "last_frame_asset_id",
}
MODE_ROLES = {"image": ("seed", "reference"), "video": ("first", "last", "reference")}
REF_ROLES = ("reference", "seed", "first", "last")


# ---- form parsing --------------------------------------------------------
def _flag(form, name: str, default: bool) -> bool:
    """The last value wins: the builder posts a hidden ``off`` before each checkbox."""
    values = form.getlist(name)
    return default if not values else str(values[-1]).strip().lower() in TRUTHY


def parse_request(form) -> ImageRequest:
    pf = PromptForm(
        **{k: str(form.get(k, "") or "") for k in PROMPT_FIELDS},
        use_default_negative=_flag(form, "use_default_negative", True),
        no_text=_flag(form, "no_text", True),
    )
    data: dict = {"form": pf}
    for key in SCALAR_FIELDS:
        value = str(form.get(key, "") or "").strip()
        if value:
            data[key] = value
    _read_refs(form, data, "image")
    data["extra_json"] = _extra_json(form)
    return ImageRequest(**data)


def _extra_json(form):
    extra = str(form.get("extra_json", "") or "").strip()
    if not extra:
        return {}
    try:
        return json.loads(extra)
    except ValueError:
        return extra  # a string where a dict belongs: pydantic reports it


def _read_refs(form, data: dict, mode: str) -> None:
    """Asset pickers post one id per single-slot role and a repeated
    ``reference_asset_ids``; blanks (a chip removed client-side) are skipped."""
    for role in MODE_ROLES[mode]:
        field = ROLE_FIELDS.get(role)
        if field is None:
            continue
        value = str(form.get(field, "") or "").strip()
        if value:
            data[field] = value
    refs = [str(v).strip() for v in form.getlist("reference_asset_ids")]
    data["reference_asset_ids"] = [v for v in refs if v]


def _coerce_setting(raw: str, kind: str):
    if kind in ("int", "integer"):
        try:
            return int(raw)
        except ValueError:
            return raw
    if kind in ("number", "float"):
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def parse_provider_settings(form, schema: list[dict]) -> dict:
    """``ps_<key>`` inputs → the provider's own settings dict. Schema-declared booleans
    are read through ``_flag`` so an unchecked box lands as ``False``, not as absent."""
    types = {
        str(e.get("key")): str(e.get("type") or "string").lower() for e in schema if e.get("key")
    }
    defaults = {str(e.get("key")): e.get("default") for e in schema if e.get("key")}
    out: dict = {}
    posted = {k for k in form if k.startswith(PS_PREFIX)}
    for key, kind in types.items():
        if kind == "bool":
            out[key] = _flag(form, PS_PREFIX + key, bool(defaults.get(key)))
            posted.discard(PS_PREFIX + key)
    for name in sorted(posted):
        raw = str(form.get(name, "") or "").strip()
        if not raw:
            continue
        key = name[len(PS_PREFIX) :]
        out[key] = _coerce_setting(raw, types.get(key, "string"))
    return out


def parse_video_request(form, schema: list[dict] | None = None) -> VideoRequest:
    pf = PromptForm(
        **{k: str(form.get(k, "") or "") for k in PROMPT_FIELDS},
        use_default_negative=_flag(form, "use_default_negative", True),
        no_text=_flag(form, "no_text", False),  # stills hide text, clips rarely need to
    )
    data: dict = {"form": pf}
    for key in VIDEO_SCALAR_FIELDS:
        value = str(form.get(key, "") or "").strip()
        if value:
            data[key] = value
    _read_refs(form, data, "video")
    data["provider_settings"] = parse_provider_settings(form, schema or [])
    data["extra_json"] = _extra_json(form)
    return VideoRequest(**data)


def error_map(exc: ValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in exc.errors():
        key = ".".join(str(p) for p in e["loc"]) or "form"
        out.setdefault(key, e["msg"])
    return out


# ---- shared context ------------------------------------------------------
def _model_row(session, air: str) -> CatalogModel | None:
    return catalog.get_by_air(session, air) if air else None


def _mode(raw: str) -> str:
    return "video" if str(raw or "").strip().lower() == "video" else "image"


def params_ctx(session, air: str, values: dict | None = None, errors: dict | None = None) -> dict:
    m = _model_row(session, air)
    values = dict(values or {})
    return {
        "model": m,
        "air": air,
        "mode": "image",
        "family": catalog.family(m) if m is not None else "diffusion",
        "capabilities": list((m.capabilities_json if m else None) or []),
        "defaults": {
            "width": (m.default_width if m else None) or 1024,
            "height": (m.default_height if m else None) or 1024,
            "steps": (m.default_steps if m else None) or 28,
            "cfg": (m.default_cfg if m else None) or 3.5,
        },
        "size_presets": SIZE_PRESETS,
        "schedulers": SCHEDULERS,
        "values": values,
        "errors": errors or {},
    }


def _video_tiers(m: CatalogModel | None) -> dict:
    """``tiers.video`` is present on every curated video row and absent on a search-added
    one, so every read goes through ``.get`` with a fallback the UI can still render."""
    if m is None:
        return {}
    return dict(((m.price_tiers_json or {}).get("video")) or {})


def provider_schema(session, air: str) -> list[dict]:
    m = _model_row(session, air)
    return list((m.provider_settings_schema if m else None) or [])


def video_params_ctx(
    session, air: str, values: dict | None = None, errors: dict | None = None
) -> dict:
    m = _model_row(session, air)
    tiers = _video_tiers(m)
    durations = [d for d in (tiers.get("durations") or []) if isinstance(d, (int, float))]
    resolutions = [str(r) for r in (tiers.get("resolutions") or [])] or list(FALLBACK_RESOLUTIONS)
    fps_options = [f for f in (tiers.get("fps") or []) if isinstance(f, (int, float))]
    return {
        "model": m,
        "air": air,
        "mode": "video",
        "capabilities": list((m.capabilities_json if m else None) or []),
        "durations": durations,
        "resolutions": resolutions,
        "fps_options": fps_options,
        "provider_settings": list((m.provider_settings_schema if m else None) or []),
        "formats": VIDEO_FORMATS,
        "defaults": {
            "duration": nearest(DEFAULT_DURATION, durations) or DEFAULT_DURATION,
            "resolution": resolutions[0] if resolutions else FALLBACK_RESOLUTIONS[0],
        },
        "values": dict(values or {}),
        "errors": errors or {},
    }


def params_for(session, mode: str, air: str, values=None, errors=None) -> tuple[str, dict]:
    build = video_params_ctx if mode == "video" else params_ctx
    return PARAMS_TEMPLATES[mode], build(session, air, values, errors)


def _int_or(raw, default: int) -> int:
    """Lenient query parsing: a half-typed or cleared form field must re-render the
    estimate, never hand FastAPI's 422 *JSON* body to an htmx swap target."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _float_or(raw, default: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def estimate_ctx(session, air: str, width=None, height=None, n=None) -> dict:
    m = _model_row(session, air)
    w = _int_or(width, (m.default_width if m is not None else None) or 1024)
    h = _int_or(height, (m.default_height if m is not None else None) or 1024)
    count = _int_or(n, 1)
    price = m.price_primary if m is not None else None
    total = None
    if price is not None:
        total = float(price) * (w * h / REF_PX) * count
    return {
        "mode": "image",
        "air": air,
        "total": total,
        "width": w,
        "height": h,
        "number_results": count,
    }


def _amount(rate: dict) -> float | None:
    value = rate.get("amount")
    return float(value) if isinstance(value, (int, float)) else None


def rate_for(m: CatalogModel | None, resolution: str = "", audio: bool = False) -> float | None:
    """The per-second rate the catalog quotes for this resolution and audio setting.

    ``tiers.rates`` labels are free text, so the match is by substring: first narrow to
    the rows that name the chosen resolution ("1080p" for LTX and Wan, "480p" for
    Seedance), then, when those rows distinguish audio at all (Veo's
    "720p / 1080p · with audio"), pick the matching side. "with audio" never matches
    "without audio", which spells the phrase differently. ``None`` means "no labelled
    rate applies" and the caller falls back to ``price_primary``.
    """
    rows = [
        (str(r.get("label") or "").lower(), _amount(r))
        for r in (((m.price_tiers_json if m else None) or {}).get("rates") or [])
        if isinstance(r, dict)
    ]
    res = (resolution or "").strip().lower()
    narrowed = [row for row in rows if res and res in row[0]]
    pool = narrowed or rows
    if any("audio" in label for label, _ in pool):
        wanted = "with audio" if audio else "without audio"
        for label, amount in pool:
            if wanted in label:
                return amount
    if narrowed:  # a resolution-only tier list: the narrowed row is the price
        return narrowed[0][1]
    return None


def video_estimate_ctx(
    session, air: str, duration=None, audio: bool = False, resolution: str = ""
) -> dict:
    m = _model_row(session, air)
    seconds = _float_or(duration, DEFAULT_DURATION)
    rate = rate_for(m, resolution, audio)
    if rate is None:
        rate = float(m.price_primary) if m is not None and m.price_primary is not None else None
    total = rate * seconds if rate is not None else None
    return {
        "mode": "video",
        "air": air,
        "total": total,
        "duration": seconds,
        "resolution": resolution,
        "rate": rate,
        "audio": audio,
    }


def audio_on(source, schema: list[dict], default_from_schema: bool = False) -> bool:
    """``source`` is a form or a query string; ``default_from_schema`` is for the first
    page render, where no checkbox has been posted yet but one is about to be rendered
    already ticked."""
    for entry in schema:
        key = str(entry.get("key") or "")
        if key in AUDIO_KEYS and str(entry.get("type") or "").lower() == "bool":
            fallback = bool(entry.get("default")) if default_from_schema else False
            return _flag(source, PS_PREFIX + key, fallback)
    return False


def safe_json(data) -> str:
    """JSON for a <script> block: the three characters that could close it early are
    escaped, so a prompt containing "</script>" cannot break out of the tag."""
    return (
        json.dumps(data, indent=1, default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def ref_chips(session, data: dict, mode: str) -> list[dict]:
    """The picked assets a remix should re-populate, resolved to thumb and name here so
    the Alpine chips need no second round trip."""
    picks: list[tuple[str, int]] = []
    for role in MODE_ROLES[mode]:
        field = ROLE_FIELDS.get(role)
        if field is None:
            continue
        try:
            asset_id = int(data.get(field) or 0)
        except (TypeError, ValueError):
            asset_id = 0
        if asset_id:
            picks.append((role, asset_id))
    for raw in data.get("reference_asset_ids") or []:
        try:
            picks.append(("reference", int(raw)))
        except (TypeError, ValueError):
            continue
    chips = []
    for role, asset_id in picks:
        asset = assets_svc.get(session, asset_id)
        if asset is None:  # a deleted asset simply drops out of the remixed form
            continue
        chips.append(
            {
                "id": asset.id,
                "role": role,
                "name": asset.original_name,
                "thumb": asset_thumb_url(assets_svc.thumb_rel(asset)) or "",
            }
        )
    return chips


def _ref_initial(session, ref: str, role: str) -> dict:
    """``?ref=asset:<id>&role=reference`` -> a form that opens with that one chip already
    picked. The role decides the mode: a first/last frame only exists in video."""
    kind, _, raw = (ref or "").partition(":")
    try:
        asset_id = int(raw)
    except ValueError as e:
        raise LookupError(ref) from e
    if kind != "asset" or asset_id <= 0:
        raise LookupError(ref)
    wanted = (role or "reference").strip().lower()
    if wanted not in REF_ROLES:
        wanted = "reference"
    mode = "video" if wanted in ("first", "last") else "image"
    data: dict = {}
    field = ROLE_FIELDS.get(wanted)
    if field is None:
        data["reference_asset_ids"] = [asset_id]
    else:
        data[field] = asset_id
    chips = ref_chips(session, data, mode)
    if not chips:  # an unknown (or deleted) asset is a bad link, not an empty form
        raise LookupError(ref)
    data["mode"] = mode
    data["refs"] = chips
    return data


def _initial(session, remix: str, ref: str = "", role: str = "") -> dict:
    if ref and not remix:
        return _ref_initial(session, ref, role)
    if not remix:
        return {}
    try:
        output = outputs_svc.get(session, int(remix))
    except (TypeError, ValueError):
        output = None
    if output is None:
        raise LookupError(remix)
    data = outputs_svc.remix_request(output)
    data.pop("negative", None)
    mode = "video" if output.kind == "video" else "image"
    data["mode"] = mode
    data["refs"] = ref_chips(session, data, mode)
    return data


# ---- routes --------------------------------------------------------------
class _QueryLike:
    """``audio_on`` reads a form/query multidict; a remix carries a plain
    ``provider_settings`` dict instead, so it is wrapped in the same tiny interface."""

    def __init__(self, data: dict):
        self._data = data

    def getlist(self, name: str) -> list[str]:
        key = name[len(PS_PREFIX) :] if name.startswith(PS_PREFIX) else name
        if key not in self._data:
            return []
        return ["on" if self._data[key] else "off"]


def _page(request: Request, remix: str, mode: str, ref: str = "", role: str = ""):
    app = request.app
    with db.session_scope(app.state.boot.session_factory) as s:
        try:
            initial = _initial(s, remix, ref, role)
        except LookupError as e:
            raise HTTPException(status_code=404, detail="unknown output or asset") from e
        mode = _mode(initial.get("mode") or mode)
        initial["mode"] = mode
        initial.setdefault("refs", [])
        models = catalog.list_models(s, "image")
        video_models = catalog.list_models(s, "video")
        chosen = str(initial.get("model") or "")
        image_air = (chosen if mode == "image" and chosen else "") or settings_svc.get(
            s, "defaults.image_model", app.state.env
        )
        video_air = (chosen if mode == "video" and chosen else "") or settings_svc.get(
            s, "defaults.video_model", app.state.env
        )
        air = video_air if mode == "video" else image_air
        fields = VIDEO_SCALAR_FIELDS if mode == "video" else SCALAR_FIELDS
        values = {k: initial.get(k) for k in fields if initial.get(k) is not None}
        values.setdefault(
            "output_format",
            settings_svc.get(
                s,
                "defaults.output_format_video"
                if mode == "video"
                else "defaults.output_format_image",
                app.state.env,
            ),
        )
        template, params = params_for(s, mode, air, values)
        if mode == "video":
            params["posted_settings"] = dict(initial.get("provider_settings") or {})
            audio = audio_on(
                _QueryLike(initial.get("provider_settings") or {}),
                params["provider_settings"],
                default_from_schema=True,
            )
            # the selects render the model's own defaults, so the first estimate must
            # price those and not the schema's generic 5 s / 720p
            estimate = video_estimate_ctx(
                s,
                air,
                values.get("duration") or params["defaults"]["duration"],
                audio,
                values.get("resolution") or params["defaults"]["resolution"],
            )
        else:
            estimate = estimate_ctx(
                s,
                air,
                values.get("width"),
                values.get("height"),
                values.get("number_results"),
            )
        ctx = {
            "mode": mode,
            "models": models,
            "video_models": video_models,
            "labels": {m.air: catalog.label(m) for m in models + video_models},
            "selected": image_air,
            "video_selected": video_air,
            "projects": projects.list_active(s),
            "selected_project": initial.get("project_id"),
            "form": dict(initial.get("form") or {}),
            "final_prompt": initial.get("final_prompt") or "",
            "initial_json": safe_json(initial),
            "params_template": template,
            "params": params,
            "estimate": estimate,
            "default_negative": settings_svc.get(s, "defaults.negative_prompt", app.state.env),
            "no_text_tokens": prompts.NO_TEXT_NEGATIVE,
        }
    ctx.update(panel_ctx(request))
    ctx["oob"] = False  # the page already carries the header badge
    return deps.render(request, "pages/generate.html", ctx)


@router.get("/generate")
def generate_page(
    request: Request,
    remix: str = "",
    prompt: str = "",
    mode: str = "image",
    ref: str = "",
    role: str = "",
):
    return _page(request, remix, _mode(mode), ref, role)


@router.get("/generate/video")
def generate_video_page(
    request: Request, remix: str = "", prompt: str = "", ref: str = "", role: str = ""
):
    """Bookmarkable alias for ``/generate?mode=video``."""
    return _page(request, remix, "video", ref, role)


@router.post("/generate/image")
def submit_image(request: Request, form: deps.Form):
    app = request.app
    no_key = _no_key(request)
    if no_key is not None:
        return no_key
    air = str(form.get("model", "") or "")
    try:
        req = parse_request(form)
    except ValidationError as e:
        return _params_422(request, "image", air, form, error_map(e))
    with db.session_scope(app.state.boot.session_factory) as s:
        default_negative = settings_svc.get(s, "defaults.negative_prompt", app.state.env)
    try:
        job = generate.enqueue_image(
            app.state.boot.session_factory, app.state.paths, req, default_negative=default_negative
        )
    except ValueError as e:
        return _params_422(request, "image", air, form, {"model": str(e)})
    return _submitted(request, job)


@router.post("/generate/video")
def submit_video(request: Request, form: deps.Form):
    app = request.app
    no_key = _no_key(request)
    if no_key is not None:
        return no_key
    air = str(form.get("model", "") or "")
    with db.session_scope(app.state.boot.session_factory) as s:
        schema = provider_schema(s, air)
    try:
        req = parse_video_request(form, schema)
    except ValidationError as e:
        return _params_422(request, "video", air, form, error_map(e))
    try:
        job = generate.enqueue_video(app.state.boot.session_factory, app.state.paths, req)
    except ValueError as e:
        return _params_422(request, "video", air, form, {"model": str(e)})
    return _submitted(request, job)


def _no_key(request: Request):
    if request.app.state.api_key():
        return None
    # the form targets #queue-panel; a missing key is not a queue event, so the
    # banner goes into the always-present error slot instead of eating the panel
    r = deps.render(request, "generate/_no_key.html", {}, 422)
    r.headers["HX-Retarget"] = "#gen-errors"
    r.headers["HX-Reswap"] = "innerHTML"
    return r


def _submitted(request: Request, job):
    runner = getattr(request.app.state, "runner", None)
    if runner is not None:
        runner.submit(job.id)
    r = deps.render(request, "generate/_queue_panel.html", panel_ctx(request, oob_badge=True))
    r.headers["HX-Trigger"] = "jobs-changed"
    return r


def _params_422(request: Request, mode: str, air: str, form, errors: dict):
    fields = VIDEO_SCALAR_FIELDS if mode == "video" else SCALAR_FIELDS
    values = {k: str(form.get(k, "") or "") for k in fields}
    with db.session_scope(request.app.state.boot.session_factory) as s:
        template, ctx = params_for(s, mode, air, values, errors)
        if mode == "video":
            ctx["posted_settings"] = parse_provider_settings(form, ctx["provider_settings"])
    r = deps.render(request, template, ctx, 422)
    r.headers["HX-Retarget"] = "#model-params"
    return r


@router.get("/hx/model-options")
def hx_model_options(request: Request, air: str = "", model: str = "", mode: str = "image"):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        template, ctx = params_for(s, _mode(mode), air or model)
    return deps.render(request, template, ctx)


@router.get("/hx/generate/estimate")
def hx_estimate(
    request: Request,
    air: str = "",
    model: str = "",
    width: str = "",
    height: str = "",
    number_results: str = "",
    mode: str = "image",
    duration: str = "",
    resolution: str = "",
):
    # strings on purpose: declaring ``int`` lets FastAPI answer a cleared Width box with a
    # 422 JSON body, which htmx (configured to swap 422s) would paste into #estimate.
    with db.session_scope(request.app.state.boot.session_factory) as s:
        if _mode(mode) == "video":
            schema = provider_schema(s, air or model)
            ctx = video_estimate_ctx(
                s, air or model, duration, audio_on(request.query_params, schema), resolution
            )
        else:
            ctx = estimate_ctx(s, air or model, width, height, number_results)
    return deps.render(request, "generate/_estimate.html", ctx)


@router.post("/hx/prompt/compose")
def hx_compose(request: Request, form: deps.Form):
    """Server-side mirror of the client preview: the same code the job will run."""
    pf = PromptForm(
        **{k: str(form.get(k, "") or "") for k in PROMPT_FIELDS},
        use_default_negative=_flag(form, "use_default_negative", True),
        no_text=_flag(form, "no_text", True),
    )
    with db.session_scope(request.app.state.boot.session_factory) as s:
        default_negative = settings_svc.get(s, "defaults.negative_prompt", request.app.state.env)
    composed = prompts.compose(pf)
    return deps.render(
        request,
        "generate/_prompt_preview.html",
        {
            "composed": composed,
            "negative": prompts.build_negative(pf, default_negative, pf.no_text),
            "length": len(prompts.cap(composed, prompts.NO_TEXT_SUFFIX if pf.no_text else "")),
        },
    )
