# VJHStudio Phase 7 (Model constraints) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Learn every catalog model's accepted parameters, sizes, durations and required inputs for free (docs pages + RunWare's pre-billing validation errors), store them in the catalog, and make the Generate page, the pre-flight checks and the runner use them so jobs like "Kling 4K at 1280x720" and "Aleph with no input video" can no longer be submitted.

**Architecture:** Two pure adapters under `vjhstudio/runware/` (`docs_pages.py` parses the public docs HTML; `probe.py` sends the two guaranteed-rejected probes and parses RunWare's messages) feed one service (`services/constraints.py`) that merges the sources into `catalog_models.constraints_json`, exposes query helpers, and runs the harvest with a background state object for the Models page + a CLI. The Generate routes/templates, `services/generate` pre-flight and `runware/runner.py` read only through those helpers.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic (migration `0005`), httpx (docs fetch, `MockTransport` in tests), runware-sdk (`RunwareError`), Jinja2 + HTMX 2 + Pico, pytest + `tests/fakes/fake_runware.py`.

**Spec:** `docs/superpowers/specs/2026-09-20-runwarestudio-design.md` — section "Model constraints (Phase 7, added 2026-09-22)". Also relevant: "RunWare adapter" (fallback policy), "Model dropdowns", "UI design → Generate".

## Global Constraints

- Python ≥ 3.11; run everything with `uv run --frozen …`; `uv run --frozen pytest -q`, `uv run --frozen ruff check .`, `uv run --frozen ruff format --check .` must be clean before every commit.
- **Probe safety rules (spec, binding):** (1) the parameter probe always carries the unknown key `vjhProbe`; (2) the size probe is sent only when the parameter probe listed both `width` and `height`, always with `width: 1, height: 1`, prompt `"a red fox in a snowy forest"`, no `inputs`, nothing else; (3) no width/height in the list → no size probe; (4) the harvest reads the balance before and after and raises `ProbeBilledError` if it changed; (5) the harvest sends nothing else.
- Tests never touch the network: docs HTML comes from fixture files under `tests/fixtures/docs/`, the content API and docs fetches use `httpx.MockTransport`, RunWare calls use `tests/fakes/fake_runware.FakeRunware` with scripted `RunwareError`s.
- `runware/` and `services/` never import `web/`. DB-only route handlers are sync `def`; handlers that await network are `async def`.
- No CDN; templates extend the existing partial conventions (`#model-params` slot, 422 re-render with inline errors).
- Copy rules: user-facing sentences from the spec verbatim: `"This model edits an existing video. VJHStudio cannot supply one yet."`, `"This model needs a first-frame image. Add one under References."`, badge `"video-to-video only — not supported yet"`, badge `"needs a first frame"`, button `"Harvest constraints"`.
- Every commit message ends with the exact line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Windows scripts stay `.bat` only (no script changes expected in this phase).

---

## File map

| File | Responsibility |
|---|---|
| `migrations/versions/0005_phase7.py` | adds `constraints_json`, `constraints_updated_at` to `catalog_models` |
| `vjhstudio/models/catalog.py` | the two new columns |
| `vjhstudio/services/constraints.py` | constraint dict helpers (`size_options`, `nearest_size`, `duration_spec`, `requires_input_video`, `can_start_from_text`, `needs_first_frame`), `merge_sources`, `observe_dims`, `store`, `harvest`, `HarvestState` |
| `vjhstudio/runware/docs_pages.py` | `fetch_docs_html`, `parse_docs` (pure HTML parsing) |
| `vjhstudio/runware/probe.py` | message parsers + the two probes |
| `vjhstudio/runware/runner.py` | never drop width/height; size correction from the rejection text |
| `vjhstudio/services/jobs.py` | persist an observed size correction |
| `vjhstudio/services/generate.py` | pre-flight input checks |
| `vjhstudio/services/catalog.py` | `view()` carries `constraints`; `list_generate_models()` filter; `badge()` |
| `vjhstudio/web/routes/generate.py` | constraint-aware params contexts |
| `vjhstudio/web/templates/generate/_video_params.html`, `_model_params.html`, `catalog/_model_select.html` | constraint-aware inputs and badges |
| `vjhstudio/web/routes/catalog.py`, `templates/catalog/_harvest_status.html`, `_row.html`, `pages/models.html` | Harvest button, status poll, per-row badge |
| `vjhstudio/main.py` | `probe` CLI |
| `vjhstudio/data/curated_models.json` | version 3 with `constraints` blocks |
| `tests/test_constraints.py`, `tests/test_docs_pages.py`, `tests/test_probe.py`, `tests/test_runner.py`, `tests/test_generate_constraints.py`, `tests/test_catalog_harvest.py`, `tests/fixtures/docs/*.html` | tests |

---

### Task 1: Schema + constraint helpers

**Files:**
- Create: `migrations/versions/0005_phase7.py`, `vjhstudio/runware/sizes.py` (pure size math: `snap_to_rule(w, h, rule) -> Size`, `nearest_size_in(dims: dict, w, h) -> Size` where `dims` is the `{"mode": ...}` block), `vjhstudio/services/constraints.py` (imports both from `sizes.py`; `nearest_size(c, w, h)` delegates with `_dims(c)`), `tests/test_constraints.py`
- Modify: `vjhstudio/models/catalog.py`, `vjhstudio/services/catalog.py` (`view()`)

**Interfaces (produces):**
```python
# vjhstudio/services/constraints.py
Size = tuple[int, int]
def size_options(c: dict | None, kind: str, fallback: list[tuple[int, int, str]]) -> list[dict]
    # -> [{"w": 3840, "h": 2160, "label": "4K (16:9) — 3840×2160"}, ...]; list mode: exactly the list;
    #    rule mode: fallback presets that satisfy min/max/step; unknown: fallback as-is
def nearest_size(c: dict | None, w: int, h: int) -> Size          # list: nearest by aspect then area; rule: snapped; unknown: unchanged
def snap_to_rule(w: int, h: int, rule: dict) -> Size
def duration_spec(c: dict | None) -> dict                        # {"values": [...]} or {"min","max","step","default"} or {}
def requires_input_video(c: dict | None) -> bool
def can_start_from_text(capabilities: list[str]) -> bool         # "io:text-to-video" in caps
def needs_first_frame(capabilities: list[str], c: dict | None) -> bool  # video model with i2v but no t2v
def is_generate_capable(kind: str, capabilities: list[str], c: dict | None) -> bool
def merge_sources(existing: dict | None, *, docs: dict | None, api: dict | None, now: str) -> dict
def observe_dims(existing: dict | None, dims: dict, now: str) -> dict
def store(session, model: CatalogModel, constraints: dict) -> None   # sets constraints_json + constraints_updated_at
```
`catalog.view(m)` gains `"constraints": m.constraints_json or {}`.

- [ ] **Step 1: Model columns + migration**

`vjhstudio/models/catalog.py` (after `raw_json`):
```python
    constraints_json: Mapped[dict | None] = mapped_column(JSON)
    constraints_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
```
`migrations/versions/0005_phase7.py` (same header shape as `0004_phase5.py`, `revision = "0005"`, `down_revision = "0004"`):
```python
def upgrade() -> None:
    with op.batch_alter_table("catalog_models", schema=None) as batch_op:
        batch_op.add_column(sa.Column("constraints_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("constraints_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("catalog_models", schema=None) as batch_op:
        batch_op.drop_column("constraints_updated_at")
        batch_op.drop_column("constraints_json")
```
Run: `uv run --frozen pytest -q tests/test_migrations.py` → passes (the autogenerate-clean check must stay green).

- [ ] **Step 2: Failing tests** (`tests/test_constraints.py`):
```python
from vjhstudio.services import constraints as C

KLING = {"dims": {"mode": "list", "list": [[3840, 2160], [2160, 3840], [2880, 2880]],
                  "labels": {"3840x2160": "4K (16:9)"}},
         "duration": {"min": 3, "max": 15, "step": 1, "default": 5}}
LTX = {"dims": {"mode": "rule", "min": 128, "max": 2048, "step": 64},
       "duration": {"min": 1, "max": 20, "type": "float"}}
VEO = {"dims": {"mode": "unknown"}, "duration": {"values": [4, 6, 7, 8], "default": 4}}
ALEPH = {"params": ["positivePrompt", "inputs.video"], "inputs": {"video": {"required": True}}}
PRESETS = [(1280, 720, "720p"), (1920, 1080, "1080p"), (1000, 700, "odd")]


def test_size_options_list_mode_is_exactly_the_list_with_labels():
    opts = C.size_options(KLING, "video", PRESETS)
    assert [(o["w"], o["h"]) for o in opts] == [(3840, 2160), (2160, 3840), (2880, 2880)]
    assert opts[0]["label"] == "4K (16:9) — 3840×2160"
    assert opts[2]["label"] == "2880×2880"


def test_size_options_rule_mode_keeps_only_presets_that_fit():
    opts = C.size_options(LTX, "video", PRESETS)
    assert [(o["w"], o["h"]) for o in opts] == [(1280, 704), (1920, 1088)]  # snapped to 64


def test_size_options_unknown_returns_fallback():
    assert [(o["w"], o["h"]) for o in C.size_options(VEO, "video", PRESETS)] == [(1280, 720), (1920, 1080), (1000, 700)]
    assert C.size_options(None, "image", PRESETS)[0]["w"] == 1280


def test_nearest_size_prefers_aspect_then_area():
    assert C.nearest_size(KLING, 1280, 720) == (3840, 2160)
    assert C.nearest_size(KLING, 720, 1280) == (2160, 3840)
    assert C.nearest_size(KLING, 1000, 1000) == (2880, 2880)
    assert C.nearest_size(LTX, 1280, 720) == (1280, 704)
    assert C.nearest_size(LTX, 100, 5000) == (128, 2048)
    assert C.nearest_size(VEO, 1280, 720) == (1280, 720)


def test_duration_spec_shapes():
    assert C.duration_spec(VEO) == {"values": [4, 6, 7, 8], "default": 4}
    assert C.duration_spec(KLING) == {"min": 3, "max": 15, "step": 1, "default": 5}
    assert C.duration_spec(None) == {}


def test_input_requirements_and_capabilities():
    assert C.requires_input_video(ALEPH) is True
    assert C.requires_input_video(KLING) is False
    assert C.can_start_from_text(["io:text-to-video"]) is True
    assert C.needs_first_frame(["io:image-to-video"], None) is True
    assert C.needs_first_frame(["io:text-to-video", "io:image-to-video"], None) is False
    assert C.is_generate_capable("video", ["io:video-to-video", "op:edit"], None) is False
    assert C.is_generate_capable("video", ["io:image-to-video"], None) is True
    assert C.is_generate_capable("video", ["io:text-to-video"], ALEPH) is False  # required input video wins
    assert C.is_generate_capable("image", [], None) is True


def test_merge_precedence_api_over_docs_and_keys_never_invented():
    docs = {"params": {"width": {"min": 128, "max": 2048, "step": 64}, "duration": {"min": 1, "max": 20}},
            "dims": [], "dim_labels": {}, "inputs": {"frameImages": {"required": False, "max_items": 2}}}
    api = {"params": ["width", "height", "duration"], "dims": {"mode": "list", "list": [[1280, 704]]}, "missing": []}
    out = C.merge_sources(None, docs=docs, api=api, now="2026-09-22T10:00:00")
    assert out["dims"] == {"mode": "list", "list": [[1280, 704]], "labels": {}}
    assert out["params"] == ["width", "height", "duration"]
    assert out["duration"] == {"min": 1, "max": 20}
    assert out["inputs"] == {"frameImages": {"required": False, "max_items": 2}}
    assert out["sources"] == {"docs": "2026-09-22T10:00:00", "api": "2026-09-22T10:00:00", "observed": None}
    assert "fps" not in out and "steps" not in out


def test_merge_docs_only_uses_docs_dims_and_rule():
    docs = {"params": {"width": {"min": 128, "max": 2048, "step": 64}}, "dims": [], "dim_labels": {}, "inputs": {}}
    out = C.merge_sources(None, docs=docs, api=None, now="t")
    assert out["dims"] == {"mode": "rule", "min": 128, "max": 2048, "step": 64}
    docs2 = {"params": {}, "dims": [[3840, 2160]], "dim_labels": {"3840x2160": "4K"}, "inputs": {}}
    assert C.merge_sources(None, docs=docs2, api=None, now="t")["dims"] == {"mode": "list", "list": [[3840, 2160]], "labels": {"3840x2160": "4K"}}
    assert C.merge_sources(None, docs=None, api=None, now="t")["dims"] == {"mode": "unknown"}


def test_merge_api_missing_required_marks_inputs():
    api = {"params": ["positivePrompt"], "dims": None, "missing": ["inputs.video"]}
    out = C.merge_sources(None, docs=None, api=api, now="t")
    assert out["inputs"]["video"] == {"required": True}


def test_observe_dims_overrides_and_stamps():
    out = C.observe_dims(KLING, {"mode": "list", "list": [[1280, 720]]}, now="t2")
    assert out["dims"]["list"] == [[1280, 720]] and out["sources"]["observed"] == "t2"
    assert out["duration"] == KLING["duration"]
```
Run: `uv run --frozen pytest -q tests/test_constraints.py` → FAIL (module missing).

- [ ] **Step 3: Implement `vjhstudio/services/constraints.py`** (helpers only in this task; `harvest`/`HarvestState` come in Task 4):
```python
"""Per-model constraints: what a model accepts (sizes, durations, inputs), merged from the
docs page, RunWare's validation errors and real-job corrections. See the spec section
"Model constraints". Everything here is pure dict logic except ``store``."""
from __future__ import annotations

from vjhstudio.models.catalog import CatalogModel
from vjhstudio.runware.sizes import nearest_size_in, snap_to_rule  # noqa: F401  (re-exported for callers)
from vjhstudio.models import utcnow

Size = tuple[int, int]
PARAM_KEYS = ("duration", "fps", "steps", "strength", "CFGScale")
_ATTR_KEYS = ("type", "min", "max", "step", "default", "values")


def _dims(c: dict | None) -> dict:
    d = (c or {}).get("dims") or {}
    return d if isinstance(d, dict) else {}


def _label(w: int, h: int, labels: dict) -> str:
    name = labels.get(f"{w}x{h}")
    return f"{name} — {w}×{h}" if name else f"{w}×{h}"


# `vjhstudio/runware/sizes.py` (new, no project imports):
#
# def snap_to_rule(w: int, h: int, rule: dict) -> tuple[int, int]:
#     lo, hi, step = int(rule.get("min") or 1), int(rule.get("max") or 10**6), int(rule.get("step") or 1)
#     def one(v: int) -> int:
#         v = max(lo, min(hi, int(v)))
#         v = (v // step) * step
#         return v if v >= lo else ((lo + step - 1) // step) * step
#     return one(w), one(h)
#
# def nearest_size_in(dims: dict, w: int, h: int) -> tuple[int, int]:
#     d = dims or {}
#     if d.get("mode") == "list" and d.get("list"):
#         want = w / h if h else 1.0
#         return min(((int(a), int(b)) for a, b in d["list"]),
#                    key=lambda s: (abs((s[0] / s[1]) - want), abs(s[0] * s[1] - w * h)))
#     if d.get("mode") == "rule":
#         return snap_to_rule(w, h, d)
#     return int(w), int(h)


def size_options(c: dict | None, kind: str, fallback: list[tuple[int, int, str]]) -> list[dict]:
    d = _dims(c)
    if d.get("mode") == "list":
        labels = d.get("labels") or {}
        return [{"w": int(w), "h": int(h), "label": _label(int(w), int(h), labels)} for w, h in d.get("list") or []]
    if d.get("mode") == "rule":
        out, seen = [], set()
        for w, h, name in fallback:
            sw, sh = snap_to_rule(w, h, d)
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
        return {"mode": "list", "list": [[int(w), int(h)] for w, h in docs["dims"]], "labels": dict(docs.get("dim_labels") or {})}
    width = (docs.get("params") or {}).get("width") or {}
    if width.get("min") and width.get("max"):
        return {"mode": "rule", "min": int(width["min"]), "max": int(width["max"]), "step": int(width.get("step") or 1)}
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
```
(Check what `catalog.py` imports for `utcnow` and use the same import.) Add `"constraints": m.constraints_json or {}` to `catalog.view()`.

- [ ] **Step 4: Run** `uv run --frozen pytest -q tests/test_constraints.py tests/test_migrations.py tests/test_catalog*.py` → PASS; ruff clean.
- [ ] **Step 5: Commit** `feat(constraints): catalog constraint columns and helpers`.

---

### Task 2: Docs page adapter

**Files:**
- Create: `vjhstudio/runware/docs_pages.py`, `tests/test_docs_pages.py`, `tests/fixtures/docs/kling-4k.html`, `tests/fixtures/docs/ltx-2-3.html`, `tests/fixtures/docs/veo-3-1.html`, `tests/fixtures/docs/aleph-2-0.html`

**Interfaces (produces):**
```python
DOCS_BASE = "https://runware.ai/docs/models/"
USER_AGENT = "Mozilla/5.0 (compatible; VJHStudio/0.3; +https://github.com/yllonnoce/VJHStudio)"
async def fetch_docs_html(slug: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0) -> str | None   # None on 404; raises DocsError on other failures
def parse_docs(html: str) -> dict
# {"params": {"width": {"type": "integer", "required": True, "min": 128, "max": 2048, "step": 64},
#             "duration": {"type": "integer", "default": 4, "values": [4, 6, 7, 8]}, ...},
#  "inputs": {"video": {"required": True}, "frameImages": {"required": False, "min_items": 1, "max_items": 2}},
#  "dims": [[3840, 2160], [2160, 3840], [2880, 2880]], "dim_labels": {"3840x2160": "4K (16:9)", ...}}
```
Numbers are ints when integral, floats otherwise (`"step: 0.01"` → `0.01`); `required` is `True` for both `required` and `required*`; `inputs.<name>` rows go under `"inputs"` with the `inputs.` prefix stripped; `items: 1`/`min items: 1`/`max items: 2` → `min_items`/`max_items`.

- [ ] **Step 1: Fixtures.** Build each fixture by hand as a *trimmed* HTML file (≤ 6 KB) that mirrors the real markup exactly (the real pages are ~750 KB). Use these snippets as the source of truth for the markup:
```html
<dl class="component-APIParameter level-0" id="request-width" data-rw-cid-uix52tx2><dt><div class="header"><h3><a href="#request-width">width</a></h3><div class="attributes"><span data-name="type">integer</span><span data-name="required">required</span><span data-name="min">min: 128</span><span data-name="max">max: 2048</span><span data-name="step">step: 64</span></div></div></dt><dd><p class="description">Width of the generated media in pixels.</p></dd></dl>
<dl class="component-APIParameter level-0" id="request-height"><dt><div class="header"><h3><a href="#request-height">height</a></h3><div class="attributes"><span data-name="type">integer</span><span data-name="required">required*</span><span data-name="paired">paired with width</span></div></div></dt><dd><p class="description">Height of the generated media in pixels.</p></dd></dl>
<dl class="component-APIParameter level-0" id="request-duration"><dt><div class="header"><h3><a href="#request-duration">duration</a></h3><div class="attributes"><span data-name="type">integer</span><span data-name="default">default: 4</span></div></div></dt><dd><p class="description">Length of the generated video in seconds.</p><details class="component-APIParameterDisclosure"><summary><span class="title">Allowed values</span><span class="badge">4 values</span></summary><div class="content"><ul><li><code>4</code></li><li><code>6</code></li><li><code>7</code></li><li><code>8</code></li></ul></div></details></dd></dl>
<dl class="component-APIParameter level-1" id="request-inputs.video"><dt><div class="header"><h3><a href="#request-inputs.video">video</a></h3><div class="attributes"><span data-name="type">string</span><span data-name="required">required</span></div></div></dt><dd><p class="description">Input video.</p></dd></dl>
<dl class="component-APIParameter level-1" id="request-inputs.frameImages"><dt><div class="header"><h3><a href="#request-inputs.frameImages">frameImages</a></h3><div class="attributes"><span data-name="type">array of strings or objects</span><span data-name="items">min items: 1</span><span data-name="items">max items: 2</span></div></div></dt><dd><p class="description">Frame images.</p></dd></dl>
<section class="component-SpecsCard" id="dimensions"><header><h3>Dimensions</h3></header><div class="content"><div class="component-ModelDimensions"><p>The following dimension combinations are supported:</p><div class="component-Table"><table><thead><tr><th>Configuration</th><th>Dimensions</th></tr></thead><tbody>
<tr><td><code class="dimension-value">4K (16:9)</code></td><td><code class="dimension-value">3840x2160</code></td></tr>
<tr><td><code class="dimension-value">4K (9:16)</code></td><td><code class="dimension-value">2160x3840</code></td></tr>
<tr><td><code class="dimension-value">4K (1:1)</code></td><td><code class="dimension-value">2880x2880</code></td></tr>
</tbody></table></div></div></div></section>
```
`kling-4k.html`: width/height `required*` + paired, duration `min: 3 / max: 15 / step: 1 / default: 5`, the Dimensions table above, plus a `providerSettings.klingai.sound` row (`<span data-name="type">boolean</span>`), and a stray `512x512` in a footer paragraph (must be ignored). `ltx-2-3.html`: width/height with min/max/step 64, duration `float / required / min: 1 / max: 20`, fps `integer / min: 1 / max: 120 / default: 25`, steps `min: 1 / max: 100 / default: 20`, no Dimensions table. `veo-3-1.html`: width/height `required*`, duration with the Allowed values block (4, 6, 7, 8), an `inputs.video` row **without** `required`, a Dimensions table with 1280x720/720x1280/1920x1080/1080x1920 (labels "720p (16:9)" etc.). `aleph-2-0.html`: `inputs.video` required, `inputs.frameImages` with min/max items, no width/height/duration rows.

- [ ] **Step 2: Failing tests** (`tests/test_docs_pages.py`):
```python
from pathlib import Path

import httpx
import pytest

from vjhstudio.runware import docs_pages as D

FIX = Path(__file__).parent / "fixtures" / "docs"


def load(name: str) -> dict:
    return D.parse_docs((FIX / name).read_text(encoding="utf-8"))


def test_kling_dims_table_and_duration_rule():
    d = load("kling-4k.html")
    assert d["dims"] == [[3840, 2160], [2160, 3840], [2880, 2880]]
    assert d["dim_labels"]["3840x2160"] == "4K (16:9)"
    assert d["params"]["width"] == {"type": "integer", "required": True}
    assert d["params"]["duration"] == {"type": "integer", "min": 3, "max": 15, "step": 1, "default": 5}
    assert "providerSettings.klingai.sound" in d["params"]
    assert [512, 512] not in d["dims"]


def test_ltx_rule_and_floats():
    d = load("ltx-2-3.html")
    assert d["dims"] == []
    assert d["params"]["width"] == {"type": "integer", "required": True, "min": 128, "max": 2048, "step": 64}
    assert d["params"]["duration"] == {"type": "float", "required": True, "min": 1, "max": 20}
    assert d["params"]["fps"]["default"] == 25


def test_veo_allowed_values_and_optional_input():
    d = load("veo-3-1.html")
    assert d["params"]["duration"] == {"type": "integer", "default": 4, "values": [4, 6, 7, 8]}
    assert d["inputs"]["video"] == {"required": False}
    assert [1280, 720] in d["dims"] and d["dim_labels"]["1280x720"].startswith("720p")


def test_aleph_inputs():
    d = load("aleph-2-0.html")
    assert d["inputs"]["video"] == {"required": True}
    assert d["inputs"]["frameImages"] == {"required": False, "min_items": 1, "max_items": 2}
    assert "width" not in d["params"]


def test_parse_garbage_is_empty_not_error():
    assert D.parse_docs("<html><body>nothing</body></html>") == {"params": {}, "inputs": {}, "dims": [], "dim_labels": {}}


@pytest.mark.asyncio
async def test_fetch_uses_browser_ua_and_maps_404_to_none():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["ua"] = req.headers.get("user-agent")
        seen["url"] = str(req.url)
        return httpx.Response(404 if "missing" in str(req.url) else 200, text="<html></html>")

    t = httpx.MockTransport(handler)
    assert await D.fetch_docs_html("google-veo-3-1", transport=t) == "<html></html>"
    assert seen["url"] == "https://runware.ai/docs/models/google-veo-3-1"
    assert seen["ua"].startswith("Mozilla/5.0")
    assert await D.fetch_docs_html("missing-model", transport=t) is None


@pytest.mark.asyncio
async def test_fetch_raises_docs_error_on_server_failure():
    t = httpx.MockTransport(lambda r: httpx.Response(503))
    with pytest.raises(D.DocsError):
        await D.fetch_docs_html("x", transport=t)
```
Run → FAIL (module missing).

- [ ] **Step 3: Implement `vjhstudio/runware/docs_pages.py`** with `html.parser`-free regex parsing (the markup is machine-generated and stable):
```python
"""The public docs page for a model (https://runware.ai/docs/models/<slug>) is the only
free source for enumerated durations, dimension tables and per-parameter ranges. This is
a pure parser plus a fetch helper; nothing here needs an API key."""
from __future__ import annotations

import html as htmllib
import re

import httpx

DOCS_BASE = "https://runware.ai/docs/models/"
USER_AGENT = "Mozilla/5.0 (compatible; VJHStudio/0.3; +https://github.com/yllonnoce/VJHStudio)"
_PARAM = re.compile(r'<dl class="component-APIParameter[^"]*" id="request-([^"]+)"[^>]*>(.*?)</dl>', re.S)
_ATTR = re.compile(r'<span data-name="([^"]+)"[^>]*>([^<]*)</span>')
_ALLOWED = re.compile(r"Allowed values.*?<ul>(.*?)</ul>", re.S)
_CODE = re.compile(r"<code[^>]*>([^<]*)</code>")
_DIMS_BLOCK = re.compile(r'class="component-ModelDimensions".*?</table>', re.S)
_DIM_CELL = re.compile(r'<code class="dimension-value"[^>]*>([^<]*)</code>')
_WXH = re.compile(r"^(\d{2,5})x(\d{2,5})$")
EMPTY = {"params": {}, "inputs": {}, "dims": [], "dim_labels": {}}


class DocsError(RuntimeError):
    pass


def _num(text: str) -> int | float | str:
    t = text.strip()
    try:
        f = float(t)
    except ValueError:
        return t
    return int(f) if f.is_integer() else f


def _attrs(body: str) -> dict:
    out: dict = {}
    for name, raw in _ATTR.findall(body):
        val = htmllib.unescape(raw).strip()
        if name == "type":
            out["type"] = val
        elif name == "required":
            out["required"] = True
        elif name in ("min", "max", "step", "default"):
            out[name] = _num(val.split(":", 1)[1]) if ":" in val else _num(val)
        elif name == "items":
            m = re.match(r"(min|max)?\s*items:\s*(\d+)", val)
            if m:
                out[f"{m.group(1) or 'min'}_items"] = int(m.group(2))
    m = _ALLOWED.search(body)
    if m:
        vals = [_num(htmllib.unescape(c)) for c in _CODE.findall(m.group(1))]
        if vals:
            out["values"] = vals
    return out


def parse_docs(html: str) -> dict:
    params: dict = {}
    inputs: dict = {}
    for name, body in _PARAM.findall(html):
        attrs = _attrs(body)
        if name.startswith("inputs."):
            inputs[name.split(".", 1)[1]] = {"required": bool(attrs.get("required")), **{k: v for k, v in attrs.items() if k in ("min_items", "max_items")}}
        else:
            params[name] = attrs
    dims: list[list[int]] = []
    labels: dict[str, str] = {}
    block = _DIMS_BLOCK.search(html)
    if block:
        cells = [htmllib.unescape(c).strip() for c in _DIM_CELL.findall(block.group(0))]
        pending: str | None = None
        for cell in cells:
            m = _WXH.match(cell.replace("×", "x"))
            if m:
                pair = [int(m.group(1)), int(m.group(2))]
                if pair not in dims:
                    dims.append(pair)
                if pending:
                    labels[f"{pair[0]}x{pair[1]}"] = pending
                pending = None
            else:
                pending = cell
    if not params and not inputs and not dims:
        return {"params": {}, "inputs": {}, "dims": [], "dim_labels": {}}
    return {"params": params, "inputs": inputs, "dims": dims, "dim_labels": labels}


async def fetch_docs_html(slug: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0) -> str | None:
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(DOCS_BASE + slug)
    except httpx.HTTPError as e:
        raise DocsError(f"docs page unreachable: {e}") from e
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise DocsError(f"docs page {r.status_code} for {slug}")
    return r.text
```
- [ ] **Step 4: Run** the file's tests → PASS. Also run the parser once against a real saved page if available (`/tmp/doc-klingai-video-3-0-4k.html` may exist on the dev box) and note the result in the report; not a test.
- [ ] **Step 5: Commit** `feat(constraints): docs page parser`.

---

### Task 3: API probe adapter

**Files:**
- Create: `vjhstudio/runware/probe.py`, `tests/test_probe.py`

**Interfaces (produces):**
```python
PROBE_KEY = "vjhProbe"
PROBE_PROMPT = "a red fox in a snowy forest"
class ProbeBilledError(RuntimeError): ...
def task_type(kind: str) -> str                       # "imageInference" | "videoInference"
def parse_allowed_params(message: str) -> list[str]   # from "Allowed values are: 'a', 'b'"
def parse_supported_dims(message: str) -> dict        # {"mode":"list","list":[[w,h],...]} | {"mode":"rule","min","max","step"} | {"mode":"unknown"}
def parse_missing_required(message: str) -> str | None
@dataclass(frozen=True) class ProbeResult: params: list[str]; dims: dict | None; missing: list[str]; errors: list[str]
async def probe_model(client, air: str, kind: str) -> ProbeResult   # the two probes, safety rules enforced
```

- [ ] **Step 1: Failing tests** (`tests/test_probe.py`):
```python
import pytest
from runware import RunwareError

from tests.fakes.fake_runware import FakeRunware
from vjhstudio.runware import probe as P

ALLOWED = ("Unsupported use of 'vjhProbe' parameter. This parameter is not supported for the selected model. "
           "Allowed values are: 'includeCost', 'taskUUID', 'taskType', 'model', 'height', 'width', 'outputType', "
           "'positivePrompt', 'duration', 'providerSettings', 'fps', 'inputs.frameImages'.")
KLING = ("Unsupported use of width/height parameters. The specified dimensions are not supported for the kling "
         "video 3.0 4k model. Supported values are: '3840x2160', '2160x3840', '2880x2880'.")
WAN = "Unsupported use of width/height parameters. … Supported values are: '1280*720', '720*1280', '960*960'."
LTX = "Invalid value for 'width' parameter. Video width must be an integer value between 128 and 2048, in multiples of 64."
VEO = "Unsupported width/height combination for this model architecture."
ALEPH_ALLOWED = ("Unsupported use of 'vjhProbe' parameter. This parameter is not supported for the selected model. "
                 "Allowed values are: 'includeCost', 'taskUUID', 'taskType', 'model', 'positivePrompt', 'inputs.video'.")


def test_parse_allowed_params():
    assert P.parse_allowed_params(ALLOWED)[:6] == ["includeCost", "taskUUID", "taskType", "model", "height", "width"]
    assert P.parse_allowed_params("no list here") == []


def test_parse_supported_dims_shapes():
    assert P.parse_supported_dims(KLING) == {"mode": "list", "list": [[3840, 2160], [2160, 3840], [2880, 2880]]}
    assert P.parse_supported_dims(WAN) == {"mode": "list", "list": [[1280, 720], [720, 1280], [960, 960]]}
    assert P.parse_supported_dims(LTX) == {"mode": "rule", "min": 128, "max": 2048, "step": 64}
    assert P.parse_supported_dims(VEO) == {"mode": "unknown"}
    assert P.parse_supported_dims("Missing required parameter: 'width/height'.") == {"mode": "unknown"}


def test_parse_missing_required():
    assert P.parse_missing_required("Missing required parameter: 'inputs.video'.") == "inputs.video"
    assert P.parse_missing_required(KLING) is None


def _err(msg, param=None):
    e = RunwareError("validation", msg)
    e.parameter = param
    return e


@pytest.mark.asyncio
async def test_probe_model_sends_two_guaranteed_rejected_requests():
    fake = FakeRunware({"run": [_err(ALLOWED, "vjhProbe"), _err(KLING, "width")]})
    res = await P.probe_model(fake, "klingai:kling-video@3-4k", "video")
    first, second = [p for name, p in fake.calls if name == "run"]
    assert first[P.PROBE_KEY] == 1 and first["taskType"] == "videoInference" and first["model"] == "klingai:kling-video@3-4k"
    assert len(first["positivePrompt"]) >= 3 and "inputs" not in first
    assert second == {**{k: second[k] for k in ("taskType", "taskUUID", "model")}, "positivePrompt": P.PROBE_PROMPT, "width": 1, "height": 1}
    assert second["taskUUID"] != first["taskUUID"]
    assert res.params[:4] == ["includeCost", "taskUUID", "taskType", "model"]
    assert res.dims == {"mode": "list", "list": [[3840, 2160], [2160, 3840], [2880, 2880]]}
    assert res.missing == [] and res.errors == []


@pytest.mark.asyncio
async def test_probe_skips_size_probe_when_model_has_no_width_height():
    fake = FakeRunware({"run": [_err(ALEPH_ALLOWED, "vjhProbe")]})
    res = await P.probe_model(fake, "runway:aleph@2.0", "video")
    assert len([1 for n, _ in fake.calls if n == "run"]) == 1
    assert res.dims is None and "inputs.video" in res.params


@pytest.mark.asyncio
async def test_probe_records_missing_required_from_size_probe():
    fake = FakeRunware({"run": [_err(ALLOWED, "vjhProbe"), _err("Missing required parameter: 'inputs.video'.", "inputs.video")]})
    res = await P.probe_model(fake, "x:y@1", "video")
    assert res.missing == ["inputs.video"] and res.dims == {"mode": "unknown"}


@pytest.mark.asyncio
async def test_probe_treats_an_accepted_request_as_billing_and_raises():
    fake = FakeRunware({"run": [[{"taskType": "videoInference", "videoURL": "http://x"}]]})
    with pytest.raises(P.ProbeBilledError):
        await P.probe_model(fake, "x:y@1", "video")


@pytest.mark.asyncio
async def test_probe_collects_non_validation_errors_instead_of_raising():
    fake = FakeRunware({"run": [RunwareError("connection", "offline")]})
    res = await P.probe_model(fake, "x:y@1", "image")
    assert res.params == [] and res.dims is None and res.errors == ["connection: offline"]
```
Run → FAIL.

- [ ] **Step 2: Implement `vjhstudio/runware/probe.py`**:
```python
"""Free constraint discovery. RunWare validates a request before it bills; these two
requests are built so they can never pass validation (see the spec's probe safety rules):
1) an unknown key -> the model's allowed parameter list; 2) width=height=1 (only when the
model takes width/height) -> its supported sizes or size rule. Anything accepted is a bug
and is raised as ProbeBilledError so the caller stops immediately."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from runware import RunOptions, RunwareError

PROBE_KEY = "vjhProbe"
PROBE_PROMPT = "a red fox in a snowy forest"
_QUOTED = re.compile(r"'([^']+)'")
_ALLOWED = re.compile(r"Allowed values are:\s*(.*)$", re.I | re.S)
_SUPPORTED = re.compile(r"Supported values are:\s*(.*)$", re.I | re.S)
_PAIR = re.compile(r"^(\d{2,5})\s*[x×*]\s*(\d{2,5})$")
_RULE = re.compile(r"between\s+(\d+)\s+and\s+(\d+)(?:,?\s+in multiples of\s+'?(\d+)'?)?", re.I)
_MISSING = re.compile(r"Missing required parameter:\s*'([^']+)'", re.I)


class ProbeBilledError(RuntimeError):
    """A probe was accepted, which means it was (or will be) billed."""


def task_type(kind: str) -> str:
    return "videoInference" if kind == "video" else "imageInference"


def parse_allowed_params(message: str) -> list[str]:
    m = _ALLOWED.search(message or "")
    return _QUOTED.findall(m.group(1)) if m else []


def parse_supported_dims(message: str) -> dict:
    msg = message or ""
    m = _SUPPORTED.search(msg)
    if m:
        pairs = []
        for token in _QUOTED.findall(m.group(1)):
            pm = _PAIR.match(token.strip())
            if pm:
                pairs.append([int(pm.group(1)), int(pm.group(2))])
        if pairs:
            return {"mode": "list", "list": pairs}
    r = _RULE.search(msg)
    if r:
        return {"mode": "rule", "min": int(r.group(1)), "max": int(r.group(2)), "step": int(r.group(3) or 1)}
    return {"mode": "unknown"}


def parse_missing_required(message: str) -> str | None:
    m = _MISSING.search(message or "")
    return m.group(1) if m else None


@dataclass(frozen=True)
class ProbeResult:
    params: list[str] = field(default_factory=list)
    dims: dict | None = None
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _base(air: str, kind: str) -> dict:
    return {"taskType": task_type(kind), "taskUUID": str(uuid.uuid4()), "model": air, "positivePrompt": PROBE_PROMPT}


async def _send(client, task: dict) -> RunwareError:
    """Returns the rejection. An accepted request is a safety failure."""
    try:
        await client.run(task, RunOptions(timeout=30_000, validate=False))
    except RunwareError as e:
        return e
    raise ProbeBilledError(f"probe for {task.get('model')} was accepted; stop and check the RunWare balance")


async def probe_model(client, air: str, kind: str) -> ProbeResult:
    errors: list[str] = []
    e1 = await _send(client, {**_base(air, kind), PROBE_KEY: 1})
    if e1.code != "validation":
        return ProbeResult(errors=[f"{e1.code}: {e1.message}"])
    params = parse_allowed_params(e1.message or "")
    if not params:
        errors.append(f"unexpected reply to the parameter probe: {e1.message}")
    if "width" not in params or "height" not in params:
        return ProbeResult(params=params, dims=None, errors=errors)  # safety rule 3
    e2 = await _send(client, {**_base(air, kind), "width": 1, "height": 1})
    if e2.code != "validation":
        return ProbeResult(params=params, dims=None, errors=errors + [f"{e2.code}: {e2.message}"])
    missing = [m] if (m := parse_missing_required(e2.message or "")) else []
    return ProbeResult(params=params, dims=parse_supported_dims(e2.message or ""), missing=missing, errors=errors)
```
- [ ] **Step 3: Run** → PASS; ruff clean (the walrus in a list literal is fine; if ruff complains, split it).
- [ ] **Step 4: Commit** `feat(constraints): RunWare validation probes`.

---

### Task 4: Harvest service, CLI and Models page button

**Files:**
- Modify: `vjhstudio/services/constraints.py` (add `HarvestState`, `STATE`, `harvest`, `start_harvest`), `vjhstudio/main.py` (`probe` command), `vjhstudio/web/routes/catalog.py`, `vjhstudio/web/templates/pages/models.html` (or wherever `_refresh_status.html` is included), `vjhstudio/web/templates/catalog/_row.html`
- Create: `vjhstudio/web/templates/catalog/_harvest_status.html`, `tests/test_catalog_harvest.py`
- Test also: `tests/test_cli.py` (one `probe --help` smoke)

**Interfaces (consumes):** `docs_pages.fetch_docs_html/parse_docs`, `probe.probe_model/ProbeBilledError`, `constraints.merge_sources/store`, `account.parse_balance` (existing, `services/account.py`), `catalog.list_models`, `client_factory` from `app.state` (same object the JobRunner uses: `request.app.state.runner.client_factory`, an async context manager `client_factory(api_key, transport)`), `secrets.effective_api_key(paths, env)` (check the exact name in `vjhstudio/secrets.py`).

**Interfaces (produces):**
```python
@dataclass class HarvestState:  running: bool; total: int; done: int; ok: int; message: str; started_at; finished_at; rows: list[dict]  # rows: {"air","name","docs","api","dims_mode"}
    def snapshot(self) -> dict
STATE = HarvestState()
async def harvest(session_factory, *, client_factory, api_key: str, kinds=("video","image"), airs: list[str] | None = None,
                  docs: bool = True, api: bool = True, docs_transport=None, state: HarvestState | None = None,
                  docs_concurrency: int = 5, api_concurrency: int = 3) -> HarvestState
def start_harvest(app_state, **kwargs) -> bool   # creates the asyncio task on app.state.harvest_task; False if already running
```
Behaviour: with `api=True` and no API key → `state.message = "Add your RunWare API key in Settings to run the API probes; docs pages only."` and only docs run. Balance check: read `account_management getDetails` before and after the API probes through the same client; if the amounts differ, set `state.message` to `"STOPPED: the balance changed during the harvest (before $X, after $Y). Nothing more was sent. Please report this."` and stop. `ProbeBilledError` from any model also stops the whole harvest with that message. Every other error is per-row (`docs: "error: …"`, `api: "error: …"`) and the loop continues. Rows are processed in catalog order; `state.done` increments per model.

- [ ] **Step 1: Failing tests** (`tests/test_catalog_harvest.py`) — use the `app`/`client` fixtures and a `FakeRunware` scripted per model, plus `httpx.MockTransport` serving the Task 2 fixtures by slug. Cover: (a) `harvest()` on two seeded models (a video row whose slug maps to `kling-4k.html` and an image row with no docs → 404) stores `constraints_json` with `dims.mode == "list"` for Kling and `sources.api` set; the 404 row gets `docs: "missing"`; (b) with `api_key=""` only docs run and `fake.calls` is empty; (c) a `ProbeBilledError` stops the loop after the first model (`state.rows` has one entry, `message` starts with `"STOPPED"`); (d) balance change (script `account_management` replies `[{"balance": 10.0}]` then `[{"balance": 9.0}]`) → message starts with `"STOPPED: the balance changed"`; (e) routes: `POST /models/harvest` → 202/200 with the status partial containing `Harvesting…`, `GET /hx/models/harvest-status` polls (`hx-trigger="every 2s"` present while running, absent when finished), a second POST while running → 409; `GET /models` contains the text `Harvest constraints`; a row with `dims.mode == "list"` shows the badge `sizes known` and an Aleph-like row (`inputs.video.required`) shows `video-to-video only — not supported yet`; (f) `uv run vjhstudio probe --help` exits 0 (subprocess or `main.build_parser`).
- [ ] **Step 2: Implement** `harvest` (asyncio gather with two semaphores; docs fetch for every row with a `slug`, api probes through one client opened once for the whole run), `HarvestState` (mirror `update.UpdateState`'s lock + `snapshot()`), `start_harvest`. Route module additions (`routes/catalog.py`): `POST /models/harvest` (`_local_only`, `async def`, `start_harvest(...)` → renders `_harvest_status.html`; 409 + partial when running), `GET /hx/models/harvest-status` (sync, renders the partial from `STATE.snapshot()`). Partial `_harvest_status.html`: a `<form id="harvest-form" hx-post="/models/harvest" hx-target="this" hx-swap="outerHTML">` with the button `Harvest constraints`, a `<small>` saying `Free: reads each model's docs page and asks RunWare which sizes it accepts. Nothing is generated.`, and while running a `<div hx-get="/hx/models/harvest-status" hx-trigger="every 2s" hx-swap="outerHTML">Harvesting… {done}/{total}</div>`; when finished, `message` + a `<details>` table of rows (air, docs, api, dims_mode). Row badge in `_row.html`: `{% set c = m.constraints_json or {} %}` → `<small class="badge">sizes known</small>` when `c.dims.mode in ("list","rule")`, `<small class="badge warn">video-to-video only — not supported yet</small>` when `not constraints.is_generate_capable(m.kind, m.capabilities_json, c)` (expose the helper through `deps.render_globals` or pass a precomputed flag in `_rows()` — prefer computing `generate_capable` in `_rows()`). CLI: `probe` subparser with `--kind {image,video}` (repeatable), `--air` (repeatable), `--no-docs`, `--no-api`; prints one line per row and the final message; exit 1 on `STOPPED`.
- [ ] **Step 3: Run** full suite → PASS; ruff clean.
- [ ] **Step 4: Commit** `feat(constraints): harvest service, Models page button and probe CLI`.

---

### Task 5: Runner size correction

**Files:**
- Modify: `vjhstudio/runware/runner.py`, `vjhstudio/services/jobs.py`, `tests/test_runner.py`, `tests/test_jobs.py` (or wherever job persistence is tested)

**Interfaces (produces):**
```python
# runner.py
PAIR = ("width", "height")
def size_correction(err: BaseException, task: dict) -> tuple[dict, dict] | None
    # parses probe.parse_supported_dims(message); list → constraints.nearest_size; rule → snap; returns (new_task, record) or None
    # record: {"field": "width/height", "action": "corrected", "from": [w,h], "to": [w2,h2], "dims": <parsed dims dict>}
# rejected_field(): returns None for "width"/"height" (never dropped)
# jobs.py: after a successful run, for each rec with action == "corrected": constraints.observe_dims + store on the model row
```
`runner.py` may import `vjhstudio.runware.probe` and `vjhstudio.runware.sizes` (same package) but never `services`. Task 1 already put the size math in `vjhstudio/runware/sizes.py` (`nearest_size_in(dims, w, h)`, `snap_to_rule(w, h, rule)`); `size_correction` uses those.

- [ ] **Step 1: Failing tests** (`tests/test_runner.py` additions):
```python
KLING_MSG = ("Unsupported use of width/height parameters. The specified dimensions are not supported for the kling "
             "video 3.0 4k model. Supported values are: '3840x2160', '2160x3840', '2880x2880'.")
LTX_MSG = "Invalid value for 'width' parameter. Video width must be an integer value between 128 and 2048, in multiples of 64."


def test_width_height_are_never_dropped():
    e = RunwareError("validation", "Unsupported use of width/height parameters. Supported values are: '3840x2160'.")
    e.parameter = "width"
    assert runner.rejected_field(e, {"width": 1280, "height": 720}) is None


def test_size_correction_picks_nearest_listed_size():
    e = RunwareError("validation", KLING_MSG); e.parameter = "width"
    new, rec = runner.size_correction(e, {"width": 1280, "height": 720, "taskUUID": "t"})
    assert (new["width"], new["height"]) == (3840, 2160)
    assert rec == {"field": "width/height", "action": "corrected", "from": [1280, 720], "to": [3840, 2160],
                   "dims": {"mode": "list", "list": [[3840, 2160], [2160, 3840], [2880, 2880]]}}


def test_size_correction_snaps_to_rule():
    e = RunwareError("validation", LTX_MSG); e.parameter = "width"
    new, rec = runner.size_correction(e, {"width": 1280, "height": 720})
    assert (new["width"], new["height"]) == (1280, 704) and rec["dims"]["mode"] == "rule"


def test_size_correction_returns_none_without_a_rule_or_list():
    e = RunwareError("validation", "Unsupported width/height combination for this model architecture."); e.parameter = "width"
    assert runner.size_correction(e, {"width": 1, "height": 1}) is None


@pytest.mark.asyncio
async def test_run_with_policy_corrects_size_once_then_succeeds():
    e = RunwareError("validation", KLING_MSG); e.parameter = "width"
    fake = FakeRunware({"run": [e, [{"taskType": "videoInference", "videoURL": "http://x/v.mp4"}]]})
    res = await runner.run_with_policy(fake, {"taskType": "videoInference", "taskUUID": "a", "model": "k", "positivePrompt": "p", "width": 1280, "height": 720},
                                       timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep)
    sent = [p for n, p in fake.calls if n == "run"]
    assert (sent[1]["width"], sent[1]["height"]) == (3840, 2160) and sent[1]["taskUUID"] != "a"
    assert res.dropped[0]["action"] == "corrected"


@pytest.mark.asyncio
async def test_a_second_size_rejection_fails_the_job():
    e = RunwareError("validation", KLING_MSG); e.parameter = "width"
    e2 = RunwareError("validation", KLING_MSG); e2.parameter = "width"
    fake = FakeRunware({"run": [e, e2]})
    with pytest.raises(RunwareError):
        await runner.run_with_policy(fake, {"taskType": "videoInference", "taskUUID": "a", "model": "k", "positivePrompt": "p", "width": 1, "height": 1},
                                     timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep)
    assert len([1 for n, _ in fake.calls if n == "run"]) == 2
```
Jobs test: seed a video catalog row with no constraints, script the fake with the Kling rejection then a success, run the job through the app's runner (follow the existing end-to-end job test pattern in `tests/test_jobs*.py`), then assert the catalog row's `constraints_json["dims"] == {"mode": "list", "list": [...], "labels": {}}` and `sources.observed` is set, and `job.dropped_params_json[0]["action"] == "corrected"`.

- [ ] **Step 2: Implement.** In `rejected_field`, return `None` when the resolved path's last segment is in `PAIR`. Add `size_correction()`:
```python
def size_correction(err: BaseException, task: dict) -> tuple[dict, dict] | None:
    message = getattr(err, "message", None) or str(err)
    param = str(getattr(err, "parameter", "") or "")
    if not ("width" in param or "height" in param or "width/height" in message):
        return None
    dims = parse_supported_dims(message)   # from vjhstudio.runware.probe
    if dims.get("mode") == "unknown" or "width" not in task or "height" not in task:
        return None
    w, h = int(task["width"]), int(task["height"])
    nw, nh = nearest_size_in(dims, w, h)   # from vjhstudio.runware.sizes
    if (nw, nh) == (w, h):
        return None
    new = copy.deepcopy(task)
    new["width"], new["height"] = nw, nh
    return new, {"field": "width/height", "action": "corrected", "from": [w, h], "to": [nw, nh], "dims": dims}
```
In `run_with_policy`'s validation branch: try `size_correction` first (only if no record with `action == "corrected"` exists yet — one correction per job), then the existing `rejected_field` path. In `jobs._persist` (or right after `run_with_policy` returns), for each `rec` with `action == "corrected"` load the catalog row by `plan.model_air` and `constraints.store(s, m, constraints.observe_dims(m.constraints_json, rec["dims"], now_iso))`.
- [ ] **Step 3: Run** full suite → PASS; ruff clean.
- [ ] **Step 4: Commit** `feat(runner): correct rejected sizes from RunWare's supported list instead of dropping them`.

---

### Task 6: Constraint-aware Generate page and pre-flight

**Files:**
- Modify: `vjhstudio/web/routes/generate.py` (`params_ctx`, `video_params_ctx`, `_page` model lists, dropdown labels), `vjhstudio/services/catalog.py` (`list_generate_models(session, kind)`, `badge(m)`), `vjhstudio/services/generate.py` (pre-flight), `vjhstudio/runware/tasks.py` (`build_video_task` uses `req.width/req.height` when present), `vjhstudio/schemas/video.py` (optional `width`/`height`), templates `generate/_video_params.html`, `generate/_model_params.html`, `catalog/_model_select.html`
- Create: `tests/test_generate_constraints.py`

**Interfaces (produces/consumes):**
- `VideoRequest.width: int | None = None`, `height: int | None = None` (validated ≥ 64). `build_video_task`: if both set → use them; else the existing `resolution_wh` path.
- `video_params_ctx` returns extra keys: `"sizes": constraints.size_options(c, "video", presets)` where presets are the curated `tiers.video.dims` entries when present else `[(1280,720,"720p"), (1920,1080,"1080p")]`; `"size_mode": c.dims.mode`; `"duration_spec": constraints.duration_spec(c)`; `"needs_first_frame": bool`. Template: when `size_mode == "list"` render `<select name="size">` of `WxH` values (labelled) and hidden `width`/`height` synced by the same tiny `onchange` handler the image panel uses, no `resolution` select; otherwise the current resolution select. Duration: `values` → select; `min/max/step` → `<input type="number" min max step>`; else current behaviour. Show `<p class="warn">needs a first frame</p>` when `needs_first_frame`.
- `params_ctx` (image): `"size_presets"` becomes `[(o["w"], o["h"], o["label"]) for o in constraints.size_options(c, "image", SIZE_PRESETS)]`; when `size_mode == "list"` the width/height inputs are `readonly`; when `rule`, the inputs carry `min/max/step` from the rule.
- `catalog.list_generate_models(session, kind)` = `list_models` filtered by `constraints.is_generate_capable`; `catalog.badge(m) -> str` returns `"needs a first frame"` / `"video-to-video only — not supported yet"` / `""`; `label()` appends ` · needs a first frame` for i2v-only video rows. `_page` uses `list_generate_models` for the dropdowns.
- `services/generate.enqueue_video` pre-flight, after `_require_kind`: `if constraints.requires_input_video(m.constraints_json): raise ValueError("This model edits an existing video. VJHStudio cannot supply one yet.")`; `if constraints.needs_first_frame(m.capabilities_json, m.constraints_json) and req.first_frame_asset_id is None: raise ValueError("This model needs a first-frame image. Add one under References.")`. The route already maps `ValueError` to the 422 form re-render with the message (verify; if it maps to a toast, put the message in `errors["model"]`).

- [ ] **Step 1: Failing tests** (`tests/test_generate_constraints.py`, using the `app`/`client` fixtures; seed rows with `constraints_json` directly through `session_factory`):
  - `GET /hx/model-options?mode=video&air=<kling>` contains `3840×2160` and `4K (16:9)` and no `1280x720`/`720p` option, and a hidden `width` input; duration renders as `<input type="number" name="duration" min="3" max="15" step="1"`.
  - `GET /hx/model-options?mode=video&air=<veo>` (values [4,6,7,8]) renders a duration `<select>` with exactly those options.
  - `GET /hx/model-options?mode=image&air=<nano-banana>` (list mode) has the listed sizes as presets and `readonly` width/height.
  - `GET /generate/video` dropdown omits the Aleph-like row and the i2v-only row's label contains `needs a first frame`.
  - `POST /generate/video` for the Aleph-like row → 422 containing the exact sentence; for the i2v-only row without a first frame → 422 with the exact sentence; with `first_frame_asset_id` set (seed an asset) → 200/202 and a job row.
  - `POST /generate/video` for Kling with `width=3840&height=2160` → the queued job's `request_json` has width/height and `build_video_task` output (check `task_json` after the runner starts, or unit-test `build_video_task` directly) carries 3840x2160.
- [ ] **Step 2: Implement** as described. Keep `_video_params.html` diff minimal: wrap the existing Resolution `<label>` in `{% if size_mode == 'list' %} … {% else %} … {% endif %}`.
- [ ] **Step 3: Run** full suite → PASS; ruff clean; smoke on a spare port with a temp data dir: seed nothing, open `/generate/video`, confirm the page renders (curated rows have no constraints yet → unchanged behaviour).
- [ ] **Step 4: Commit** `feat(generate): constraint-aware sizes, durations, dropdown filtering and input pre-flight`.

---

### Task 7: Curated snapshot v3, version 0.3.0, docs

**Files:**
- Modify: `vjhstudio/data/curated_models.json` (version 3; `constraints` block per curated model), `vjhstudio/services/catalog.py` (`seed_curated` / `upsert_row` copy `row["constraints"]` into `constraints_json` when the row has one and the existing row has no `sources.observed`/`api`), `vjhstudio/__init__.py` (`0.3.0`), `CHANGELOG.md`, `README.md` (a "Models" section: what Harvest constraints does, that it is free, and that video-only editors are hidden from Generate), `tests/test_version.py`, `tests/test_catalog*.py`

Curated constraints to ship (from verified probes/docs on 2026-09-22):
- `klingai:kling-video@3-4k`: dims list `[[3840,2160],[2160,3840],[2880,2880]]` labels `4K (16:9)`, `4K (9:16)`, `4K (1:1)`; duration `{min 3, max 15, step 1, default 5}`.
- `klingai:kling-video@3-standard`: duration `{min 3, max 15, step 1, default 5}` (keep `tiers.video` resolutions; dims unknown → no block).
- `lightricks:ltx@2.3`: dims rule `{min 128, max 2048, step 64}`; duration `{min 1, max 20, type float}`; fps `{min 1, max 120, default 25}`.
- `alibaba:wan@2.7`: dims list `[[1280,720],[720,1280],[960,960],[1088,832],[832,1088],[1920,1080],[1080,1920],[1440,1440],[1632,1248],[1248,1632]]`; duration `{min 2, max 15, default 5}`.
- `google:3@2` (Veo 3.1): dims list `[[1280,720],[720,1280],[1920,1080],[1080,1920],[3840,2160],[2160,3840]]` labels `720p (16:9)` …; duration `{values [4,6,7,8], default 4}`.
- `runware:101@1` (FLUX.1 dev): dims rule `{128, 2048, 64}`; steps `{min 1, max 100, default 28}`; strength `{min 0, max 1, step 0.01, default 0.8}`.
- `google:4@2` (Nano Banana Pro): dims list of the documented sizes (take the 20 pairs from the docs table: 1024x1024, 1152x928, 928x1152 … include every pair the docs page lists; the implementer fetches `https://runware.ai/docs/models/google-nano-banana-pro` once with a browser User-Agent and runs `docs_pages.parse_docs` to get the exact list — record the count in the report).
- If the curated JSON also carries `bytedance:seedance@2.0`, `runway`, etc., leave them without a block.

- [ ] **Step 1: Tests**: `seed_curated` on an empty DB stores Kling's dims list; re-seeding does not overwrite a row whose `sources.api` is set; `__version__ == "0.3.0"`; CHANGELOG has `## 0.3.0`; README contains `Harvest constraints`.
- [ ] **Step 2: Implement**; `CHANGELOG.md` `## 0.3.0 — 2026-09-22` Added: model constraints harvest (free), constraint-aware Generate page, runner size correction, `probe` CLI; Fixed: Kling 4K size rejection, video-only editors offered as text-to-video.
- [ ] **Step 3: Run** full suite → PASS; ruff clean.
- [ ] **Step 4: Commit** `chore: curated constraints, version 0.3.0, changelog and README`.

---

## Phase 7 exit criteria

- On the live catalog, `vjhstudio probe --kind video --kind image` finishes with the balance unchanged, and the report shows `dims_mode` list/rule for the large majority of rows (Veo-style "unknown" rows fall back to docs tables).
- Generate → video → Kling VIDEO 3.0 4K offers exactly the three 4K sizes and a 3–15 s duration; submitting works without a size correction. Runway Aleph 2.0 is absent from the dropdown and shows the badge on the Models page; posting to it directly returns the spec sentence.
- A job whose model has no constraints yet and gets a "Supported values are" rejection is corrected once, succeeds, and the catalog row now carries the observed list.
- `uv run --frozen pytest -q` green, ruff clean, no network in tests.
