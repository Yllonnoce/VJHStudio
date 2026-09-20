# VJHStudio Phase 3 (Image generation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The first usable creative loop: a Generate page with the structured prompt builder and price-sorted model dropdown, image jobs that run in the background through RunWare with a working progress bar and completion notifications, files saved into project folders with JSON sidecars, a gallery with remix and download, and cost tracking.

**Architecture:** Pure task builders (`runware/tasks.py`) turn a typed `ImageRequest` into a RunWare task dict; `runware/runner.py` runs it through `client.run` with retry and parameter-fallback rules; `runware/download.py` streams results to disk with sidecars. `services/jobs.py` is an asyncio `JobRunner` living in the app lifespan (queue + semaphore + per-job cancel event, progress snapshot in memory, DB writes throttled). Routes stay thin; the Generate page is Jinja2 + Alpine (client-side prompt preview mirrors `services/prompts.compose`) and HTMX polling of the queue partial (2 s while jobs are active). Estimated progress uses the catalog's `typical_latency_ms`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic (one new migration), pydantic 2, httpx (downloads), runware-sdk (`client.run`, `RunOptions`), Jinja2 + HTMX 2 + Alpine 3 + Pico, Pillow (thumbnails), pytest + FakeRunware + `httpx.MockTransport`.

**Spec:** `docs/superpowers/specs/2026-09-20-runwarestudio-design.md` — sections "RunWare adapter", "Job runner", "Prompt composition", "Job completion notifications", "Working progress bar", "UI design → Generate / Gallery / Projects", "Data model → jobs, outputs, usage_entries, projects".

## Global Constraints

- Package `vjhstudio`; env prefix `VJHSTUDIO_`; commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .` clean before every commit. `runware/` and `services/` never import `web/`. Routes that only touch the DB are sync `def`; routes that await are `async def`.
- Every RunWare task dict: `taskType`, `taskUUID` (= job id, new uuid per retry), `outputType: "URL"`, `includeCost: true`. Image: `positivePrompt`, `negativePrompt` (diffusion only), `model`, `width`, `height` (multiples of 64, clamped 512–2048 for diffusion; catalog defaults otherwise), `numberResults` (1–8), `outputFormat` (PNG|JPG|WEBP), optional `seed`, `steps`, `CFGScale`, `scheduler`; image-to-image via `inputs.seedImage` + top-level `strength` (diffusion) or `inputs.referenceImages: [..]` (instruction family). `extra_json` merged last and may not override `taskType`/`taskUUID`/`model`.
- Prompt cap: `positivePrompt` ≤ 2900 chars, suffix-safe. "No text in image" toggle (default on for image) appends the suffix ` Pure artwork only: absolutely no text, no words, no letters, no labels, no captions, no title, no watermark, no signature, no borders, no user interface elements.` and adds negative tokens `text, words, letters, typography, writing, labels, captions, title, watermark, signature, logo, user interface, borders, frame, split image, collage`.
- Runner policy: max 5 attempts. `validation` with `err.parameter` (or regex `unsupported use of '?([A-Za-z0-9]+)'?` on the message): `inputs.seedImage` → move value to `inputs.referenceImages=[v]` and drop `strength`; `strength` → drop only strength; `inputs.referenceImages` → drop + flag `reference_dropped`; other params → delete that dotted key; never drop `model`/`positivePrompt`/`taskType`/`taskUUID`. Backoff: `rateLimit` [2,5,15] s, `connection` [1,3,8] s, `serverError` once after 5 s; anything else raises. All drops/conversions recorded in `dropped_params_json` and the sidecar.
- Files: `data/outputs/<project-slug>/<YYYYMMDD-HHMMSS-xxxxxx>.<ext>` + sidecar `.json`; download via httpx stream to `.part` then `os.replace`; 2 retries; sidecar keys `app_version, job_id, project, model, prompt, negative_prompt, params, seed, cost, task_sent, dropped_params, source_url, created_at`. Thumbnails: `data/thumbs/<output_id>.jpg` (max 384 px, Pillow) generated after download; the gallery uses thumbnails.
- Job lifecycle: `queued → running → succeeded|failed|cancelled`; `cancel` sets the asyncio Event (UI copy: "Stop waiting; RunWare still bills a submitted job"); boot marks `running` as `failed(orphaned)` and re-submits `queued` (already done in `boot.orphan_jobs`; the runner must consume `BootInfo.requeued_jobs`). Concurrency = setting `jobs.concurrency` (default 3), live-adjustable.
- Progress: real `progress` from `on_progress` when RunWare emits it; otherwise estimate `min(90, 100*(1-exp(-elapsed_ms/expected_ms)))` with `expected_ms = tiers.typical_latency_ms or 20000`, refined by a rolling average of finished job durations per model (`tiers.observed.avg_ms`); 100 on success. Stage labels: `queued`, `submitting`, `rendering`, `downloading`, `done`, `failed`, `cancelled`.
- Notifications: when a job finishes, the queue partial response carries `HX-Trigger: {"job-finished": {"id":..., "status":..., "title":..., "thumb": <url or null>}}`; `app.js` shows a toast and, if `Notification.permission === "granted"` and setting `ui.notify_desktop` is on, a desktop notification. Settings gets the toggle (`ui.notify_desktop`, bool, default False) and a "Test notification" button that requests permission.
- Cost: one `usage_entries` row per costed result (`task_type="imageInference"`), job.cost = sum; header chip shows balance and today's spend; project rows show totals.
- Schema change (migration `0002_phase3`): `jobs.expected_ms INTEGER NULL`, `jobs.seen_at DATETIME NULL`, `jobs.title VARCHAR(120) NULL`; `settings` gains no columns (keys only). `outputs.thumb_rel_path VARCHAR(300) NULL`.

## File Structure

```
vjhstudio/schemas/__init__.py, image.py        ImageRequest (pydantic), PromptForm
vjhstudio/services/prompts.py                  compose(form) -> str ; build_negative(form, default_negative, no_text) ; apply_no_text(prompt) ; PROMPT_MAX=2900
vjhstudio/runware/tasks.py                     build_image_task(req, task_uuid, media_map, family) -> dict
vjhstudio/runware/runner.py                    run_with_policy(client, task, *, timeout_s, cancel_event, on_progress, on_attempt) -> TaskResult ; rejected_field(err, task)
vjhstudio/runware/results.py                   TaskResult, ResultItem, parse_items(rows)
vjhstudio/runware/download.py                  download_items(items, dest_dir, ext, transport=None) -> list[SavedFile] ; write_sidecar(path, data) ; make_thumbnail(src, dest, max_px=384)
vjhstudio/services/projects.py                 create(session, name) ; list_active ; slugify ; ensure_dir(paths, slug) ; totals(session, project_id)
vjhstudio/services/generate.py                 enqueue_image(session_factory, paths, req) -> Job (validates, composes prompt, creates Job row, returns it)
vjhstudio/services/jobs.py                     JobRunner (start/stop/submit/cancel/set_concurrency/snapshot/wait_idle) ; run_one(job_id) pipeline ; progress estimate helpers
vjhstudio/services/outputs.py                  record_outputs(session, job, saved) ; gallery_query(session, filters, page) ; toggle_favourite ; delete_output(paths, session, id) ; remix_params(output)
vjhstudio/services/costs.py                    record_usage ; totals_by_project ; today_spend ; observe_latency(session, model_air, ms)
migrations/versions/0002_phase3.py
vjhstudio/web/routes/generate.py               GET /generate (image) ; POST /generate/image ; POST /hx/prompt/compose ; GET /hx/model-options?air= ; GET /hx/generate/estimate
vjhstudio/web/routes/jobs.py                   GET /hx/jobs/active ; GET /hx/jobs/{id} ; POST /jobs/{id}/cancel ; POST /jobs/{id}/retry ; GET /api/jobs ; GET /api/jobs/{id} ; POST /jobs/seen
vjhstudio/web/routes/gallery.py                GET /gallery ; GET /hx/gallery ; GET /hx/outputs/{id} ; POST /outputs/{id}/favourite ; DELETE /outputs/{id} ; GET /outputs/{id}/download ; GET /outputs/{id}/remix
vjhstudio/web/routes/projects.py               GET /projects ; POST /projects ; POST /projects/{id} ; POST /projects/{id}/archive ; GET /hx/projects/select
vjhstudio/web/routes/files.py                  GET /files/outputs/{slug}/{filename} ; GET /files/thumbs/{name}
vjhstudio/web/templates/pages/generate.html, gallery.html, projects.html ; generate/_prompt_builder.html, _model_params.html, _project_select.html, _queue_panel.html, _job_card.html, _estimate.html ; gallery/_grid.html, _card.html, _detail.html ; projects/_row.html ; partials/_jobs_badge.html
vjhstudio/web/static/js/app.js (+ composePrompt, generateForm, job-finished handler) ; css additions
tests/test_prompts.py, test_tasks.py, test_runner.py, test_download.py, test_jobs.py, test_generate_service.py, test_outputs.py, test_web_generate.py, test_web_jobs.py, test_web_gallery.py, test_web_projects.py, tests/fixtures/compose_cases.json
```

---

### Task 1: Prompt composition (Python) with shared fixtures

**Files:**
- Create: `vjhstudio/schemas/__init__.py` (empty), `vjhstudio/schemas/image.py`, `vjhstudio/services/prompts.py`, `tests/fixtures/compose_cases.json`, `tests/test_prompts.py`

**Interfaces:**
- Produces:
  - `schemas.image.PromptForm(BaseModel)`: fields `subject, style, mood, lighting, camera, composition, colour, extras, negative: str = ""`, `use_default_negative: bool = True`, `no_text: bool = True`.
  - `schemas.image.ImageRequest(BaseModel)`: `project_id: int`, `prompt_id: int | None = None`, `model: str`, `form: PromptForm`, `final_prompt: str | None = None` (when the user edited/polished; else composed), `width: int = 1024`, `height: int = 1024`, `number_results: int = 1` (1..8), `seed: int | None = None`, `steps: int | None = None`, `cfg_scale: float | None = None`, `scheduler: str | None = None`, `strength: float | None = None`, `output_format: Literal["PNG","JPG","WEBP"] = "PNG"`, `seed_image_asset_id: int | None = None`, `reference_asset_ids: list[int] = []`, `extra_json: dict = {}`, `title: str | None = None`. Validators: width/height multiples of 64 within 128..2048 (`field_validator`), number_results 1..8, strength 0..1.
  - `services.prompts.FIELD_ORDER = ("subject","style","mood","lighting","camera","composition","colour","extras")`
  - `prompts.clean(s) -> str` (collapse whitespace, strip, strip trailing ` ,;.`)
  - `prompts.compose(form: PromptForm | dict) -> str` (join cleaned non-empty fields with `", "`, drop case-insensitive duplicate fields)
  - `prompts.build_negative(form, default_negative: str, no_text: bool) -> str` (user tokens + default tokens if `use_default_negative` + NO_TEXT_NEGATIVE tokens if `no_text`, de-duplicated case-insensitively, joined `", "`)
  - `prompts.apply_no_text(prompt: str) -> str` and `prompts.cap(prompt: str, suffix: str = "") -> str` (suffix-safe truncation to `PROMPT_MAX = 2900`)
  - `prompts.NO_TEXT_SUFFIX`, `prompts.NO_TEXT_NEGATIVE` (exact strings from Global Constraints)
  - `prompts.final_prompt(req: ImageRequest) -> str` = `cap(req.final_prompt or compose(req.form), NO_TEXT_SUFFIX if req.form.no_text else "")`

- [ ] **Step 1: Fixture** `tests/fixtures/compose_cases.json` (also used by the JS mirror in Task 8):
```json
[
 {"form": {"subject": "a red fox", "style": "oil painting", "mood": "", "lighting": "golden hour", "camera": "", "composition": "", "colour": "", "extras": ""}, "composed": "a red fox, oil painting, golden hour"},
 {"form": {"subject": "  a   red fox, ", "style": "oil painting.", "mood": "Oil Painting"}, "composed": "a red fox, oil painting"},
 {"form": {"subject": "", "style": "   "}, "composed": ""},
 {"form": {"subject": "castle", "extras": "8k; detailed;"}, "composed": "castle, 8k; detailed"}
]
```
- [ ] **Step 2: Failing tests**
```python
# tests/test_prompts.py
import json
from pathlib import Path
import pytest
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import prompts

CASES = json.loads((Path(__file__).parent / "fixtures" / "compose_cases.json").read_text())

@pytest.mark.parametrize("case", CASES, ids=[c["composed"][:20] or "empty" for c in CASES])
def test_compose_cases(case):
    assert prompts.compose(case["form"]) == case["composed"]

def test_compose_is_deterministic_and_ordered():
    form = PromptForm(extras="z", subject="a", colour="c")
    assert prompts.compose(form) == "a, c, z" == prompts.compose(form)

def test_build_negative_merges_and_dedupes():
    form = PromptForm(negative="blurry, Text", use_default_negative=True, no_text=True)
    neg = prompts.build_negative(form, "blurry, low quality", no_text=True)
    toks = [t.strip() for t in neg.split(",")]
    assert toks[:3] == ["blurry", "Text", "low quality"]
    assert "watermark" in toks and len(toks) == len({t.lower() for t in toks})

def test_build_negative_without_defaults():
    assert prompts.build_negative(PromptForm(negative="", use_default_negative=False, no_text=False), "x", no_text=False) == ""

def test_cap_is_suffix_safe():
    body = "a" * 5000
    out = prompts.cap(body, prompts.NO_TEXT_SUFFIX)
    assert len(out) <= prompts.PROMPT_MAX and out.endswith(prompts.NO_TEXT_SUFFIX)

def test_final_prompt_prefers_user_text():
    req = ImageRequest(project_id=1, model="runware:101@1", form=PromptForm(subject="fox", no_text=False), final_prompt="my own words")
    assert prompts.final_prompt(req) == "my own words"
    req2 = ImageRequest(project_id=1, model="runware:101@1", form=PromptForm(subject="fox", no_text=True))
    assert prompts.final_prompt(req2) == "fox" + prompts.NO_TEXT_SUFFIX

def test_image_request_validation():
    with pytest.raises(ValueError):
        ImageRequest(project_id=1, model="m", form=PromptForm(), width=1000)
    with pytest.raises(ValueError):
        ImageRequest(project_id=1, model="m", form=PromptForm(), number_results=9)
    assert ImageRequest(project_id=1, model="m", form=PromptForm(), width=1024, height=768).height == 768
```
- [ ] **Step 3: Run → fail.**
- [ ] **Step 4: Implement**
```python
# vjhstudio/schemas/image.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class PromptForm(BaseModel):
    subject: str = ""
    style: str = ""
    mood: str = ""
    lighting: str = ""
    camera: str = ""
    composition: str = ""
    colour: str = ""
    extras: str = ""
    negative: str = ""
    use_default_negative: bool = True
    no_text: bool = True


class ImageRequest(BaseModel):
    project_id: int
    prompt_id: int | None = None
    model: str
    form: PromptForm = Field(default_factory=PromptForm)
    final_prompt: str | None = None
    width: int = 1024
    height: int = 1024
    number_results: int = Field(1, ge=1, le=8)
    seed: int | None = None
    steps: int | None = Field(None, ge=1, le=150)
    cfg_scale: float | None = Field(None, ge=0, le=30)
    scheduler: str | None = None
    strength: float | None = Field(None, ge=0, le=1)
    output_format: Literal["PNG", "JPG", "WEBP"] = "PNG"
    seed_image_asset_id: int | None = None
    reference_asset_ids: list[int] = Field(default_factory=list)
    extra_json: dict = Field(default_factory=dict)
    title: str | None = None

    @field_validator("width", "height")
    @classmethod
    def _dim(cls, v: int) -> int:
        if v % 64 or not 128 <= v <= 2048:
            raise ValueError("must be a multiple of 64 between 128 and 2048")
        return v
```
```python
# vjhstudio/services/prompts.py
"""Deterministic prompt composition. Mirrored byte-for-byte by static/js/app.js composePrompt."""
from __future__ import annotations
import re
from ..schemas.image import ImageRequest, PromptForm

FIELD_ORDER = ("subject", "style", "mood", "lighting", "camera", "composition", "colour", "extras")
PROMPT_MAX = 2900
NO_TEXT_SUFFIX = (" Pure artwork only: absolutely no text, no words, no letters, no labels, no captions,"
                  " no title, no watermark, no signature, no borders, no user interface elements.")
NO_TEXT_NEGATIVE = ("text, words, letters, typography, writing, labels, captions, title, watermark,"
                    " signature, logo, user interface, borders, frame, split image, collage")
_WS = re.compile(r"\s+")


def clean(s: str | None) -> str:
    return _WS.sub(" ", s or "").strip().rstrip(" ,;.").strip()


def compose(form: PromptForm | dict) -> str:
    data = form.model_dump() if isinstance(form, PromptForm) else dict(form)
    parts: list[str] = []
    seen: set[str] = set()
    for key in FIELD_ORDER:
        v = clean(data.get(key))
        if v and v.lower() not in seen:
            parts.append(v)
            seen.add(v.lower())
    return ", ".join(parts)


def _tokens(text: str) -> list[str]:
    return [t for t in (clean(x) for x in (text or "").split(",")) if t]


def build_negative(form: PromptForm, default_negative: str, no_text: bool) -> str:
    toks = _tokens(form.negative)
    if form.use_default_negative:
        toks += _tokens(default_negative)
    if no_text:
        toks += _tokens(NO_TEXT_NEGATIVE)
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t.lower() not in seen:
            out.append(t)
            seen.add(t.lower())
    return ", ".join(out)


def cap(prompt: str, suffix: str = "") -> str:
    body = (prompt or "")[: PROMPT_MAX - len(suffix)]
    return body + suffix


def apply_no_text(prompt: str) -> str:
    return cap(prompt, NO_TEXT_SUFFIX)


def final_prompt(req: ImageRequest) -> str:
    base = clean(req.final_prompt) if req.final_prompt else compose(req.form)
    return cap(base, NO_TEXT_SUFFIX if req.form.no_text else "")
```
- [ ] **Step 5: Run → green; ruff clean. Commit:** `git add vjhstudio/schemas vjhstudio/services/prompts.py tests/fixtures/compose_cases.json tests/test_prompts.py && git commit -m "feat: prompt composition and ImageRequest schema" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`
