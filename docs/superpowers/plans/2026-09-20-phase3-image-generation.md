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

---

### Task 2: Task builder, result parsing and the runner policy

**Files:**
- Create: `vjhstudio/runware/tasks.py`, `vjhstudio/runware/results.py`, `vjhstudio/runware/runner.py`, `tests/test_tasks.py`, `tests/test_runner.py`
- Modify: `tests/fakes/fake_runware.py` (make `run()` honour `options.cancel_event` and call `options.on_progress` when a scripted reply is a `("progress", [10, 50], reply)` tuple)

**Interfaces:**
- Consumes: `ImageRequest`, `prompts.final_prompt`, `prompts.build_negative`, `runware.errors.classify`, `RunwareError`, `RunOptions` from `runware`.
- Produces:
  - `tasks.build_image_task(req: ImageRequest, task_uuid: str, media: dict[int, str], family: str, negative: str) -> dict` — `media` maps asset id → RunWare media UUID/URL (Phase 4 fills it; Phase 3 passes `{}`); `family` ∈ `diffusion|instruction`. Rules: always `taskType="imageInference"`, `taskUUID`, `model`, `positivePrompt=prompts.final_prompt(req)`, `outputType="URL"`, `outputFormat=req.output_format`, `includeCost=True`, `numberResults`, `width`, `height` (diffusion: rounded to a multiple of 64 and clamped 512..2048 with aspect clamped to 2:1; instruction: as given); optional `seed`, `steps`, `CFGScale`, `scheduler` only for diffusion and only when not None; `negativePrompt=negative` only for diffusion and only when non-empty; image inputs: diffusion → `inputs.seedImage=media[seed_image_asset_id]` + `strength` (default 0.8); instruction → `inputs.referenceImages=[media[i] for i in reference_asset_ids (+ seed image if given)]`; finally `task.update({k: v for k, v in req.extra_json.items() if k not in ("taskType","taskUUID","model")})`.
  - `tasks.dim64(v: int) -> int` (round to 64, clamp 512..2048) and `tasks.clamp_aspect(w, h) -> tuple[int,int]` (max 2:1 either way, adjust the larger side down).
  - `results.ResultItem(url: str, seed: int | None, cost: float | None, uuid: str | None, nsfw: bool, raw: dict)`, `results.TaskResult(items: list[ResultItem], task_uuid: str, task_sent: dict, dropped: list[dict], attempts: int)`, `results.parse_items(rows: list[dict]) -> list[ResultItem]` (reads `imageURL`/`videoURL`/`url`, `seed`, `cost`, `imageUUID`/`videoUUID`, `NSFWContent`).
  - `runner.rejected_field(err: BaseException, task: dict) -> str | None` (`.parameter` first, then regex on the message, only if the dotted path exists in the task and is not protected).
  - `runner.apply_fallback(task: dict, field: str) -> tuple[dict, dict]` → new task + a record `{"field": ..., "action": "converted_to_referenceImages"|"dropped"|"dropped_strength"}`.
  - `async runner.run_with_policy(client, task, *, timeout_s: float, cancel_event: asyncio.Event | None, on_progress: Callable[[int], None] | None, on_attempt: Callable[[dict, list[dict]], None] | None = None, sleep=asyncio.sleep) -> TaskResult`; raises `RunwareError` (last one) when attempts are exhausted or the error is not retryable.

- [ ] **Step 1: Failing tests**
```python
# tests/test_tasks.py
from vjhstudio.runware import tasks
from vjhstudio.schemas.image import ImageRequest, PromptForm

def req(**kw):
    base = dict(project_id=1, model="runware:101@1", form=PromptForm(subject="fox", no_text=False))
    base.update(kw)
    return ImageRequest(**base)

def test_dim64_and_aspect():
    assert tasks.dim64(1000) == 1024 and tasks.dim64(100) == 512 and tasks.dim64(5000) == 2048
    assert tasks.clamp_aspect(2048, 512) == (1024, 512)
    assert tasks.clamp_aspect(512, 2048) == (512, 1024)
    assert tasks.clamp_aspect(1024, 768) == (1024, 768)

def test_diffusion_task_shape():
    t = tasks.build_image_task(req(steps=28, cfg_scale=3.5, seed=7, extra_json={"scheduler": "Euler", "model": "hack"}), "uuid-1", {}, "diffusion", "blurry")
    assert t["taskType"] == "imageInference" and t["taskUUID"] == "uuid-1" and t["model"] == "runware:101@1"
    assert t["positivePrompt"] == "fox" and t["negativePrompt"] == "blurry"
    assert t["outputType"] == "URL" and t["includeCost"] is True and t["outputFormat"] == "PNG"
    assert t["width"] == 1024 and t["height"] == 1024 and t["numberResults"] == 1
    assert t["steps"] == 28 and t["CFGScale"] == 3.5 and t["seed"] == 7 and t["scheduler"] == "Euler"

def test_instruction_task_drops_diffusion_knobs_and_uses_reference_images():
    t = tasks.build_image_task(req(model="google:4@2", steps=28, seed_image_asset_id=5, reference_asset_ids=[6]), "u", {5: "uuid-5", 6: "uuid-6"}, "instruction", "blurry")
    assert "steps" not in t and "negativePrompt" not in t and "strength" not in t
    assert t["inputs"]["referenceImages"] == ["uuid-5", "uuid-6"]

def test_diffusion_seed_image_and_strength():
    t = tasks.build_image_task(req(seed_image_asset_id=5), "u", {5: "uuid-5"}, "diffusion", "")
    assert t["inputs"]["seedImage"] == "uuid-5" and t["strength"] == 0.8 and "negativePrompt" not in t

def test_no_text_suffix_applied():
    r = req(); r.form.no_text = True
    t = tasks.build_image_task(r, "u", {}, "diffusion", "")
    assert t["positivePrompt"].endswith("no user interface elements.")
```
```python
# tests/test_runner.py
import asyncio
import pytest
from runware import RunwareError
from vjhstudio.runware import runner
from tests.fakes.fake_runware import FakeRunware

TASK = {"taskType": "imageInference", "taskUUID": "u1", "model": "m", "positivePrompt": "p",
        "inputs": {"seedImage": "img"}, "strength": 0.8, "steps": 28}

def test_rejected_field_from_parameter_and_message():
    e = RunwareError("unsupportedParameter", "Unsupported use of 'steps' parameter.")
    assert runner.rejected_field(e, TASK) == "steps"
    e2 = RunwareError("unsupportedParameter", "nope"); e2.parameter = "inputs.seedImage"
    assert runner.rejected_field(e2, TASK) == "inputs.seedImage"
    e3 = RunwareError("unsupportedParameter", "Unsupported use of 'model' parameter.")
    assert runner.rejected_field(e3, TASK) is None

def test_apply_fallback_rules():
    t, rec = runner.apply_fallback(TASK, "inputs.seedImage")
    assert t["inputs"]["referenceImages"] == ["img"] and "seedImage" not in t["inputs"] and "strength" not in t
    assert rec["action"] == "converted_to_referenceImages"
    t2, rec2 = runner.apply_fallback(TASK, "strength")
    assert "strength" not in t2 and t2["inputs"]["seedImage"] == "img" and rec2["action"] == "dropped_strength"
    t3, rec3 = runner.apply_fallback(TASK, "steps")
    assert "steps" not in t3 and rec3["action"] == "dropped"

async def _no_sleep(_s):
    return None

async def test_fallback_then_success_records_drops():
    fake = FakeRunware({"run": [RunwareError("unsupportedParameter", "Unsupported use of 'steps' parameter."),
                                [{"imageURL": "http://x/1.png", "seed": 3, "cost": 0.01}]]})
    attempts = []
    res = await runner.run_with_policy(fake, TASK, timeout_s=5, cancel_event=None, on_progress=None,
                                       on_attempt=lambda t, d: attempts.append((dict(t), list(d))), sleep=_no_sleep)
    assert res.items[0].url == "http://x/1.png" and res.items[0].seed == 3 and res.attempts == 2
    assert res.dropped == [{"field": "steps", "action": "dropped"}]
    assert "steps" not in fake.calls[-1][1] and fake.calls[-1][1]["taskUUID"] != "u1"

async def test_rate_limit_backoff_then_success():
    fake = FakeRunware({"run": [RunwareError("rateLimitExceeded", "slow"), [{"imageURL": "u"}]]})
    slept = []
    async def sleep(s): slept.append(s)
    res = await runner.run_with_policy(fake, TASK, timeout_s=5, cancel_event=None, on_progress=None, sleep=sleep)
    assert slept == [2] and res.attempts == 2

async def test_non_retryable_raises_immediately():
    fake = FakeRunware({"run": [RunwareError("invalidApiKey", "bad"), [{"imageURL": "u"}]]})
    with pytest.raises(RunwareError):
        await runner.run_with_policy(fake, TASK, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep)
    assert len(fake.calls) == 1

async def test_exhausts_after_five_attempts():
    errs = [RunwareError("unsupportedParameter", f"Unsupported use of 'k{i}' parameter.") for i in range(5)]
    task = dict(TASK, k0=1, k1=1, k2=1, k3=1, k4=1)
    fake = FakeRunware({"run": errs + [[{"imageURL": "u"}]]})
    with pytest.raises(RunwareError):
        await runner.run_with_policy(fake, task, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep)
    assert len(fake.calls) == 5

async def test_progress_callback_and_cancel():
    fake = FakeRunware({"run": [("progress", [10, 55], [{"imageURL": "u"}])]})
    seen = []
    res = await runner.run_with_policy(fake, TASK, timeout_s=5, cancel_event=None, on_progress=seen.append, sleep=_no_sleep)
    assert seen == [10, 55] and res.items
    ev = asyncio.Event(); ev.set()
    fake2 = FakeRunware({"run": [[{"imageURL": "u"}]]})
    with pytest.raises(RunwareError) as ei:
        await runner.run_with_policy(fake2, TASK, timeout_s=5, cancel_event=ev, on_progress=None, sleep=_no_sleep)
    assert ei.value.code == "aborted"
```
Fake change (`tests/fakes/fake_runware.py::run`): if `options` has a set `cancel_event`, raise `RunwareError("aborted", "Request aborted")`; if the scripted reply is a tuple `("progress", [..], reply)`, call `options.on_progress({"progress": p})` for each p, then return `reply`.

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement**
```python
# vjhstudio/runware/tasks.py
"""Pure builders: typed request -> RunWare task dict. No I/O."""
from __future__ import annotations
from ..schemas.image import ImageRequest
from ..services import prompts

PROTECTED = ("taskType", "taskUUID", "model")


def dim64(v: int) -> int:
    return int(max(512, min(2048, round(v / 64) * 64)))


def clamp_aspect(w: int, h: int) -> tuple[int, int]:
    if w > 2 * h:
        w = dim64(2 * h)
    elif h > 2 * w:
        h = dim64(2 * w)
    return w, h


def build_image_task(req: ImageRequest, task_uuid: str, media: dict[int, str], family: str, negative: str) -> dict:
    diffusion = family != "instruction"
    w, h = (clamp_aspect(dim64(req.width), dim64(req.height)) if diffusion else (req.width, req.height))
    task: dict = {
        "taskType": "imageInference", "taskUUID": task_uuid, "model": req.model,
        "positivePrompt": prompts.final_prompt(req), "outputType": "URL",
        "outputFormat": req.output_format, "includeCost": True,
        "numberResults": req.number_results, "width": w, "height": h,
    }
    if req.seed is not None:
        task["seed"] = req.seed
    if diffusion:
        if negative:
            task["negativePrompt"] = negative
        for key, val in (("steps", req.steps), ("CFGScale", req.cfg_scale), ("scheduler", req.scheduler)):
            if val is not None:
                task[key] = val
    refs = [media[i] for i in req.reference_asset_ids if i in media]
    seed_img = media.get(req.seed_image_asset_id) if req.seed_image_asset_id is not None else None
    inputs: dict = {}
    if diffusion:
        if seed_img:
            inputs["seedImage"] = seed_img
            task["strength"] = req.strength if req.strength is not None else 0.8
        if refs:
            inputs["referenceImages"] = refs
    else:
        all_refs = ([seed_img] if seed_img else []) + refs
        if all_refs:
            inputs["referenceImages"] = all_refs
    if inputs:
        task["inputs"] = inputs
    task.update({k: v for k, v in (req.extra_json or {}).items() if k not in PROTECTED})
    return task
```
```python
# vjhstudio/runware/results.py
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResultItem:
    url: str
    seed: int | None
    cost: float | None
    uuid: str | None
    nsfw: bool
    raw: dict


@dataclass
class TaskResult:
    items: list[ResultItem]
    task_uuid: str
    task_sent: dict
    dropped: list[dict] = field(default_factory=list)
    attempts: int = 1


def parse_items(rows: list[dict]) -> list[ResultItem]:
    out: list[ResultItem] = []
    for r in rows or []:
        url = r.get("imageURL") or r.get("videoURL") or r.get("url")
        if not url:
            continue
        cost = r.get("cost")
        out.append(ResultItem(url=str(url), seed=r.get("seed"), cost=float(cost) if cost is not None else None,
                              uuid=r.get("imageUUID") or r.get("videoUUID"), nsfw=bool(r.get("NSFWContent", False)), raw=r))
    return out
```
```python
# vjhstudio/runware/runner.py
"""Retry + parameter-fallback policy around client.run()."""
from __future__ import annotations
import asyncio, copy, re, uuid
from collections.abc import Awaitable, Callable
from runware import RunOptions, RunwareError
from .results import TaskResult, parse_items

MAX_ATTEMPTS = 5
PROTECTED = ("taskType", "taskUUID", "model", "positivePrompt")
_UNSUPPORTED = re.compile(r"unsupported use of '?([A-Za-z0-9_.]+)'? parameter", re.I)
_BACKOFF = {"rateLimit": [2, 5, 15], "connection": [1, 3, 8], "serverError": [5]}


def _has_path(task: dict, path: str) -> bool:
    cur: object = task
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _find_path(task: dict, name: str) -> str | None:
    if name in task:
        return name
    for parent in ("inputs", "providerSettings", "settings"):
        sub = task.get(parent)
        if isinstance(sub, dict):
            if name in sub:
                return f"{parent}.{name}"
            for k2, v2 in sub.items():
                if isinstance(v2, dict) and name in v2:
                    return f"{parent}.{k2}.{name}"
    return None


def rejected_field(err: BaseException, task: dict) -> str | None:
    cand = getattr(err, "parameter", None)
    if not cand:
        m = _UNSUPPORTED.search(getattr(err, "message", None) or str(err))
        cand = m.group(1) if m else None
    if not cand:
        return None
    path = cand if _has_path(task, cand) else _find_path(task, cand.split(".")[-1])
    if not path or path.split(".")[0] in PROTECTED or path in PROTECTED:
        return None
    return path


def _delete_path(task: dict, path: str) -> None:
    parts = path.split(".")
    cur = task
    for p in parts[:-1]:
        cur = cur[p]
    cur.pop(parts[-1], None)
    if parts[:-1] and isinstance(task.get(parts[0]), dict) and not task[parts[0]]:
        task.pop(parts[0])


def apply_fallback(task: dict, field: str) -> tuple[dict, dict]:
    new = copy.deepcopy(task)
    if field == "inputs.seedImage":
        val = new["inputs"].pop("seedImage")
        new["inputs"].setdefault("referenceImages", []).insert(0, val)
        new.pop("strength", None)
        return new, {"field": field, "action": "converted_to_referenceImages"}
    if field == "strength":
        new.pop("strength", None)
        return new, {"field": field, "action": "dropped_strength"}
    _delete_path(new, field)
    action = "dropped_reference_images" if field == "inputs.referenceImages" else "dropped"
    return new, {"field": field, "action": action}


async def run_with_policy(client, task: dict, *, timeout_s: float, cancel_event: asyncio.Event | None,
                          on_progress: Callable[[int], None] | None,
                          on_attempt: Callable[[dict, list[dict]], None] | None = None,
                          sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> TaskResult:
    dropped: list[dict] = []
    backoffs: dict[str, int] = {}
    current = copy.deepcopy(task)
    last: RunwareError | None = None

    def _progress(item: dict) -> None:
        p = item.get("progress") if isinstance(item, dict) else None
        if on_progress and isinstance(p, (int, float)):
            on_progress(int(p))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RunwareError("aborted", "Request aborted")
        opts = RunOptions(timeout=int(timeout_s * 1000), cancel_event=cancel_event, on_progress=_progress, validate=False)
        try:
            rows = await client.run(current, opts)
            return TaskResult(parse_items(rows), current["taskUUID"], current, dropped, attempt)
        except RunwareError as e:
            last = e
            if e.code == "validation":
                fld = rejected_field(e, current)
                if fld is None:
                    raise
                current, rec = apply_fallback(current, fld)
                dropped.append(rec)
                current["taskUUID"] = str(uuid.uuid4())
                if on_attempt:
                    on_attempt(current, dropped)
                continue
            delays = _BACKOFF.get(e.code)
            if not delays:
                raise
            backoffs[e.code] = backoffs.get(e.code, 0) + 1
            if backoffs[e.code] > len(delays):
                raise
            await sleep(delays[backoffs[e.code] - 1])
            continue
    assert last is not None
    raise last
```
- [ ] **Step 4: Run → green (fix the fake first). ruff clean. Commit:** `git add vjhstudio/runware tests/fakes tests/test_tasks.py tests/test_runner.py && git commit -m "feat: image task builder, result parsing and runner retry/fallback policy" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 3: Downloads, sidecars and thumbnails

**Files:**
- Create: `vjhstudio/runware/download.py`, `tests/test_download.py`

**Interfaces:**
- Consumes: `results.ResultItem`, httpx, Pillow.
- Produces:
  - `download.SavedFile(path: Path, size: int, url: str, item: ResultItem)`
  - `download.new_stem(now: datetime | None = None) -> str` → `YYYYMMDD-HHMMSS-<6 hex>`
  - `async download.download_items(items: list[ResultItem], dest_dir: Path, ext: str, *, transport: httpx.AsyncBaseTransport | None = None, retries: int = 2, timeout: float = 120.0, on_progress: Callable[[int, int], None] | None = None) -> list[SavedFile]` — streams to `<stem>.<ext>.part`, `os.replace` to final, retries `httpx.HTTPError` up to `retries`, raises `DownloadError` after; `on_progress(done, total)` per file.
  - `download.write_sidecar(media_path: Path, data: dict) -> Path` → `<stem>.json` next to the file (indent=2, atomic via `.part`).
  - `download.make_thumbnail(src: Path, dest: Path, max_px: int = 384) -> Path | None` (Pillow; RGB JPEG quality 85; returns None on failure without raising).

- [ ] **Step 1: Failing tests**
```python
# tests/test_download.py
import json, re
from pathlib import Path
import httpx, pytest
from PIL import Image
from vjhstudio.runware import download
from vjhstudio.runware.results import ResultItem

PNG = None

def _png_bytes() -> bytes:
    import io
    buf = io.BytesIO(); Image.new("RGB", (800, 600), (200, 30, 30)).save(buf, "PNG"); return buf.getvalue()

def _transport(fail_first: int = 0):
    calls = {"n": 0}
    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= fail_first:
            raise httpx.ConnectError("flaky")
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})
    return httpx.MockTransport(handler), calls

def test_stem_format():
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", download.new_stem())

async def test_download_writes_file_and_retries(tmp_path):
    t, calls = _transport(fail_first=1)
    items = [ResultItem("http://x/a.png", 1, 0.01, "u", False, {})]
    saved = await download.download_items(items, tmp_path, "png", transport=t)
    assert saved[0].path.exists() and saved[0].path.suffix == ".png" and saved[0].size > 100
    assert not list(tmp_path.glob("*.part")) and calls["n"] == 2

async def test_download_gives_up(tmp_path):
    t, _ = _transport(fail_first=10)
    with pytest.raises(download.DownloadError):
        await download.download_items([ResultItem("http://x/a.png", None, None, None, False, {})], tmp_path, "png", transport=t)

def test_sidecar_and_thumbnail(tmp_path):
    img = tmp_path / "20260920-120000-abcdef.png"
    Image.new("RGB", (800, 600), (0, 0, 255)).save(img)
    side = download.write_sidecar(img, {"prompt": "x", "seed": 1})
    assert side.name == "20260920-120000-abcdef.json" and json.loads(side.read_text())["seed"] == 1
    thumb = download.make_thumbnail(img, tmp_path / "t.jpg")
    with Image.open(thumb) as im:
        assert max(im.size) == 384 and im.format == "JPEG"
    assert download.make_thumbnail(tmp_path / "missing.png", tmp_path / "t2.jpg") is None
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement**
```python
# vjhstudio/runware/download.py
"""Fetch RunWare output URLs to disk (they expire), with sidecars and thumbnails."""
from __future__ import annotations
import json, os, secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import httpx
from .results import ResultItem


class DownloadError(Exception):
    pass


@dataclass(frozen=True)
class SavedFile:
    path: Path
    size: int
    url: str
    item: ResultItem


def new_stem(now: datetime | None = None) -> str:
    return f"{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


async def _fetch(client: httpx.AsyncClient, url: str, dest: Path) -> int:
    part = dest.with_name(dest.name + ".part")
    size = 0
    async with client.stream("GET", url) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            async for chunk in r.aiter_bytes(1024 * 1024):
                f.write(chunk)
                size += len(chunk)
    os.replace(part, dest)
    return size


async def download_items(items: list[ResultItem], dest_dir: Path, ext: str, *,
                         transport: httpx.AsyncBaseTransport | None = None, retries: int = 2,
                         timeout: float = 120.0, on_progress: Callable[[int, int], None] | None = None) -> list[SavedFile]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[SavedFile] = []
    async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=True) as client:
        for n, item in enumerate(items, 1):
            dest = dest_dir / f"{new_stem()}.{ext.lower()}"
            last: Exception | None = None
            for _ in range(retries + 1):
                try:
                    size = await _fetch(client, item.url, dest)
                    saved.append(SavedFile(dest, size, item.url, item))
                    last = None
                    break
                except httpx.HTTPError as e:
                    last = e
            if last is not None:
                raise DownloadError(f"could not download {item.url}: {last}") from last
            if on_progress:
                on_progress(n, len(items))
    return saved


def write_sidecar(media_path: Path, data: dict) -> Path:
    side = media_path.with_suffix(".json")
    part = side.with_name(side.name + ".part")
    part.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(part, side)
    return side


def make_thumbnail(src: Path, dest: Path, max_px: int = 384) -> Path | None:
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "JPEG", quality=85)
        return dest
    except Exception:  # noqa: BLE001
        return None
```
- [ ] **Step 4: Run → green; ruff clean. Commit:** `git add vjhstudio/runware/download.py tests/test_download.py && git commit -m "feat: output downloads with sidecars and thumbnails" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 4: Migration 0002, projects/costs/outputs services, generate service and the JobRunner

**Files:**
- Create: `migrations/versions/0002_phase3.py`, `vjhstudio/services/projects.py`, `vjhstudio/services/costs.py`, `vjhstudio/services/outputs.py`, `vjhstudio/services/generate.py`, `vjhstudio/services/jobs.py`, `tests/test_projects.py`, `tests/test_generate_service.py`, `tests/test_jobs.py`, `tests/test_outputs.py`
- Modify: `vjhstudio/models/job.py` (+ `expected_ms: Mapped[int | None]`, `seen_at: Mapped[datetime | None]`, `title: Mapped[str | None] = String(120)`), `vjhstudio/models/output.py` (+ `thumb_rel_path: Mapped[str | None] = String(300)`), `vjhstudio/config.py` (`Paths.thumbs = data/thumbs`, created by `ensure_dirs`), `vjhstudio/web/app.py` (lifespan starts/stops the runner; `app.state.runner`), `tests/conftest.py` (runner started with the fake client and `download_transport` from a fixture), `vjhstudio/services/settings.py` (+ `ui.notify_desktop: Spec(bool, False)`; `_cast` already handles bool)

**Interfaces:**
- Produces:
  - `projects.slugify(name) -> str` (python-slugify, max 60, fallback `project`), `projects.create(session, paths, name, description=None) -> Project` (unique slug by suffix `-2`, `-3`…; mkdir outputs/<slug>), `projects.list_active(session) -> list[Project]`, `projects.get(session, id)`, `projects.rename(session, id, name, description)`, `projects.set_archived(session, id, flag)`, `projects.dir_for(paths, slug) -> Path` (honours setting `paths.outputs_dir` when set), `projects.totals(session, project_id) -> dict(outputs:int, cost:float)`.
  - `costs.record_usage(session, *, job, task_type, cost, model_air) -> UsageEntry`, `costs.today_spend(session) -> float`, `costs.totals_by_project(session) -> dict[int, dict]`, `costs.observe_latency(session, model_air, ms) -> None` (rolling avg in `catalog_models.price_tiers_json["observed"] = {"avg_ms", "n"}`), `costs.expected_ms(session, model_air) -> int` (observed avg if n ≥ 1 else `tiers.typical_latency_ms` else 20000).
  - `outputs.record_outputs(session, paths, job, saved: list[SavedFile], params: dict, sidecar_extra: dict) -> list[Output]` (writes sidecar + thumbnail per file, inserts rows with `rel_path`, `thumb_rel_path`, seed/cost/size/width/height via Pillow), `outputs.gallery(session, *, project_id=None, kind=None, model=None, favourite=None, q=None, date_from=None, date_to=None, page=1, per_page=48) -> tuple[list[Output], int]`, `outputs.toggle_favourite(session, id)`, `outputs.delete(session, paths, id) -> bool` (file + sidecar + thumb + row), `outputs.remix_request(output) -> dict` (the `request_json` of its job with `seed` filled from the output), `outputs.abs_path(paths, output) -> Path`, `outputs.mark_missing(session, paths)`.
  - `generate.enqueue_image(session_factory, paths, req: ImageRequest, *, default_negative: str) -> Job` — validates the model exists in the catalog and is `kind == "image"` (`ValueError` "…is not an image model" otherwise) and the project exists; computes `negative = prompts.build_negative(req.form, default_negative, req.form.no_text)`, `title = req.title or compose(req.form)[:80] or "Untitled"`, `expected_ms = costs.expected_ms(...)`; inserts `Job(id=uuid4, kind="image", status="queued", model_air, request_json=req.model_dump() | {"negative": negative}, title, expected_ms)`; returns the Job (detached).
  - `jobs.JobRunner(session_factory, paths, *, client_factory, api_key_getter, transport_getter, concurrency, download_transport=None, catalog_family=catalog.family)` with `async start()`, `async stop()`, `submit(job_id)`, `cancel(job_id) -> bool`, `set_concurrency(n)`, `snapshot() -> dict[str, dict]` (in-memory `{id: {progress, stage, started_at}}`), `async wait_idle(timeout=10)` (tests), `active_ids() -> list[str]`.
    Pipeline `_run_job(job_id)`: load job → `running`, `started_at`, `attempts+1`, stage `submitting` → resolve family via catalog → build task via `tasks.build_image_task(req, job.id, media={}, family, negative)` → persist `task_json` → `runner.run_with_policy(client, task, timeout_s=setting runware.timeout_s, cancel_event, on_progress=…, on_attempt=persist task_json/dropped)` inside `async with client_factory(api_key, transport)` → stage `downloading` → `download.download_items(items, projects.dir_for(paths, slug), req.output_format)` → `outputs.record_outputs(...)` + `costs.record_usage` per item + `costs.observe_latency` → `succeeded`, `cost`, `progress=100`, `finished_at`. Exceptions: `RunwareError` → `classify` → `failed` (`aborted` → `cancelled`); `DownloadError` → `failed` code `download`; other → `failed` code `unknown` (log traceback). Progress writes to DB at most every 2 s per job plus on every stage change. Concurrency via `asyncio.Semaphore` swapped by `set_concurrency`. On `start()`, re-submit `BootInfo.requeued_jobs`.
  - Estimated progress helper: `jobs.estimate_progress(elapsed_ms: float, expected_ms: int) -> int` = `min(90, int(100 * (1 - exp(-elapsed_ms / max(expected_ms, 1000)))))`.

- [ ] **Step 1: Migration** — after editing the models, generate with the Alembic snippet from Phase 1 (`command.revision(alembic_config(<scratch db>), message="phase3", autogenerate=True, rev_id="0002")`, then review: it must add exactly `jobs.expected_ms`, `jobs.seen_at`, `jobs.title`, `outputs.thumb_rel_path`, all nullable, in batch mode). `tests/test_migrations.py::test_models_match_migrations` stays green.

- [ ] **Step 2: Failing tests** (representative; write all four files)
```python
# tests/test_projects.py
import pytest
from vjhstudio import boot, config, db, models
from vjhstudio.services import projects

@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory

def test_create_unique_slug_and_dir(env):
    paths, f = env
    with db.session_scope(f) as s:
        a = projects.create(s, paths, "My Project!")
        b = projects.create(s, paths, "my project")
        assert a.slug == "my-project" and b.slug == "my-project-2"
        assert (paths.outputs / "my-project-2").is_dir()
        assert [p.slug for p in projects.list_active(s)] == ["default", "my-project", "my-project-2"]
        projects.set_archived(s, b.id, True)
        assert [p.slug for p in projects.list_active(s)] == ["default", "my-project"]
        assert projects.totals(s, a.id) == {"outputs": 0, "cost": 0.0}
```
```python
# tests/test_generate_service.py
import pytest
from vjhstudio import boot, config, db, models
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import generate

@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory

def test_enqueue_creates_queued_job(env):
    paths, f = env
    with db.session_scope(f) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
    req = ImageRequest(project_id=pid, model="runware:101@1", form=PromptForm(subject="a fox", negative="blurry"))
    job = generate.enqueue_image(f, paths, req, default_negative="low quality")
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "queued" and j.kind == "image" and j.title == "a fox" and j.expected_ms == 2942
        assert j.request_json["negative"].startswith("blurry, low quality")

def test_enqueue_rejects_non_image_model_and_bad_project(env):
    paths, f = env
    with pytest.raises(ValueError, match="not an image model"):
        generate.enqueue_image(f, paths, ImageRequest(project_id=1, model="google:3@2", form=PromptForm(subject="x")), default_negative="")
    with pytest.raises(ValueError, match="project"):
        generate.enqueue_image(f, paths, ImageRequest(project_id=999, model="runware:101@1", form=PromptForm(subject="x")), default_negative="")
```
```python
# tests/test_jobs.py
import asyncio, json
import httpx, pytest
from PIL import Image
from runware import RunwareError
from vjhstudio import boot, config, db, models
from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import costs, generate, jobs
from tests.fakes.fake_runware import FakeRunware, fake_factory

def _png() -> bytes:
    import io
    b = io.BytesIO(); Image.new("RGB", (64, 64), (1, 2, 3)).save(b, "PNG"); return b.getvalue()

@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    return paths, info.session_factory, info

def _runner(env, fake, concurrency=2):
    paths, f, info = env
    t = httpx.MockTransport(lambda r: httpx.Response(200, content=_png()))
    return jobs.JobRunner(f, paths, client_factory=fake_factory(fake), api_key_getter=lambda: "key",
                          transport_getter=lambda: "rest", concurrency=concurrency, download_transport=t)

def _req(f):
    with db.session_scope(f) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
    return ImageRequest(project_id=pid, model="runware:101@1", form=PromptForm(subject="fox"), number_results=2)

async def test_job_succeeds_end_to_end(env):
    paths, f, _ = env
    fake = FakeRunware({"run": [[{"imageURL": "http://x/1.png", "seed": 1, "cost": 0.01}, {"imageURL": "http://x/2.png", "seed": 2, "cost": 0.01}]]})
    r = _runner(env, fake)
    await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id)
    await r.wait_idle()
    await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "succeeded" and j.progress == 100 and abs(j.cost - 0.02) < 1e-9
        outs = s.query(models.Output).filter_by(job_id=job.id).all()
        assert len(outs) == 2 and all((paths.outputs / o.rel_path).exists() for o in outs)
        assert all((paths.data / o.thumb_rel_path).exists() for o in outs)
        side = json.loads((paths.outputs / outs[0].sidecar_rel_path).read_text())
        assert side["job_id"] == job.id and side["seed"] == 1 and side["cost"] == 0.01
        assert s.query(models.UsageEntry).count() == 2 and costs.today_spend(s) > 0
        assert "observed" in s.query(models.CatalogModel).filter_by(air="runware:101@1").one().price_tiers_json

async def test_job_failure_is_classified(env):
    paths, f, _ = env
    fake = FakeRunware({"run": [RunwareError("invalidApiKey", "bad")]})
    r = _runner(env, fake); await r.start()
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(job.id); await r.wait_idle(); await r.stop()
    with db.session_scope(f) as s:
        j = s.get(models.Job, job.id)
        assert j.status == "failed" and j.error_code == "auth" and "Settings" in j.error_message

async def test_cancel_queued_and_running(env):
    paths, f, _ = env
    gate = asyncio.Event()
    class SlowFake(FakeRunware):
        async def run(self, params, options=None):
            await gate.wait()
            if options and options.cancel_event and options.cancel_event.is_set():
                raise RunwareError("aborted", "Request aborted")
            return [{"imageURL": "http://x/1.png"}]
    fake = SlowFake({})
    r = _runner(env, fake, concurrency=1); await r.start()
    j1 = generate.enqueue_image(f, paths, _req(f), default_negative="")
    j2 = generate.enqueue_image(f, paths, _req(f), default_negative="")
    r.submit(j1.id); r.submit(j2.id)
    await asyncio.sleep(0.05)
    assert r.cancel(j2.id) is True   # still queued -> cancelled directly
    assert r.cancel(j1.id) is True   # running -> event set
    gate.set()
    await r.wait_idle(); await r.stop()
    with db.session_scope(f) as s:
        assert s.get(models.Job, j1.id).status == "cancelled" and s.get(models.Job, j2.id).status == "cancelled"

async def test_requeued_jobs_run_on_start(env):
    paths, f, info = env
    job = generate.enqueue_image(f, paths, _req(f), default_negative="")
    info2 = boot.boot(paths)
    assert job.id in info2.requeued_jobs
    fake = FakeRunware({"run": [[{"imageURL": "http://x/1.png"}]]})
    r = _runner((paths, f, info2), fake); await r.start(requeue=info2.requeued_jobs)
    await r.wait_idle(); await r.stop()
    with db.session_scope(f) as s:
        assert s.get(models.Job, job.id).status == "succeeded"

def test_estimate_progress():
    assert jobs.estimate_progress(0, 10000) == 0
    assert 55 <= jobs.estimate_progress(10000, 10000) <= 65
    assert jobs.estimate_progress(10**9, 10000) == 90
```
```python
# tests/test_outputs.py — gallery filters, favourite, delete, remix
import pytest
from vjhstudio import boot, config, db, models
from vjhstudio.services import outputs

@pytest.fixture
def env(tmp_path):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot.boot(paths)
    with db.session_scope(info.session_factory) as s:
        pid = s.query(models.Project).filter_by(slug="default").one().id
        s.add(models.Job(id="j1", project_id=pid, kind="image", status="succeeded", model_air="m1",
                         request_json={"model": "m1", "form": {"subject": "fox"}, "width": 512, "height": 512}))
        for i in range(3):
            p = paths.outputs / "default" / f"20260920-12000{i}-aaaaaa.png"
            p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")
            s.add(models.Output(job_id="j1", project_id=pid, kind="image", filename=p.name, rel_path=f"default/{p.name}",
                                sidecar_rel_path=f"default/{p.stem}.json", model_air="m1", prompt_text=f"fox {i}",
                                params_json={}, seed=i, is_favourite=(i == 1)))
    return paths, info.session_factory, pid

def test_gallery_filters_and_paging(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        rows, total = outputs.gallery(s, project_id=pid)
        assert total == 3 and [o.seed for o in rows] == [2, 1, 0]
        assert outputs.gallery(s, favourite=True)[1] == 1
        assert outputs.gallery(s, q="fox 2")[1] == 1
        assert outputs.gallery(s, model="nope")[1] == 0
        assert len(outputs.gallery(s, page=2, per_page=2)[0]) == 1

def test_favourite_delete_remix(env):
    paths, f, pid = env
    with db.session_scope(f) as s:
        o = outputs.gallery(s)[0][0]
        assert outputs.toggle_favourite(s, o.id).is_favourite is True
        req = outputs.remix_request(o)
        assert req["seed"] == o.seed and req["form"]["subject"] == "fox"
        path = outputs.abs_path(paths, o)
        assert outputs.delete(s, paths, o.id) is True and not path.exists()
        assert outputs.gallery(s)[1] == 2
```
- [ ] **Step 3: Implement** the five services as specified in Interfaces (write them fully; the implementer is a skilled developer — the behaviours and signatures above are the contract, the tests are the acceptance). Key details:
  - `jobs.JobRunner._run_job` throttles DB progress writes with a per-job monotonic timestamp; `snapshot()` is merged over DB rows by the routes in Task 5.
  - `wait_idle(timeout)` awaits until the queue is empty and no `_run_job` task is live.
  - `stop()` sets every cancel event and awaits the in-flight tasks for up to 10 s.
  - `start(requeue: list[str] | None = None)` starts the dispatcher task and submits `requeue` ids.
  - `app.py` lifespan: after boot, build the runner with `client_factory=app.state.client_factory`, `api_key_getter=app.state.api_key`, `transport_getter=lambda: app.state.setting("runware.transport")`, `concurrency=app.state.setting("jobs.concurrency")`, `download_transport=getattr(app.state, "download_transport", None)`; `await runner.start(requeue=boot.requeued_jobs)`; on shutdown `await runner.stop()`. `create_app` gains `download_transport=None` param stored on `app.state`. Settings save of `jobs.concurrency` calls `app.state.runner.set_concurrency`.
  - `conftest.py`: `app` fixture passes `download_transport=httpx.MockTransport(lambda r: httpx.Response(200, content=<64x64 png bytes>))`.
- [ ] **Step 4: Run → green; ruff clean. Commit:** `git add -A && git commit -m "feat: job runner, projects/costs/outputs services, generate service, migration 0002" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 5: Generate page, queue panel, jobs routes and file serving

**Files:**
- Create: `vjhstudio/web/routes/generate.py`, `vjhstudio/web/routes/jobs.py`, `vjhstudio/web/routes/files.py`, templates `pages/generate.html`, `generate/_prompt_builder.html`, `generate/_model_params.html`, `generate/_project_select.html`, `generate/_queue_panel.html`, `generate/_job_card.html`, `generate/_estimate.html`, `partials/_jobs_badge.html`, `tests/test_web_generate.py`, `tests/test_web_jobs.py`
- Modify: `vjhstudio/web/app.py` (include routers), `_header.html` (nav: Home, Generate, Gallery, Projects, Models, Settings; jobs badge next to the balance chip), `pages/index.html` (link to Generate), `static/css/app.css` (3-column generate grid collapsing under 1100 px, job cards, progress bar colours), `static/js/app.js` (see Task 7 for the prompt mirror; here only the `job-finished` handler stub is not needed yet)

**Routes:**
| Method | Path | Behaviour |
|---|---|---|
| GET | `/generate` | page; query `?remix=<output_id>` or `?prompt=<id>` (Phase 5) pre-fills; renders `initial` JSON in `<script id="generate-initial" type="application/json">` |
| POST | `/generate/image` | form → `ImageRequest` (fields `project_id, model, subject…extras, negative, use_default_negative, no_text, final_prompt, width, height, number_results, seed, steps, cfg_scale, scheduler, output_format, title, extra_json`); 422 re-renders `generate/_model_params.html` with `errors` (`HX-Retarget: #model-params`); success → `generate.enqueue_image` + `runner.submit` → returns `generate/_queue_panel.html` with `HX-Trigger: jobs-changed` |
| POST | `/hx/prompt/compose` | form → `{"composed": compose(form), "negative": build_negative(...)}` rendered as a small partial (used as a server-side check; the live preview is client JS) |
| GET | `/hx/model-options?air=&mode=image` | `generate/_model_params.html` for that model: family, defaults (`default_width/height/steps/cfg`), size presets (1024x1024, 1152x896, 896x1152, 1344x768, 768x1344, custom), steps/CFG/scheduler shown only for diffusion, seed + lock, results 1–8, output format; `capabilities` note |
| GET | `/hx/generate/estimate?air=&width=&height=&number_results=` | `generate/_estimate.html`: `≈ $x.xxxx` = `price_primary × (w·h/1024²) × n` for images (n/a when unknown) |
| GET | `/hx/jobs/active` | `generate/_queue_panel.html`: queued/running jobs first (with in-memory snapshot merged: progress, stage) then the last 10 finished; the panel's `hx-trigger` is `every 2s, jobs-changed from:body` while any job is active, else `jobs-changed from:body`; when a job finished since the previous poll (`finished_at` newer than `seen_at` and `seen_at` null), the response sets `HX-Trigger` JSON `{"job-finished": {...}}` for each and stamps `seen_at`; the response also includes `partials/_jobs_badge.html` OOB |
| GET | `/hx/jobs/{id}` | one `generate/_job_card.html` |
| POST | `/jobs/{id}/cancel` | `runner.cancel`; returns the card |
| POST | `/jobs/{id}/retry` | new job from `request_json` (same request, new id) → queue panel |
| POST | `/jobs/seen` | marks all finished jobs seen (badge reset) |
| GET | `/api/jobs?status=&limit=` , `/api/jobs/{id}` | JSON views (`id, kind, status, title, model_air, progress, stage, expected_ms, elapsed_ms, cost, error_code, error_message, created_at, started_at, finished_at, outputs:[{id, thumb_url, url}]`) |
| GET | `/files/outputs/{slug}/{filename}`, `/files/thumbs/{name}` | `FileResponse` with traversal guard (resolve under the outputs/thumbs dir else 404); outputs served with `Content-Disposition: inline`; `?download=1` → attachment |

**Progress shown per card:** `progress = real if job.progress > 0 and reported-by-runware else estimate_progress(elapsed, expected_ms)` (the runner marks `status_text="rendering"`; when RunWare gave a real percentage the snapshot carries `real=True`); `<progress value=… max=100>`; stage label + elapsed + "~Ns left" when estimated (`expected_ms - elapsed`, floor 0); failed/cancelled cards get class `danger`.

- [ ] **Step 1: Failing tests** (the `client` fixture runs the lifespan; the runner is started with the fake client + PNG mock transport from Task 4's conftest change)
```python
# tests/test_web_generate.py
import asyncio
from runware import RunwareError

FORM = {"project_id": "1", "model": "runware:101@1", "subject": "a red fox", "style": "oil painting",
        "width": "1024", "height": "1024", "number_results": "1", "output_format": "PNG", "no_text": "on", "use_default_negative": "on"}

async def test_generate_page_renders(client):
    r = await client.get("/generate")
    assert r.status_code == 200 and 'id="generate-form"' in r.text and 'id="queue-panel"' in r.text
    assert 'name="model"' in r.text and "FLUX.1 [dev]" in r.text and 'id="generate-initial"' in r.text

async def test_model_options_diffusion_vs_instruction(client):
    r = await client.get("/hx/model-options?air=runware:101@1")
    assert 'name="steps"' in r.text and 'value="28"' in r.text
    r = await client.get("/hx/model-options?air=google:4@2")
    assert 'name="steps"' not in r.text and "instruction" in r.text

async def test_estimate(client):
    r = await client.get("/hx/generate/estimate?air=runware:101@1&width=1024&height=1024&number_results=2")
    assert "$0.0076" in r.text
    r = await client.get("/hx/generate/estimate?air=runware:100@1&width=1024&height=1024&number_results=1")
    assert "n/a" in r.text

async def test_submit_requires_key_and_validates(client):
    r = await client.post("/generate/image", data=FORM)
    assert r.status_code == 422 and "API key" in r.text
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    r = await client.post("/generate/image", data={**FORM, "width": "1000"})
    assert r.status_code == 422 and "multiple of 64" in r.text and r.headers.get("HX-Retarget") == "#model-params"

async def test_submit_runs_job_and_queue_shows_result(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png", "seed": 5, "cost": 0.004}]]
    r = await client.post("/generate/image", data=FORM)
    assert r.status_code == 200 and "jobs-changed" in r.headers.get("HX-Trigger", "") and "a red fox" in r.text
    await app.state.runner.wait_idle()
    r = await client.get("/hx/jobs/active")
    assert "succeeded" in r.text and "/files/thumbs/" in r.text and "$0.0040" in r.text
    assert "job-finished" in r.headers.get("HX-Trigger", "")
    r2 = await client.get("/hx/jobs/active")
    assert "job-finished" not in r2.headers.get("HX-Trigger", "")   # seen now
    jobs = (await client.get("/api/jobs")).json()
    assert jobs[0]["status"] == "succeeded" and jobs[0]["outputs"][0]["thumb_url"].startswith("/files/thumbs/")
    thumb = await client.get(jobs[0]["outputs"][0]["thumb_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"].startswith("image/")
    full = await client.get(jobs[0]["outputs"][0]["url"] + "?download=1")
    assert "attachment" in full.headers.get("content-disposition", "")

async def test_failed_job_card_and_retry(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [RunwareError("invalidApiKey", "bad"), [{"imageURL": "http://x/1.png"}]]
    await client.post("/generate/image", data=FORM)
    await app.state.runner.wait_idle()
    r = await client.get("/hx/jobs/active")
    assert "failed" in r.text and "rejected the API key" in r.text and "Retry" in r.text
    jid = (await client.get("/api/jobs")).json()[0]["id"]
    r = await client.post(f"/jobs/{jid}/retry")
    assert r.status_code == 200
    await app.state.runner.wait_idle()
    assert (await client.get("/api/jobs")).json()[0]["status"] == "succeeded"

async def test_files_traversal_guarded(client):
    assert (await client.get("/files/outputs/default/..%2F..%2Fvjh.db")).status_code in (400, 404)
    assert (await client.get("/files/thumbs/../vjh.db")).status_code in (400, 404)

async def test_remix_prefills_initial(client, fake, app):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": "http://x/1.png", "seed": 77}]]
    await client.post("/generate/image", data=FORM)
    await app.state.runner.wait_idle()
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/generate?remix={oid}")
    assert '"seed": 77' in r.text and '"subject": "a red fox"' in r.text
```
```python
# tests/test_web_jobs.py
async def test_active_panel_polls_only_when_active(client):
    r = await client.get("/hx/jobs/active")
    assert 'hx-trigger="jobs-changed from:body"' in r.text and "every 2s" not in r.text

async def test_cancel_unknown_404(client):
    assert (await client.post("/jobs/nope/cancel")).status_code == 404

async def test_jobs_badge_oob(client):
    r = await client.get("/hx/jobs/active")
    assert 'id="jobs-badge"' in r.text and 'hx-swap-oob="true"' in r.text
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** routes and templates per the tables above. Layout of `pages/generate.html`: `<div class="gen-grid">` with three columns — left `<form id="generate-form" hx-post="/generate/image" hx-target="#queue-panel" hx-swap="outerHTML" hx-indicator="#gen-spinner">` containing `generate/_prompt_builder.html` (the nine fields inside `x-data="generateForm(initial)"`, a live `<output x-text="composed">` preview, negative preview, "No text in image" and "Use default negative" checkboxes, a `<textarea name="final_prompt" x-model="finalPrompt" placeholder="Leave empty to use the composed prompt">`), middle `#model-params` (`generate/_model_params.html` fetched with `hx-get="/hx/model-options" hx-trigger="load, change from:#model-select" hx-include="#model-select"`) with the model `<select id="model-select" name="model">` (price-sorted, `catalog/_model_select.html`), project select (`generate/_project_select.html`, with inline "New project" input posting to `/projects` — Task 6), estimate span (`hx-get="/hx/generate/estimate" hx-trigger="load, change from:#generate-form delay:400ms" hx-include="#generate-form"`), Generate button (disabled when no key with a banner linking to Settings), right `generate/_queue_panel.html`. `initial` JSON comes from `?remix` (`outputs.remix_request`) or `{}`. Job card shows title, model name, status pill, `<progress>`, stage/elapsed/ETA line, thumbnails (`<a href="/files/outputs/..."><img src="/files/thumbs/..."></a>`), cost, Cancel/Retry/Remix buttons, `<details>` with the error and dropped params.
- [ ] **Step 4: Run → green; ruff clean; smoke** (`serve` on a spare port with `data-debug`, `curl /generate | grep -c generate-form` = 1). **Commit:** `git add -A && git commit -m "feat: Generate page with job queue, progress, jobs API and file serving" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 6: Gallery, Projects pages, notifications toggle

**Files:**
- Create: `vjhstudio/web/routes/gallery.py`, `vjhstudio/web/routes/projects.py`, templates `pages/gallery.html`, `gallery/_grid.html`, `gallery/_card.html`, `gallery/_detail.html`, `pages/projects.html`, `projects/_row.html`, `tests/test_web_gallery.py`, `tests/test_web_projects.py`
- Modify: `settings/_general_form.html` (checkbox for `ui.notify_desktop` + "Test notification" button calling `vjhTestNotification()`), `routes/settings.py` (`_general_ctx` unchanged; `POST /settings` treats missing bool keys as "false" when the form was submitted from the general form: include a hidden `<input name="_form" value="general">` and, when present, set every bool SPEC key absent from the form to "false"), `_header.html` (Gallery/Projects links already added in Task 5)

**Routes:**
| Method | Path | Behaviour |
|---|---|---|
| GET | `/gallery` | page with filter form (project, kind, model, from, to, favourite, q) and first grid |
| GET | `/hx/gallery?…&page=` | `gallery/_grid.html` (48/page, "Load more" button replaced by next page) |
| GET | `/hx/outputs/{id}` | `gallery/_detail.html` for the lightbox: full image, prompt, negative, params table, seed, cost, job link, buttons Download / Favourite / Remix (`/generate?remix=`) / Copy path / Delete |
| POST | `/outputs/{id}/favourite` | returns `gallery/_card.html` |
| DELETE | `/outputs/{id}` | deletes file+sidecar+thumb+row; returns empty 200 with `HX-Trigger: close-lightbox` |
| GET | `/outputs/{id}/download` | 302 to `/files/outputs/<rel>?download=1` |
| GET | `/projects` | table (name, slug, outputs, cost, folder path + copy button, Rename, Archive) + create form |
| POST | `/projects` | create → `projects/_row.html` (afterbegin) + OOB toast; when called with `hx-target="#project-select"` (`?return=select`) returns `generate/_project_select.html` with the new project selected |
| POST | `/projects/{id}` | rename/description → row |
| POST | `/projects/{id}/archive` | toggle → row (or empty when hiding archived) |
| GET | `/hx/projects/select` | `generate/_project_select.html` |

- [ ] **Step 1: Failing tests** (write full files; representative cases):
```python
# tests/test_web_gallery.py
FORM = {"project_id": "1", "model": "runware:101@1", "subject": "castle", "width": "1024", "height": "1024", "number_results": "1", "output_format": "PNG"}

async def _make(client, fake, app, n=1):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["run"] = [[{"imageURL": f"http://x/{i}.png", "seed": i, "cost": 0.001} for i in range(n)]]
    await client.post("/generate/image", data={**FORM, "number_results": str(n)})
    await app.state.runner.wait_idle()

async def test_gallery_lists_and_filters(client, fake, app):
    await _make(client, fake, app, n=3)
    r = await client.get("/gallery")
    assert r.status_code == 200 and r.text.count("/files/thumbs/") == 3
    r = await client.get("/hx/gallery?q=castle&kind=image&project_id=1")
    assert r.text.count("/files/thumbs/") == 3
    assert (await client.get("/hx/gallery?q=zzz")).text.count("/files/thumbs/") == 0

async def test_detail_favourite_delete(client, fake, app):
    await _make(client, fake, app)
    oid = (await client.get("/api/jobs")).json()[0]["outputs"][0]["id"]
    r = await client.get(f"/hx/outputs/{oid}")
    assert "castle" in r.text and "Remix" in r.text and "Delete" in r.text
    assert "★" in (await client.post(f"/outputs/{oid}/favourite")).text
    assert (await client.get("/hx/gallery?favourite=1")).text.count("/files/thumbs/") == 1
    r = await client.delete(f"/outputs/{oid}")
    assert r.status_code == 200 and "close-lightbox" in r.headers.get("HX-Trigger", "")
    assert (await client.get(f"/hx/outputs/{oid}")).status_code == 404
```
```python
# tests/test_web_projects.py
async def test_projects_crud(client):
    r = await client.get("/projects")
    assert r.status_code == 200 and "default" in r.text
    r = await client.post("/projects", data={"name": "Book covers"})
    assert r.status_code == 200 and "book-covers" in r.text
    pid = [p for p in (await client.get("/api/projects")).json() if p["slug"] == "book-covers"][0]["id"]
    r = await client.post(f"/projects/{pid}", data={"name": "Book Covers 2"})
    assert "Book Covers 2" in r.text
    r = await client.post(f"/projects/{pid}/archive")
    assert r.status_code == 200
    assert all(p["slug"] != "book-covers" for p in (await client.get("/api/projects")).json())
    r = await client.post("/projects?return=select", data={"name": "Quick"})
    assert '<select' in r.text and 'selected' in r.text and "Quick" in r.text

async def test_notify_toggle_saves(client):
    r = await client.post("/settings", data={"_form": "general", "ui.notify_desktop": "on", "jobs.concurrency": "3"})
    assert r.status_code == 200
    r = await client.get("/settings")
    assert 'name="ui.notify_desktop"' in r.text and "checked" in r.text
    await client.post("/settings", data={"_form": "general", "jobs.concurrency": "3"})
    assert 'name="ui.notify_desktop" checked' not in (await client.get("/settings")).text
```
(also add `GET /api/projects` → JSON `[{id, name, slug, outputs, cost, is_archived}]` in `routes/projects.py`.)
- [ ] **Step 2: Run → fail. Step 3: Implement. Step 4: Run → green; ruff clean. Commit:** `git add -A && git commit -m "feat: gallery with lightbox, projects page, desktop-notification setting" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 7: Client-side prompt mirror, progress bar polish, notifications and lightbox JS

**Files:**
- Modify: `vjhstudio/web/static/js/app.js` (add `composePrompt`, `generateForm(initial)` Alpine component, `job-finished` handler → toast + `Notification`, `vjhTestNotification()`, lightbox open/close + arrow keys, `close-lightbox` handler), `static/css/app.css`, `pages/gallery.html` (dialog markup), `generate/_prompt_builder.html` (Alpine bindings), `README.md` ("Make your first image" section: 4 steps)
- Create: `tests/test_js_mirror.py` — runs the fixture cases through the JS `composePrompt` with `node` when available (`shutil.which("node")`), else skipped: `node -e "const {composePrompt}=require('./vjhstudio/web/static/js/compose.js'); ..."` — to make this testable, put `composePrompt`/`clean` in a separate `static/js/compose.js` that works both as a browser script and a CommonJS module (`if (typeof module !== 'undefined') module.exports = {composePrompt, clean};`), loaded by base.html before app.js.

**Rules:** `compose.js` mirrors `services/prompts.py` exactly: `ORDER`, `clean = s => (s ?? '').replace(/\s+/g, ' ').trim().replace(/[ ,;.]+$/, '').trim()`, dedupe case-insensitive, join `', '`. `generateForm(initial)` state: `fields`, `finalPrompt`, `noText`, `useDefaultNegative`, `composed` getter, `negativePreview` (client-side: user tokens + default tokens string passed from the server as `defaultNegative` + the no-text tokens), `init()` loads `initial` (fields, finalPrompt, model/params are server-rendered) or the localStorage draft `vjh.generate.draft`; `$watch` saves the draft (debounced 300 ms); on successful submit (htmx `afterRequest` with 200) clear `finalPrompt` only.
`job-finished` handler: `document.body.addEventListener('job-finished', e => { const d = e.detail; toast(`${d.title}: ${d.status}`, d.status === 'succeeded' ? 'ok' : 'error', d.thumb); if (window.Notification && Notification.permission === 'granted' && document.documentElement.dataset.notify === '1') new Notification('VJHStudio', {body: `${d.title}: ${d.status}`, icon: d.thumb || '/static/img/icon.png'}); })` — `base.html` sets `data-notify="{{ '1' if notify_desktop else '0' }}"` (from the `ui.notify_desktop` setting via `render()` globals). `vjhTestNotification()` requests permission then fires a sample.
Lightbox: `<dialog id="lightbox">` in `pages/gallery.html`; cards use `hx-get="/hx/outputs/{id}" hx-target="#lightbox-body"` + `onclick="vjhOpenLightbox()"`; `close-lightbox` event closes it; ArrowLeft/ArrowRight click `.lb-prev/.lb-next` if present; Esc native.

- [ ] **Step 1: Failing test** `tests/test_js_mirror.py` (skips without node) + a Python test that `base.html` includes `compose.js` before `app.js` and `data-notify`.
- [ ] **Step 2–4: implement, run (with node if installed: `node --version`), ruff clean, commit** `feat: client-side prompt mirror, notifications, lightbox`.

---

## Phase 3 exit criteria

- With a real key: Generate → pick FLUX.1 [dev] → type a subject → Generate: the card shows a moving progress bar with stage text, then the thumbnail; the file and its `.json` sidecar are in `data/outputs/default/`; a toast (and a desktop notification when enabled) announces completion; the header balance drops and the cost appears on the card and the project row.
- Gallery filters, lightbox, favourite, remix (form pre-filled incl. seed), download and delete work.
- Cancel stops waiting on a running job; a failed job shows the RunWare message and Retry works.
- `uv run pytest -q` green on the whole suite; ruff clean; `vjhstudio doctor` unaffected.
