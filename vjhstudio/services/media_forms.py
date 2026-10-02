"""What the Music & SFX, Speech and 3D forms show for a given model, how a posted form
becomes a request, and what it will cost. Pure dict logic over the model's harvested
``constraints.fields``; the routes and templates stay thin."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from ..models import CatalogModel
from ..runware import tasks_media
from ..schemas import media as media_schema
from . import catalog, constraints

# Opened on first visit when the catalog has them: the cheapest sensible model per kind.
PREFERRED = {
    "audio": "runware:ace-step@v1.5-turbo",
    "speech": "xai:tts@0",
    # Tripo, not the cheaper Hunyuan Rapid: the tab opens on "From a description", and
    # Hunyuan Rapid's text-to-3D has been failing at the provider (TEXT_3D_WARNINGS)
    "3d": "tripo:v3.1@0",
}
# Models whose text-to-3D path is known to fail at the provider, with what to tell the
# person about to wait for it. Shown under the description box; the job is still allowed,
# since an outage ends. Evidence for the entry is in tests/test_media_tasks.py.
TEXT_3D_WARNINGS = {
    "tencent:hunyuan-3d@3.1-rapid": (
        "Heads-up: building from a description has been failing on this model. The job is "
        "accepted, runs for about 16 minutes and ends in an error at the provider, and no "
        "other Hunyuan job can start meanwhile. Building from an image works here; for a "
        "description, Tripo 3D works."
    ),
}
FORMATS = {
    "audio": media_schema.AUDIO_FORMATS,
    "speech": media_schema.AUDIO_FORMATS,
    "3d": media_schema.MODEL3D_FORMATS,
}
# ``settings.*`` keys the forms place by hand; the rest land under "More settings".
PLACED = ("lyrics", "instrumental", "lyricsOptimizer")
_LABELS = {"bpm": "BPM", "pbr": "PBR materials", "hdTexture": "HD texture"}
_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")
_NUMERIC = ("integer", "float", "number")


SETTING_KEYS = {
    "audio": "defaults.audio_model",
    "speech": "defaults.speech_model",
    "3d": "defaults.model3d_model",
}


def default_air(kind: str, options: list[CatalogModel], chosen: str = "") -> str:
    """The model a tab opens on: the one picked in Settings when this install has it,
    else the built-in cheap sensible one, else a favourite, else the first offered."""
    airs = [m.air for m in options]
    if chosen and chosen in airs:
        return chosen
    if PREFERRED[kind] in airs:
        return PREFERRED[kind]
    favourite = next((m.air for m in options if m.is_favourite), None)
    return favourite or (airs[0] if airs else "")


def _spec(fields: dict, name: str) -> dict | None:
    spec = fields.get(name)
    return spec if isinstance(spec, dict) else None


def _label(key: str) -> str:
    return _LABELS.get(key) or _CAMEL.sub(r" \1", key).capitalize()


def extras(fields: dict) -> list[dict]:
    """The model's own one-level ``settings.*`` switches, choices and numbers, as form
    controls (``st_<key>``). Nested objects and anything untyped stay out: the
    extra-JSON box reaches those."""
    out: list[dict] = []
    for name, spec in fields.items():
        if not name.startswith("settings.") or not isinstance(spec, dict):
            continue
        key = name[len("settings.") :]
        if "." in key or key in PLACED:
            continue
        kind = str(spec.get("type") or "").lower()
        values = spec.get("values") if isinstance(spec.get("values"), list) else None
        if kind == "boolean":
            control = "bool"
        elif values:
            control = "choice"
        elif kind in _NUMERIC:
            control = "number"
        elif kind == "string":
            control = "text"
        else:
            continue
        out.append(
            {
                "key": key,
                "name": f"st_{key}",
                "label": _label(key),
                "control": control,
                "type": kind,
                "values": values or [],
                "min": spec.get("min"),
                "max": spec.get("max"),
                "step": spec.get("step") or ("any" if kind != "integer" else 1),
                "default": spec.get("default"),
            }
        )
    return out


VOCALS = ("instrumental", "lyrics", "auto")


def vocals_of(values: dict, stored: dict) -> str:
    """Which Vocals option the form opens on: the posted one, else what a stored
    request (a remix) says, else instrumental -- the choice that needs nothing more."""
    raw = str(values.get("vocals", "") or "").strip().lower()
    if raw in VOCALS:
        return raw
    if values.get("instrumental") is True:
        return "instrumental"
    if stored.get("lyricsOptimizer") is True:
        return "auto"
    if str(values.get("lyrics") or "").strip() or values.get("instrumental") is False:
        return "lyrics"
    return "instrumental"


def _range_text(spec: dict | None) -> str:
    spec = spec or {}
    lo, hi = spec.get("min"), spec.get("max")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        return f"{int(lo)}–{int(hi)}"
    return ""


def _tri_value(raw) -> str:
    """A stored ``True``/``False``/``None`` (or an already-posted string) as the value of
    the three-way select: on / off / leave it to the model."""
    if raw is True or str(raw).lower() in ("on", "true"):
        return "on"
    if raw is False or str(raw).lower() in ("off", "false"):
        return "off"
    return ""


def form_ctx(
    session: Session,
    kind: str,
    air: str,
    values: dict | None = None,
    errors: dict | None = None,
    image_assets: list[dict] | None = None,
) -> dict:
    """Everything ``generate/_media_params.html`` needs. A model with nothing harvested
    (``known`` false) still gets its required box plus the optional ones that are cheap
    to offer; RunWare's free rejection drops what it does not take."""
    m = catalog.get_by_air(session, air) if air else None
    c = m.constraints_json if m is not None else None
    caps = list((m.capabilities_json if m is not None else None) or [])
    fields = constraints.media_fields(c)
    known = bool(fields)
    values = dict(values or {})
    stored = values.get("settings") if isinstance(values.get("settings"), dict) else {}
    controls = extras(fields)
    for control in controls:
        raw = values.get(control["name"], stored.get(control["key"]))
        control["value"] = _tri_value(raw) if control["control"] == "bool" else raw
    listed = (_spec(fields, "outputFormat") or {}).get("values") or []
    formats = [f for f in FORMATS[kind] if f in listed] or list(FORMATS[kind])
    ctx: dict = {
        "kind": kind,
        "air": air,
        "model": m,
        "known": known,
        "values": values,
        "errors": errors or {},
        "formats": formats,
        "extras": controls,
        "has_seed": not known or "seed" in fields,
        # shown as a hint only: a seed outside the range is folded into it when the job
        # is queued (tasks_media.fold_seed), never refused
        "seed_range": _range_text(_spec(fields, "seed")),
        "has_negative": known and "negativePrompt" in fields,
    }
    if kind == "audio":
        prompt = _spec(fields, "positivePrompt") or {}
        ctx.update(
            prompt_max=prompt.get("max") or 3000,
            has_lyrics=not known or "settings.lyrics" in fields,
            lyrics_max=(_spec(fields, "settings.lyrics") or {}).get("max") or 3500,
            # A model with an instrumental switch (MiniMax Music) wants to be told how
            # the vocals are handled: without singing, with the lyrics typed here, or
            # with lyrics it writes itself. One select says it; ``parse`` turns it into
            # the request and ``tasks_media.resolve_audio`` enforces the model's rule.
            has_vocals_choice=known and "settings.instrumental" in fields,
            has_lyrics_writer=known and "settings.lyricsOptimizer" in fields,
            vocals=vocals_of(values, stored),
            # a model with nothing harvested may or may not take a length: offer an
            # optional box and let the builder/runner sort it out
            duration=_spec(fields, "duration") if known else {},
        )
    elif kind == "speech":
        voice = _spec(fields, "speech.voice") or {}
        language = _spec(fields, "speech.language") or {}
        text = _spec(fields, "speech.text") or {}
        ctx.update(
            text_max=text.get("max"),
            voices=[str(v) for v in voice.get("values") or []],
            voice_default=voice.get("default") or "",
            languages=[str(v) for v in language.get("values") or []],
            language_default=language.get("default") or "",
            has_language=not known or "speech.language" in fields,
            speed=_spec(fields, "speech.speed"),
        )
    else:
        inputs = (c or {}).get("inputs") if isinstance((c or {}).get("inputs"), dict) else {}
        image_required = any(
            isinstance(inputs.get(k), dict) and inputs[k].get("required")
            for k in ("image", "images")
        )
        takes_text = not caps or "io:text-to-3d" in caps
        takes_image = not caps or "io:image-to-3d" in caps
        prompt = _spec(fields, "positivePrompt") or {}
        takes_text = takes_text and not image_required
        takes_text = takes_text and not (known and "positivePrompt" not in fields)
        ctx.update(
            takes_text=takes_text,
            text_warning=TEXT_3D_WARNINGS.get(air, ""),
            takes_image=takes_image,
            # a model that does both gets a "From a description / From an image" choice;
            # only the chosen box is shown, and only it is sent (``parse``)
            choose_source=takes_text and takes_image,
            source=source_of(values),
            image_required=image_required or not takes_text,
            prompt_max=prompt.get("max") or 1000,
            image_assets=image_assets or [],
            # several views of one object for the models that take a list; the picked
            # ids in pick order, so a re-render keeps both the ticks and the order
            image_max=tasks_media.max_images({"constraints": c or {}}),
            picked=picked_images(values),
        )
    return ctx


def source_of(form) -> str:
    """``"text"`` or ``"image"``: what a 3D object is built from. The posted choice
    wins; without one (first render, a remix, a hand-made request) a picked image
    means image."""
    raw = str(form.get("source", "") or "").strip().lower()
    if raw in ("text", "image"):
        return raw
    return "image" if picked_images(form) else "text"


def picked_images(form) -> list[str]:
    """The picked image ids as strings, in the order they were picked. ``image_order``
    (kept up to date by app.js as boxes are ticked) says the order; the ticked boxes say
    which are still picked. Without the script the page order stands. A stored request
    (a remix) carries a plain list."""
    getlist = getattr(form, "getlist", None)
    raw = getlist("image_asset_ids") if getlist else form.get("image_asset_ids") or []
    if isinstance(raw, (str, int)):
        raw = [raw]
    ticked = [str(v).strip() for v in raw if str(v).strip()]
    single = str(form.get("image_asset_id", "") or "").strip()
    if single and single not in ticked:
        ticked.insert(0, single)
    order = [v.strip() for v in str(form.get("image_order", "") or "").split(",") if v.strip()]
    first = [v for v in order if v in ticked]
    return list(dict.fromkeys(first + [v for v in ticked if v not in first]))


# ---- a posted form -> a request -------------------------------------------------------
def _s(form, key: str) -> str:
    return str(form.get(key, "") or "").strip()


def _tri(raw: str) -> bool | None:
    return {"on": True, "off": False}.get(raw.lower())


def _number(raw: str, kind: str):
    try:
        return int(raw) if kind == "integer" else float(raw)
    except ValueError:
        return raw  # RunWare (or pydantic, for the named fields) says what is wrong with it


def posted_settings(form, fields: dict) -> dict:
    out: dict = {}
    for control in extras(fields):
        raw = _s(form, control["name"])
        if not raw:
            continue
        if control["control"] == "bool":
            value = _tri(raw)
            if value is not None:
                out[control["key"]] = value
        elif control["type"] in _NUMERIC:
            out[control["key"]] = _number(raw, control["type"])
        else:
            out[control["key"]] = raw
    return out


def _extra_json(form):
    raw = _s(form, "extra_json")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return raw  # a string where a dict belongs: pydantic reports it


def parse(kind: str, form, fields: dict) -> media_schema.MediaRequest:
    """Raises pydantic's ``ValidationError``. Blank optional boxes are left out, so the
    request's own defaults (and the model's) apply."""
    data: dict = {
        "project_id": _s(form, "project_id"),
        "model": _s(form, "model"),
        "settings": posted_settings(form, fields),
        "extra_json": _extra_json(form),
    }
    names = {
        "audio": ("prompt", "negative_prompt", "lyrics", "duration", "seed", "output_format"),
        "speech": ("text", "voice", "language", "speed", "seed", "output_format"),
        "3d": ("prompt", "negative_prompt", "seed", "output_format"),
    }[kind]
    skip: tuple[str, ...] = ()
    if kind == "3d" and _s(form, "source"):
        # the form shows one of the two boxes; whatever is left in the other one from
        # before the switch is not part of the request
        skip = ("prompt", "negative_prompt") if source_of(form) == "image" else ("images",)
    for name in names:
        value = _s(form, name)
        if value and name not in skip:
            data[name] = value
    if kind == "3d" and "images" not in skip:
        data["image_asset_ids"] = picked_images(form)
    if kind == "audio":
        vocals = _s(form, "vocals").lower()
        if vocals == "instrumental":
            data["instrumental"] = True
            data.pop("lyrics", None)  # left in the hidden box from before the switch
        elif vocals in ("lyrics", "auto"):
            data["instrumental"] = False
            if vocals == "auto":
                data["settings"]["lyricsOptimizer"] = True
        else:  # a model with no vocals choice, or a hand-made request
            data["instrumental"] = _tri(_s(form, "instrumental"))
        data.setdefault("prompt", "")
    elif kind == "speech":
        data.setdefault("text", "")
    return media_schema.REQUEST_FOR_KIND[kind](**data)


def posted_values(kind: str, form) -> dict:
    """The raw strings of a posted form, for re-rendering it after a validation error."""
    values: dict = {k: str(v) for k, v in form.items()}
    if kind == "3d":
        values["image_asset_ids"] = picked_images(form)  # a repeated field: keep them all
    return values


# ---- cost -------------------------------------------------------------------------------
def _labelled_rate(m: CatalogModel, word: str) -> float | None:
    for r in ((m.price_tiers_json or {}).get("rates")) or []:
        if (
            isinstance(r, dict)
            and r.get("unit") == "output"
            and word in str(r.get("label") or "").lower()
            and isinstance(r.get("amount"), (int, float))
        ):
            return float(r["amount"])
    return None


def estimate(session: Session, kind: str, air: str, form) -> dict:
    """``{"total": float | None, "note": str}``. ``None`` means "cannot say", with the
    note saying why; the real cost is whatever RunWare reports when the job finishes."""
    m = catalog.get_by_air(session, air) if air else None
    rate = m.price_primary if m is not None else None
    unit = m.price_unit if m is not None else None
    if m is None or rate is None:
        return {"total": None, "note": "no price for this model"}
    if unit == "per_second":
        spec = _spec(constraints.media_fields(m.constraints_json), "duration") or {}
        raw = _s(form, "duration") or spec.get("default")
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            return {"total": None, "note": f"${rate:.4f} per second; the model picks the length"}
        return {"total": rate * seconds, "note": f"{seconds:g}s × ${rate:.4f}/s"}
    if unit == "per_1k_chars":
        n = len(_s(form, "text"))
        return {"total": rate * n / 1000, "note": f"{n} characters × ${rate:.3f} per 1,000"}
    if kind == "3d":
        mode = source_of(form)
        labelled = _labelled_rate(m, mode)
        total = labelled if labelled is not None else float(rate)
        return {"total": total, "note": f"{mode}-to-3D; options such as PBR can add to it"}
    return {"total": float(rate), "note": "per generation"}
