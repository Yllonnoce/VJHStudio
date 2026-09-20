"""The Generate page: prompt builder, model parameters, cost estimate, submit.

The form is parsed by hand rather than by a FastAPI body model: HTML checkboxes post
nothing when cleared, so every boolean field is rendered with a hidden ``off`` companion
and the *last* posted value wins. Everything else is handed to pydantic, whose errors are
turned straight back into an inline 422 re-render of the parameters column.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ... import db
from ...models import CatalogModel
from ...schemas.image import ImageRequest, PromptForm
from ...services import catalog, generate, projects, prompts
from ...services import outputs as outputs_svc
from ...services import settings as settings_svc
from .. import deps
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
    extra = str(form.get("extra_json", "") or "").strip()
    if extra:
        try:
            data["extra_json"] = json.loads(extra)
        except ValueError:
            data["extra_json"] = extra  # a string where a dict belongs: pydantic reports it
    return ImageRequest(**data)


def error_map(exc: ValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in exc.errors():
        key = ".".join(str(p) for p in e["loc"]) or "form"
        out.setdefault(key, e["msg"])
    return out


# ---- shared context ------------------------------------------------------
def _model_row(session, air: str) -> CatalogModel | None:
    return catalog.get_by_air(session, air) if air else None


def params_ctx(session, air: str, values: dict | None = None, errors: dict | None = None) -> dict:
    m = _model_row(session, air)
    values = dict(values or {})
    return {
        "model": m,
        "air": air,
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


def _int_or(raw, default: int) -> int:
    """Lenient query parsing: a half-typed or cleared form field must re-render the
    estimate, never hand FastAPI's 422 *JSON* body to an htmx swap target."""
    try:
        value = int(str(raw).strip())
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
    return {"air": air, "total": total, "width": w, "height": h, "number_results": count}


def safe_json(data) -> str:
    """JSON for a <script> block: the three characters that could close it early are
    escaped, so a prompt containing "</script>" cannot break out of the tag."""
    return (
        json.dumps(data, indent=1, default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _initial(session, remix: str) -> dict:
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
    return data


# ---- routes --------------------------------------------------------------
@router.get("/generate")
def generate_page(request: Request, remix: str = "", prompt: str = ""):
    app = request.app
    with db.session_scope(app.state.boot.session_factory) as s:
        try:
            initial = _initial(s, remix)
        except LookupError as e:
            raise HTTPException(status_code=404, detail="unknown output") from e
        models = catalog.list_models(s, "image")
        air = str(
            initial.get("model") or settings_svc.get(s, "defaults.image_model", app.state.env)
        )
        values = {k: initial.get(k) for k in SCALAR_FIELDS if initial.get(k) is not None}
        values.setdefault(
            "output_format", settings_svc.get(s, "defaults.output_format_image", app.state.env)
        )
        ctx = {
            "models": models,
            "labels": {m.air: catalog.label(m) for m in models},
            "selected": air,
            "projects": projects.list_active(s),
            "selected_project": initial.get("project_id"),
            "form": dict(initial.get("form") or {}),
            "final_prompt": initial.get("final_prompt") or "",
            "initial_json": safe_json(initial),
            "params": params_ctx(s, air, values),
            "estimate": estimate_ctx(
                s,
                air,
                values.get("width"),
                values.get("height"),
                values.get("number_results"),
            ),
            "default_negative": settings_svc.get(s, "defaults.negative_prompt", app.state.env),
            "no_text_tokens": prompts.NO_TEXT_NEGATIVE,
        }
    ctx.update(panel_ctx(request))
    ctx["oob"] = False  # the page already carries the header badge
    return deps.render(request, "pages/generate.html", ctx)


@router.post("/generate/image")
def submit_image(request: Request, form: deps.Form):
    app = request.app
    if not app.state.api_key():
        # the form targets #queue-panel; a missing key is not a queue event, so the
        # banner goes into the always-present error slot instead of eating the panel
        r = deps.render(request, "generate/_no_key.html", {}, 422)
        r.headers["HX-Retarget"] = "#gen-errors"
        r.headers["HX-Reswap"] = "innerHTML"
        return r
    air = str(form.get("model", "") or "")
    try:
        req = parse_request(form)
    except ValidationError as e:
        return _params_422(request, air, form, error_map(e))
    with db.session_scope(app.state.boot.session_factory) as s:
        default_negative = settings_svc.get(s, "defaults.negative_prompt", app.state.env)
    try:
        job = generate.enqueue_image(
            app.state.boot.session_factory, app.state.paths, req, default_negative=default_negative
        )
    except ValueError as e:
        return _params_422(request, air, form, {"model": str(e)})
    runner = getattr(app.state, "runner", None)
    if runner is not None:
        runner.submit(job.id)
    r = deps.render(request, "generate/_queue_panel.html", panel_ctx(request, oob_badge=True))
    r.headers["HX-Trigger"] = "jobs-changed"
    return r


def _params_422(request: Request, air: str, form, errors: dict):
    values = {k: str(form.get(k, "") or "") for k in SCALAR_FIELDS}
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ctx = params_ctx(s, air, values, errors)
    r = deps.render(request, "generate/_model_params.html", ctx, 422)
    r.headers["HX-Retarget"] = "#model-params"
    return r


@router.get("/hx/model-options")
def hx_model_options(request: Request, air: str = "", model: str = "", mode: str = "image"):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ctx = params_ctx(s, air or model)
    return deps.render(request, "generate/_model_params.html", ctx)


@router.get("/hx/generate/estimate")
def hx_estimate(
    request: Request,
    air: str = "",
    model: str = "",
    width: str = "",
    height: str = "",
    number_results: str = "",
):
    # strings on purpose: declaring ``int`` lets FastAPI answer a cleared Width box with a
    # 422 JSON body, which htmx (configured to swap 422s) would paste into #estimate.
    with db.session_scope(request.app.state.boot.session_factory) as s:
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
