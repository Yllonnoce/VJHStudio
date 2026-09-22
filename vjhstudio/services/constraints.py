"""Per-model constraints: what a model accepts (sizes, durations, inputs), merged from the
docs page, RunWare's validation errors and real-job corrections. See the spec section
"Model constraints". Everything here is pure dict logic except ``store``."""

from __future__ import annotations

from vjhstudio.models import utcnow
from vjhstudio.models.catalog import CatalogModel
from vjhstudio.runware.sizes import (  # noqa: F401  (re-exported for callers)
    nearest_size_in,
    snap_to_rule,
)

Size = tuple[int, int]
PARAM_KEYS = ("duration", "fps", "steps", "strength", "CFGScale")
_ATTR_KEYS = ("type", "min", "max", "step", "default", "values")


def _dims(c: dict | None) -> dict:
    d = (c or {}).get("dims") or {}
    return d if isinstance(d, dict) else {}


def _label(w: int, h: int, labels: dict) -> str:
    name = labels.get(f"{w}x{h}")
    return f"{name} — {w}×{h}" if name else f"{w}×{h}"


def size_options(c: dict | None, kind: str, fallback: list[tuple[int, int, str]]) -> list[dict]:
    d = _dims(c)
    if d.get("mode") == "list":
        labels = d.get("labels") or {}
        return [
            {"w": int(w), "h": int(h), "label": _label(int(w), int(h), labels)}
            for w, h in d.get("list") or []
        ]
    if d.get("mode") == "rule":
        step = int(d.get("step") or 1)
        out, seen = [], set()
        for w, h, name in fallback:
            sw, sh = snap_to_rule(w, h, d)
            # A preset that needs more than a quarter-step of correction on either axis
            # isn't a good fit for this model's grid; the free width/height inputs (also
            # shown for rule mode) cover it instead of offering a misleading preset.
            if abs(w - sw) * 4 > step or abs(h - sh) * 4 > step:
                continue
            if (sw, sh) in seen:
                continue
            seen.add((sw, sh))
            out.append({"w": sw, "h": sh, "label": f"{name} — {sw}×{sh}"})
        return out
    return [{"w": w, "h": h, "label": f"{name} — {w}×{h}"} for w, h, name in fallback]


def nearest_size(c: dict | None, w: int, h: int) -> Size:
    return nearest_size_in(_dims(c), w, h)


def duration_spec(c: dict | None) -> dict:
    spec = (c or {}).get("duration") or {}
    if not isinstance(spec, dict):
        return {}
    if spec.get("values"):
        return {k: spec[k] for k in ("values", "default") if k in spec}
    return {k: spec[k] for k in ("min", "max", "step", "default") if k in spec}


def requires_input_video(c: dict | None) -> bool:
    video = ((c or {}).get("inputs") or {}).get("video") or {}
    return bool(isinstance(video, dict) and video.get("required"))


def can_start_from_text(capabilities: list[str]) -> bool:
    return "io:text-to-video" in (capabilities or []) or "io:text-to-image" in (capabilities or [])


def needs_first_frame(capabilities: list[str], c: dict | None) -> bool:
    caps = capabilities or []
    return "io:image-to-video" in caps and "io:text-to-video" not in caps


def is_generate_capable(kind: str, capabilities: list[str], c: dict | None) -> bool:
    if requires_input_video(c):
        return False
    if kind != "video":
        return True
    caps = capabilities or []
    if not caps:
        return True  # a search-added row with no tags: let the runner find out
    return "io:text-to-video" in caps or "io:image-to-video" in caps


def _docs_dims(docs: dict) -> dict | None:
    if docs.get("dims"):
        return {
            "mode": "list",
            "list": [[int(w), int(h)] for w, h in docs["dims"]],
            "labels": dict(docs.get("dim_labels") or {}),
        }
    width = (docs.get("params") or {}).get("width") or {}
    if width.get("min") and width.get("max"):
        return {
            "mode": "rule",
            "min": int(width["min"]),
            "max": int(width["max"]),
            "step": int(width.get("step") or 1),
        }
    return None


def merge_sources(existing: dict | None, *, docs: dict | None, api: dict | None, now: str) -> dict:
    out: dict = dict(existing or {})
    sources = dict(out.get("sources") or {"docs": None, "api": None, "observed": None})
    if docs:
        sources["docs"] = now
        for key in PARAM_KEYS:
            attrs = (docs.get("params") or {}).get(key)
            if attrs:
                out[key] = {k: attrs[k] for k in _ATTR_KEYS if k in attrs}
        if docs.get("inputs"):
            out["inputs"] = {**(out.get("inputs") or {}), **docs["inputs"]}
        dd = _docs_dims(docs)
        if dd:
            out["dims"] = dd
    if api:
        sources["api"] = now
        if api.get("params"):
            out["params"] = list(api["params"])
        if api.get("dims") and api["dims"].get("mode") != "unknown":
            dims = dict(api["dims"])
            if dims.get("mode") == "list":
                dims.setdefault("labels", (out.get("dims") or {}).get("labels") or {})
            out["dims"] = dims
        for path in api.get("missing") or []:
            if path.startswith("inputs."):
                out.setdefault("inputs", {})[path.split(".", 1)[1]] = {"required": True}
    out.setdefault("dims", {"mode": "unknown"})
    out["sources"] = sources
    return out


def observe_dims(existing: dict | None, dims: dict, now: str) -> dict:
    out = dict(existing or {})
    out["dims"] = dict(dims)
    sources = dict(out.get("sources") or {"docs": None, "api": None, "observed": None})
    sources["observed"] = now
    out["sources"] = sources
    return out


def store(session, model: CatalogModel, constraints: dict) -> None:
    model.constraints_json = constraints
    model.constraints_updated_at = utcnow()
    session.flush()
