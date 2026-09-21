# VJHStudio Phase 5 (Prompt library & polish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every prompt the app builds is kept, findable and reusable — saved by hand or recorded automatically when a job runs — and the builder can hand its composed text to RunWare to be rewritten, with the polished versions offered as cards and the chosen one stored on the prompt.

**Architecture:** `services/prompts.py` grows a library half on top of the existing composition half: `upsert` writes a `Prompt` row keyed by a `content_hash` of (kind, composed, final, negative, form) scoped to the project, so `generate.enqueue_image/enqueue_video` can call `prompts.for_request` on every submit and link `jobs.prompt_id` without ever creating a second row for the same text — "used N times" is `use_count`. `services/polish.py` is a thin async service: build a task (`runware/tasks.py::build_prompt_enhance` / `build_polish_text`), run it through the existing `runner.run_with_policy` (retry + classified errors for free) inside `client_factory`, parse the SDK's `list[dict]` into versions, and hand back a `PolishResult` the route renders as cards and `costs.record_usage` books as a `promptEnhance`/`textInference` usage row. The Generate page gains a polish block in the prompt column (outside both mode fieldsets), a hidden `prompt_id`, a Save-prompt dialog, and a `?prompt=<id>` prefill that reuses the `initial` blob `_page` already speaks.

**Tech Stack:** as before. No new dependencies: `hashlib`/`json` for the hash, the vendored htmx/Alpine for the cards and dialog.

**Spec:** `docs/superpowers/specs/2026-09-20-runwarestudio-design.md` — "Prompt composition" (polish modes), "Model dropdowns (image / video / polish-text)", "Data model → prompts", "UI design → Generate (Polish with AI, Save prompt), Prompts page", "Verified facts about runware-sdk" (`promptEnhance` = `{model: runware:llama-3-1-8b@prompt-enhancer, prompt ≤300 chars, promptMaxLength 12-400, promptVersions 1-5}`).

## Global Constraints

- Package `vjhstudio`; commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .` clean before every commit; `runware/` and `services/` never import `web/`; DB-only handlers stay sync `def`, handlers that `await` are `async def` (pinned by an `inspect.iscoroutinefunction` test, as in `tests/test_web_assets.py`); no CDN; no network in tests (FakeRunware scripts `run`; `httpx.MockTransport` for downloads).
- Tags use the asset convention verbatim: `,a,b,` normalized, filtered with `LIKE '%,tag,%'`. `services/prompts.py` re-exports `normalize_tags`/`tags_list`/`_like_escape` from `services/assets.py` — one implementation app-wide, no second copy to drift.
- Dedupe key: `content_hash = sha256(json.dumps([kind, composed, final, negative, form_canonical], sort_keys=True, separators=(",", ":")))` hex, where `form_canonical` is the `PromptForm` dump minus empty strings. Scope is `(project_id, content_hash)`; the **lowest id** match wins, so a `duplicate` copy never shadows the original it was cloned from.
- `promptEnhance`: `{"taskType": "promptEnhance", "taskUUID": …, "model": "runware:llama-3-1-8b@prompt-enhancer", "prompt": <composed truncated to 300 chars, suffix-safe via prompts.cap>, "promptMaxLength": 300, "promptVersions": <1..3>, "includeCost": True}`. The SDK returns `list[dict]`, **one row per version**, each `{text, cost}`.
- `textInference`: `{"taskType": "textInference", "taskUUID": …, "model": <chosen text AIR>, "messages": [{"role": "system", "content": POLISH_SYSTEM}, {"role": "user", "content": <composed>}], "outputFormat": "TEXT", "includeCost": True}`; reply rows are `{text, cost, finishReason, usage}`. **Decision: one call, numbered lines** (not N calls, not `numberResults`): one round trip, one cost row, and `numberResults` is unverified across text providers — the runner's unsupported-parameter fallback would silently drop it and return one version anyway. `POLISH_SYSTEM` = `"Rewrite the following into a single vivid image/video generation prompt of at most 120 words. Output only the prompt."`; when >1 version is asked for, `" Give N alternatives, each on its own line numbered 1., 2., 3., and nothing else."` is appended, and the reply text is split on `^\s*\d+[.)]\s*` (no numbering found → the whole text is version 1).
- Polish writes nothing by itself (spec): `polish_json` reaches a `Prompt` row only through Save-prompt or a Generate submit that carries it.
- Polish runs inline in an `async def` route, `timeout_s=30` per attempt through `runner.run_with_policy` (no new retry code). No API key → 422 `generate/_no_key.html` retargeted to `#polish-results`; empty composed → 422 with an inline message; `RunwareError` → `errors.classify()` message, 422, same partial. htmx swaps 422 by config (`base.html` meta), so every failure lands in the panel.
- Cost: one `usage_entries` row per successful polish, `task_type` = `"promptEnhance"` or `"textInference"`, `model_air` = the model used, `cost` = sum of the rows' costs (0.0 when absent), `project_id` from the posted project.
- Migration `0004_phase5` generated with the Phase 1 Alembic snippet (`command.revision(alembic_config(<scratch db>), message="phase5", autogenerate=True, rev_id="0004")`, batch mode), reviewed to contain exactly: `prompts.tags`, `prompts.content_hash`, `ix_prompts_tags`, `ix_prompts_hash`. `tests/test_migrations.py::test_models_match_migrations` stays green.
- Price-sorted model selects always go through `catalog/_model_select.html`; the polish select uses `allow_empty=True` (its empty option already reads "(use promptEnhance)") and `catalog.list_models(session, "text")` + `catalog.label`.

## File Structure

```
vjhstudio/models/prompt.py              + tags, content_hash, ix_prompts_tags, ix_prompts_hash
vjhstudio/services/prompts.py           + content_hash, upsert, for_request, mark_used, get, list_prompts,
                                          set_favourite, set_tags, set_title, duplicate, delete, to_initial, PER_PAGE
vjhstudio/services/polish.py            PolishVersion, PolishResult, clamp_versions, split_versions, polish(), record_cost()
vjhstudio/services/generate.py          + polish_json kwarg; auto-history links jobs.prompt_id
vjhstudio/services/costs.py             + record_usage(project_id=…) for job-less usage
vjhstudio/runware/tasks.py              + PROMPT_ENHANCE_MODEL, POLISH_SYSTEM, build_prompt_enhance, build_polish_text
vjhstudio/runware/results.py            TaskResult + rows: list[dict] (raw replies; parse_items drops text-only rows)
migrations/versions/0004_phase5.py
vjhstudio/web/routes/prompts.py         GET /prompts ; GET /hx/prompts ; POST /prompts ; POST /prompts/{id}/favourite|duplicate|tags ; DELETE /prompts/{id}
vjhstudio/web/routes/generate.py        + POST /hx/prompt/polish ; ?prompt=<id> prefill ; polish/save context ; polish_json on submit
vjhstudio/web/templates/pages/prompts.html, prompts/_list.html, _list_page.html, _row.html
vjhstudio/web/templates/generate/_polish.html, _polish_results.html, _save_prompt.html
vjhstudio/web/templates/_header.html    nav: Prompts between Gallery and Assets
vjhstudio/web/static/js/app.js          generateForm: promptId, polishJson, polish state, usePolish, savePrompt, draft guard
tests/test_prompt_library.py, tests/test_polish.py, tests/test_web_prompts.py, tests/test_js_generate_form.py
```

---

### Task 1: Prompt library service and migration 0004

**Files:** Modify `vjhstudio/models/prompt.py`, `vjhstudio/services/prompts.py`; Create `migrations/versions/0004_phase5.py`, `tests/test_prompt_library.py`.

**Interfaces (produces):**
- `models.Prompt`: `tags: Mapped[str] = mapped_column(String(400), default=",", nullable=False)`, `content_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)`; `__table_args__` gains `Index("ix_prompts_tags", "tags")` and `Index("ix_prompts_hash", "project_id", "content_hash")`.
- `prompts.PER_PAGE = 30`; `prompts.normalize_tags`, `prompts.tags_list` (re-exported from `services.assets`).
- `prompts.content_hash(kind: str, composed: str, final: str, negative: str, form: PromptForm | dict) -> str`
- `prompts.upsert(session, *, project_id: int | None, kind: str, title: str, form: PromptForm | dict, final_prompt: str, negative_prompt: str = "", tags: str = "", polish_json: dict | None = None, final_edited: bool = False) -> tuple[Prompt, bool]` — composes internally, hashes, returns the existing row (`created=False`) when `(project_id, content_hash)` already exists, merging tags (`normalize_tags(old + "," + new)`) and overwriting `polish_json` only when a new one is given; never touches `use_count`.
- `prompts.for_request(session, req: ImageRequest | VideoRequest, *, kind: str, negative: str = "", title: str | None = None, tags: str = "", polish_json: dict | None = None) -> Prompt` — auto-history: reuses `req.prompt_id`'s row when it exists **and** its `content_hash` still matches, else `upsert`s; then `mark_used`.
- `prompts.mark_used(session, prompt_id: int) -> Prompt | None` (`use_count += 1`, `last_used_at = utcnow()`)
- `prompts.get(session, prompt_id: int) -> Prompt | None`
- `prompts.list_prompts(session, *, q: str | None = None, kind: str | None = None, favourite: bool | None = None, project_id: int | None = None, tag: str | None = None, page: int = 1, per_page: int = PER_PAGE) -> tuple[list[Prompt], int]` — newest first; `q` matches `title`/`composed_prompt`/`final_prompt` (escaped LIKE).
- `prompts.set_favourite(session, prompt_id, value: bool | None = None) -> Prompt` (toggle when `None`), `prompts.set_tags(session, prompt_id, tags: str) -> Prompt`, `prompts.set_title(session, prompt_id, title: str) -> Prompt`
- `prompts.duplicate(session, prompt_id) -> Prompt` — same content and hash, title `"<title> (copy)"`, `use_count=0`, `last_used_at=None`, `is_favourite=False`; bypasses dedupe deliberately.
- `prompts.delete(session, prompt_id) -> bool` — jobs keep their history (`jobs.prompt_id` FK is `ON DELETE SET NULL`).
- `prompts.to_initial(p: Prompt) -> dict` — `{"form", "final_prompt", "prompt_id", "project_id", "title", "mode"}`, the exact shape `_page`/`generateForm` already consume.

**Tests (`tests/test_prompt_library.py`; boot a tmp DB as `tests/test_projects.py` does):** identical form+final in the same project hashes the same and `upsert` twice → one row, `created` True then False; a different project → two rows; tags merge on the second upsert; `polish_json=None` on a re-upsert keeps the stored blob; `for_request` on an `ImageRequest` creates + bumps `use_count` to 1, a second identical request bumps to 2 with no new row; `for_request` with a stale `prompt_id` whose text was edited creates a new row and leaves the old `use_count` alone; `list_prompts` filters q/kind/favourite/project/tag and pages; `duplicate` returns a second row with the same hash and `list_prompts`' q still finds both, and a later `upsert` of that text returns the **original** (lowest id); `delete` returns True then False, and a job that referenced it survives with `prompt_id is None`; `to_initial` round-trips a saved video prompt to `mode == "video"`.

- [ ] Write tests → fail → implement → generate migration 0004 with the Alembic snippet → review it adds exactly the two columns and two indexes → `test_models_match_migrations` green → suite green, ruff clean → commit `feat: prompt library service with content-hash dedupe and tags`.

---

### Task 2: Polish service, task builders and `POST /hx/prompt/polish`

**Files:** Create `vjhstudio/services/polish.py`, `vjhstudio/web/templates/generate/_polish_results.html`, `tests/test_polish.py`; Modify `vjhstudio/runware/tasks.py`, `vjhstudio/runware/results.py`, `vjhstudio/services/costs.py`, `vjhstudio/web/routes/generate.py`, `tests/test_web_generate.py`.

**Interfaces (produces):**
- `tasks.PROMPT_ENHANCE_MODEL = "runware:llama-3-1-8b@prompt-enhancer"`, `tasks.PROMPT_ENHANCE_MAX_CHARS = 300`, `tasks.PROMPT_ENHANCE_MAX_LENGTH = 300`, `tasks.POLISH_SYSTEM`
- `tasks.build_prompt_enhance(prompt: str, task_uuid: str, *, versions: int = 3, max_length: int = PROMPT_ENHANCE_MAX_LENGTH) -> dict` (truncates with `prompts`-style suffix-safe slicing done locally — `runware/` must not import `services/`; versions clamped 1..5, max_length clamped 12..400)
- `tasks.build_polish_text(model: str, composed: str, task_uuid: str, *, versions: int = 3, system: str = POLISH_SYSTEM) -> dict`
- `results.TaskResult` gains `rows: list[dict] = field(default_factory=list)`; `runner.run_with_policy` passes the raw reply through (text tasks carry no URL, so `parse_items` drops them).
- `costs.record_usage(session, *, job, task_type, cost, model_air, project_id: int | None = None)` — `project_id` is used when `job is None`.
- `polish.POLISH_TIMEOUT_S = 30.0`, `polish.VERSIONS_MAX = 3`, `polish.MODES = ("promptEnhance", "textInference")`
- `polish.clamp_versions(raw) -> int` (garbage → 1, clamped 1..3)
- `polish.split_versions(text: str, versions: int) -> list[str]` (numbered-line splitter; unnumbered text → `[text]`)
- `@dataclass(frozen=True) polish.PolishVersion: text: str; cost: float | None`
- `@dataclass(frozen=True) polish.PolishResult: mode: str; model: str; versions: list[PolishVersion]; cost: float; source: str` with `to_json(chosen_index: int | None = None) -> dict` → `{"source", "mode", "model", "versions": [str], "chosen_index", "cost"}` (the `prompts.polish_json` shape)
- `async polish.run(client_factory, api_key: str, transport: str, *, mode: str, composed: str, model: str = "", versions: int = 3, timeout_s: float = POLISH_TIMEOUT_S) -> PolishResult` — raises `ValueError("Write a prompt first.")` on empty composed and `RunwareError` through.
- Route `POST /hx/prompt/polish` (`async def`, in `routes/generate.py`): reads the builder fields (`PROMPT_FIELDS`) plus `polish_mode`, `polish_model`, `polish_versions`, `project_id`; mode falls back to setting `prompt.polish_mode`, model to `defaults.polish_model`; on success records usage in one `session_scope` and renders `generate/_polish_results.html` with `{versions, mode, model, cost, polish_json}` where each card carries `data-text` and a "Use this" button; `HX-Retarget: #polish-results` is set on every 422.

**Tests (`tests/test_polish.py` + web cases in `tests/test_web_generate.py`):** `build_prompt_enhance` shape and a 400-char composed truncated to exactly 300; `promptVersions` clamped for 0 and 9; `build_polish_text` puts `POLISH_SYSTEM` in `messages[0]` and the composed text in `messages[1]`, asks for numbered lines only when `versions > 1`; `split_versions("1. a\n2. b\n3. c", 3) == ["a","b","c"]`, `split_versions("just one", 3) == ["just one"]`, extra lines truncated to `versions`; `polish.run` in `promptEnhance` mode with the fake scripted `run -> [{"text": "a", "cost": 0.0002}, {"text": "b", "cost": 0.0002}]` → two versions, `cost == 0.0004`, and `fake.calls[-1][1]["model"] == PROMPT_ENHANCE_MODEL`; `textInference` mode with `[{"text": "1. a\n2. b", "cost": 0.01}]` → two versions sharing one cost; `POST /hx/prompt/polish` with no API key → 422 containing "API key" and `HX-Retarget: #polish-results`; empty subject → 422 "Write a prompt first."; a scripted `RunwareError("quota", …)` → 422 with the classified "top up" text; a successful post → 200 with two `Use this` buttons and a `usage_entries` row `task_type == "promptEnhance"`, `project_id` set; `inspect.iscoroutinefunction(generate_routes.hx_polish)` is True while `hx_compose` stays sync.

- [ ] Tests → implement → green, ruff clean → commit `feat: polish prompts via promptEnhance and textInference`.

---

### Task 3: Prompts page, library routes and nav

**Files:** Create `vjhstudio/web/routes/prompts.py`, `vjhstudio/web/templates/pages/prompts.html`, `prompts/_list.html`, `prompts/_list_page.html`, `prompts/_row.html`, `tests/test_web_prompts.py`; Modify `vjhstudio/web/app.py` (include router), `vjhstudio/web/templates/_header.html` (nav "Prompts" between Gallery and Assets), `vjhstudio/web/static/css/app.css` (prompt rows).

**Routes (all sync `def` — DB only):**
- `GET /prompts` — filter form (`q`, `kind`, `favourite`, `project_id`, `tag`) with `hx-get="/prompts" hx-select="#prompt-list" hx-target="#prompt-list" hx-push-url="true"`, the gallery's pattern verbatim; list included on first paint.
- `GET /hx/prompts?…&page=` — page 1 returns `prompts/_list.html`, later pages `prompts/_list_page.html` swapped over the Load-more button itself (`hx-target="this"`), the fix the assets grid needed.
- `POST /prompts` — save from the Generate form: parses the builder fields, `final_prompt`, `project_id`, `kind` (from `mode`), `title` (falls back to `generate.title_for`), `tags`, `polish_json`; `prompts.upsert` → 200 `prompts/_row.html` as an OOB toast plus `HX-Trigger: {"prompt-saved": {"id": …, "created": …}}`; a missing project → 422 with the message inline.
- `POST /prompts/{id}/favourite`, `POST /prompts/{id}/duplicate`, `POST /prompts/{id}/tags` (form `tags`), `DELETE /prompts/{id}` (200 empty body, 404 unknown) — each returns the single `prompts/_row.html` except delete.
- Row markup: title, kind badge, project, `★`, tags, "used {{ p.use_count }} times", `<details>` with raw fields / composed / final / polished versions, and actions **Load into form** (`<a href="/generate?prompt={{ p.id }}">`), Duplicate, Favourite, Delete (`hx-confirm`).

**Tests (`tests/test_web_prompts.py`):** `/prompts` renders with the filter form and an empty state; nav order Gallery → Prompts → Assets; two saved prompts filter by `q`, `kind`, `favourite=1`, `tag`; `POST /prompts` from a Generate-style payload creates a row and answers `HX-Trigger` carrying the new id, a second identical post answers `created: false` and still one row; duplicate makes a second row whose title ends "(copy)"; favourite toggles twice; tags post normalizes `"Fox, FOX "` to `,fox,`; delete → 200 then 404; page 2 of 31 prompts returns no `id="prompt-list"` wrapper; `Load into form` link points at `/generate?prompt=<id>`; every handler in the module is sync `def`.

- [ ] Tests → implement → green, ruff clean → commit `feat: prompts page with search, favourite, duplicate and delete`.

---

### Task 4: Generate page integration — `prompt_id`, `?prompt=`, auto-history, polish column

**Files:** Modify `vjhstudio/web/routes/generate.py`, `vjhstudio/services/generate.py`, `vjhstudio/web/templates/pages/generate.html`, `generate/_prompt_builder.html`; Create `vjhstudio/web/templates/generate/_polish.html`, `generate/_save_prompt.html`; Modify `tests/test_web_generate.py`, `tests/test_generate_service.py`.

**Interfaces / behaviour:**
- `generate.enqueue_image(session_factory, paths, req, *, default_negative: str, polish_json: dict | None = None) -> Job` and `generate.enqueue_video(session_factory, paths, req, *, polish_json: dict | None = None) -> Job` — both now call `prompts.for_request(s, req, kind=…, negative=…, polish_json=polish_json)` inside the existing session and set `job.prompt_id = prompt.id` (overriding `req.prompt_id` only when the row was superseded). Auto-history is therefore on for every submit, including video.
- `routes/generate._prompt_initial(session, prompt: str) -> dict` — `int()` or unknown id → `LookupError` → 404, mirroring the remix branch; returns `prompts.to_initial(row)`.
- `routes/generate._initial(session, remix, ref="", role="", prompt="")` — precedence `remix` > `prompt` > `ref`; `generate_page`/`generate_video_page` finally pass their `prompt` argument through (the readiness gap: it was accepted and ignored).
- `_page` context gains `text_models` (`catalog.list_models(s, "text")`), `polish_model` (`defaults.polish_model`), `polish_mode` (`prompt.polish_mode`), `prompt_id` (`initial.get("prompt_id")`), `prompt_title` (`initial.get("title")`).
- `pages/generate.html`: `<input type="hidden" name="prompt_id" :value="promptId">` and `<input type="hidden" name="polish_json" :value="polishJson">` rendered **outside** both mode fieldsets (the disabled-fieldset trick would stop them posting); `generate/_polish.html` and `generate/_save_prompt.html` are included in the prompt column, so the text-model select is never inside a disabled fieldset either.
- `generate/_polish.html`: mode select (`polish_mode`), versions select 1/2/3 (`polish_versions`), the text-model select via `catalog/_model_select.html` with `allow_empty=True` shown only under `x-show="polishMode === 'textInference'"`, a "Polish with AI" button (`hx-post="/hx/prompt/polish" hx-include="#generate-form" hx-target="#polish-results" hx-swap="innerHTML"`), and `<div id="polish-results" aria-live="polite">`.
- `generate/_save_prompt.html`: `<dialog id="save-prompt">` with title + tags inputs and a save button posting to `/prompts` with `hx-include="#generate-form"`.
- `submit_image`/`submit_video` read `polish_json` from the form (`json.loads`, dict-only, ≤8000 chars, otherwise ignored) and pass it to the enqueue call.

**Tests:** `/generate?prompt=<id>` prefills — the `initial` JSON blob carries the saved `form`, `final_prompt` and `prompt_id`, and the rendered page has `name="prompt_id"` with that value; `/generate?prompt=999999` → 404; `/generate?prompt=<video prompt>` renders the Video tab selected (`initial.mode == "video"`); the hidden `prompt_id` and the polish select both appear **before** the first `<fieldset` of the mode panes (regex index assertion — they must not sit inside a disabled fieldset); submitting an image with no `prompt_id` creates a Prompt row and the job's `prompt_id` points at it with `use_count == 1`; submitting the same text twice → one Prompt row, `use_count == 2`, two jobs; submitting with `prompt_id=<id>` of an unchanged row reuses it; a submit carrying `polish_json` stores the blob on the row; a video submit links a `kind="video"` row; `polish_json="not json"` is ignored rather than 500; the loaded-prompt page still renders the refs chips from `ref_chips` (no re-derivation).

- [ ] Tests → implement → green, ruff clean → commit `feat: load prompts into the generate form and record every job's prompt`.

---

### Task 5: Alpine state for polish, save dialog and the draft guard

**Files:** Modify `vjhstudio/web/static/js/app.js`, `vjhstudio/web/templates/generate/_polish_results.html` (card buttons dispatch), `vjhstudio/web/static/css/app.css`; Create `tests/test_js_generate_form.py`.

**Interfaces / behaviour (`window.generateForm`):**
- New state: `promptId: initial.prompt_id || ''`, `polishJson: ''`, `polishMode` / `polishModel` / `polishVersions` seeded from the form's `data-polish-*` attributes, `savedTitle: initial.title || ''`.
- `usePolish(detail)` — sets `this.finalPrompt = detail.text` and `this.polishJson = JSON.stringify({source: detail.mode, model: detail.model, versions: detail.versions, chosen_index: detail.index, cost: detail.cost})`; wired by one delegated `click` listener on `#polish-results` reading `data-*` off the card button (the panel is htmx-swapped, so the binding cannot live on the button).
- Draft guard (readiness gap 3): `_loadDraft()` runs only when `!(initial.form || initial.prompt_id || initial.remix)`; `_saveDraft` never writes `promptId`/`polishJson`, and any manual edit of a builder field clears `promptId` so an edited prompt is not silently re-linked to the row it came from.
- `document.body.addEventListener('prompt-saved', …)` sets `this.promptId` from the trigger payload (normalised through the existing `vjhUnwrapTrigger`) and toasts "Prompt saved" / "Already in your library".
- `openSaveDialog()` / `closeSaveDialog()` drive `<dialog id="save-prompt">`; a successful submit clears `finalPrompt` exactly as today.

**Tests (`tests/test_js_generate_form.py`, source-level like `tests/test_js_mirror.py`):** `node --check` on `app.js` when node is available (skipped otherwise); the `_loadDraft()` call site matches a regex requiring all three of `initial.form`, `initial.prompt_id`, `initial.remix`; `usePolish` assigns both `this.finalPrompt` and `this.polishJson`; the `_saveDraft` payload literal mentions neither `promptId` nor `polishJson`; a `prompt-saved` listener exists and assigns `promptId`; `generate/_polish_results.html` renders each card with `data-text`, `data-index`, `data-mode`, `data-model` and a `Use this` button; plus a route case asserting the Generate page includes `id="save-prompt"` and `id="polish-results"`.

- [ ] Tests → implement → green; manual smoke with a real key: polish a prompt in both modes, click "Use this", Save prompt, reload `/prompts`, "Load into form", generate → the job links the same row and "used 2 times" appears → commit `feat: polish cards, save-prompt dialog and prompt-aware drafts`.

---

## Phase 5 exit criteria

- Build a prompt, polish it in `promptEnhance` mode (3 versions ≤ 300 chars), pick one, Save it with tags; it appears on `/prompts`, and a `usage_entries` row records the `promptEnhance` cost.
- Switch polish mode to `textInference`, pick a price-sorted text model from the select (which stays enabled on both tabs), polish again and use a version; `polish_json` on the saved row names that model.
- "Load into form" opens `/generate?prompt=<id>` with the raw fields, final prompt and mode restored, the hidden `prompt_id` posted, and the localStorage draft not overwriting it.
- Generating twice from the same text produces one Prompt row with `use_count == 2` and both jobs' `prompt_id` pointing at it; deleting the prompt leaves the jobs intact.
- `/prompts` filters by q/kind/favourite/project/tag, duplicates, favourites and deletes; nav shows Prompts between Gallery and Assets; Load more appends instead of replacing.
- Suite green, ruff check and format clean, `test_models_match_migrations` green at revision 0004.
