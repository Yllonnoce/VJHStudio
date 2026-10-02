"""The Music & SFX, Speech and 3D tabs of the Generate page.

Image and Video share one Alpine-driven form (``routes/generate.py``); these three are
plain server-rendered forms on their own URLs inside the same page shell, because what
each model takes differs so much that the fields are re-rendered per model instead of
toggled in the browser."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ... import db
from ...runware import tasks_media
from ...schemas import media as media_schema
from ...services import assets as assets_svc
from ...services import catalog, constraints, generate, media_forms, projects
from ...services import outputs as outputs_svc
from ...services import prompts as prompts_svc
from ...services import settings as settings_svc
from .. import deps
from ..urls import asset_thumb_url
from .generate import _no_key, _submitted, _unsupported, error_map
from .jobs import panel_ctx

router = APIRouter()
KINDS = media_schema.MEDIA_KINDS
# (mode key, tab label, link) for all five tabs, in display order
TABS = (
    ("image", "Image", "/generate/image"),
    ("video", "Video", "/generate/video"),
    ("audio", "Music & SFX", "/generate/audio"),
    ("speech", "Speech", "/generate/speech"),
    ("3d", "3D", "/generate/3d"),
)
SUBMIT_LABELS = {"audio": "Generate audio", "speech": "Generate speech", "3d": "Generate 3D object"}
LEADS = {
    "audio": "Describe a piece of music or a sound effect.",
    "speech": "Type a text and pick a voice to read it.",
    "3d": "Describe an object, or pick an image to turn into one.",
}
PICKER_ASSETS = 24


def _image_assets(session) -> list[dict]:
    rows, _total = assets_svc.list_assets(session, kind="image", per_page=PICKER_ASSETS)
    return [
        {
            "id": a.id,
            "name": a.original_name,
            "thumb": asset_thumb_url(assets_svc.thumb_rel(a)) or "",
        }
        for a in rows
    ]


def _params(session, kind: str, air: str, values: dict | None = None, errors: dict | None = None):
    picks = _image_assets(session) if kind == "3d" else None
    return media_forms.form_ctx(session, kind, air, values, errors, picks)


def _remix_values(session, kind: str, remix: str) -> dict:
    """``?remix=<output id>`` -> the job's stored request, as form values."""
    if not remix:
        return {}
    try:
        output = outputs_svc.get(session, int(remix))
    except (TypeError, ValueError):
        output = None
    if output is None or output.kind != kind:
        raise HTTPException(status_code=404, detail="unknown output")
    data = outputs_svc.remix_request(output)
    if data.get("seed") is None:
        data.pop("seed", None)
    return data


def _prompt_values(session, kind: str, prompt: str) -> dict:
    """``?prompt=<id>`` -> a saved prompt of this kind, as form values."""
    if not prompt:
        return {}
    try:
        row = prompts_svc.get(session, int(prompt))
    except (TypeError, ValueError):
        row = None
    if row is None or row.kind != kind:
        raise HTTPException(status_code=404, detail="unknown prompt")
    return {**dict(row.form_json or {}), "project_id": row.project_id}


def _page(request: Request, kind: str, remix: str = "", prompt: str = ""):
    app = request.app
    with db.session_scope(app.state.boot.session_factory) as s:
        # one link, one source of truth: a remix wins over a saved prompt
        values = _remix_values(s, kind, remix) or _prompt_values(s, kind, prompt)
        models = catalog.list_generate_models(s, kind)
        chosen = settings_svc.get(s, media_forms.SETTING_KEYS[kind], app.state.env)
        air = str(values.get("model") or "") or media_forms.default_air(kind, models, chosen)
        params = _params(s, kind, air, values)
        ctx = {
            "mode": kind,
            # opened from a remix or a saved prompt: the browser's remembered draft
            # (app.js) must leave these values alone
            "prefilled": bool(values),
            "tabs": TABS,
            "lead": LEADS[kind],
            "submit_label": SUBMIT_LABELS[kind],
            "models": models,
            "labels": {m.air: catalog.label(m) for m in models},
            "selected": air,
            "selected_unsupported": _unsupported(s, air),
            "projects": projects.list_active(s),
            "selected_project": values.get("project_id"),
            "params": params,
            "estimate": media_forms.estimate(s, kind, air, values),
        }
    ctx.update(panel_ctx(request))
    ctx["oob"] = False  # the page already carries the header badge
    return deps.render(request, "pages/generate_media.html", ctx)


def _kind(raw: str) -> str:
    if raw not in KINDS:
        raise HTTPException(status_code=404, detail="unknown mode")
    return raw


def _submit(request: Request, kind: str, form):
    app = request.app
    no_key = _no_key(request)
    if no_key is not None:
        return no_key
    air = str(form.get("model", "") or "")
    factory = app.state.boot.session_factory
    with db.session_scope(factory) as s:
        m = catalog.get_by_air(s, air) if air else None
        fields = constraints.media_fields(m.constraints_json if m is not None else None)
    try:
        req = media_forms.parse(kind, form, fields)
        job = generate.enqueue_media(factory, app.state.paths, kind, req)
    except ValidationError as e:
        return _params_422(request, kind, air, form, error_map(e))
    except ValueError as e:
        key = "vocals" if str(e) == tasks_media.NEEDS_LYRICS else "form"
        return _params_422(request, kind, air, form, {key: str(e)})
    return _submitted(request, job)


def _params_422(request: Request, kind: str, air: str, form, errors: dict):
    # pydantic words a model-level validator as "Value error, …": show the sentence alone
    errors = {k: str(v).removeprefix("Value error, ") for k, v in errors.items()}
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ctx = _params(s, kind, air, media_forms.posted_values(kind, form), errors)
    r = deps.render(request, "generate/_media_params.html", {"params": ctx}, 422)
    r.headers["HX-Retarget"] = "#media-params"
    r.headers["HX-Reswap"] = "outerHTML"
    return r


# Three explicit routes per verb rather than ``/generate/{kind}``: a path parameter
# would also match ``/generate/image`` and ``/generate/video`` if the router order ever
# changed, and those belong to the Alpine form.
def _register(kind: str) -> None:
    @router.get(f"/generate/{kind}", name=f"generate_{kind}_page")
    def page(request: Request, remix: str = "", prompt: str = ""):
        return _page(request, kind, remix, prompt)

    @router.post(f"/generate/{kind}", name=f"submit_{kind}")
    def submit(request: Request, form: deps.Form):
        return _submit(request, kind, form)


for _k in KINDS:
    _register(_k)


@router.post("/hx/media/params")
def hx_media_params(request: Request, form: deps.Form):
    """The fields for the chosen model, keeping whatever was already typed."""
    kind = _kind(str(form.get("kind", "") or ""))
    air = str(form.get("model", "") or "")
    values = media_forms.posted_values(kind, form)
    with db.session_scope(request.app.state.boot.session_factory) as s:
        if kind == "speech":
            # a voice or language picked for the previous model means nothing to this
            # one (xAI's "eve" is not an Inworld voice): fall back to this model's own
            m = catalog.get_by_air(s, air) if air else None
            fields = constraints.media_fields(m.constraints_json if m is not None else None)
            for name in ("voice", "language"):
                listed = (fields.get(f"speech.{name}") or {}).get("values") or []
                if listed and values.get(name) not in [str(v) for v in listed]:
                    values.pop(name, None)
        ctx = _params(s, kind, air, values)
    return deps.render(request, "generate/_media_params.html", {"params": ctx})


@router.post("/hx/media/estimate")
def hx_media_estimate(request: Request, form: deps.Form):
    kind = _kind(str(form.get("kind", "") or ""))
    air = str(form.get("model", "") or "")
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ctx = media_forms.estimate(s, kind, air, form)
    return deps.render(request, "generate/_media_estimate.html", {"estimate": ctx})
