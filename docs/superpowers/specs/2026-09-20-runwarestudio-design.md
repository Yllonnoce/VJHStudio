# VJHStudio — Design and Implementation Plan

> Renamed to **VJHStudio** on 2026-09-20 (package `vjhstudio`, env prefix `VJHSTUDIO_`, repo github.com/yllonnoce/VJHStudio).

## Context

A new, single-user, local web app that is a fully flexible image and video generation front end for the
RunWare.AI API. It replaces ad-hoc use of RunWare inside other projects (ScenePlay had an image client,
DungeonCrawler had text/TTS) with a standalone tool that:

- builds good prompts from a structured form and keeps them for reuse,
- saves every generated image/video to disk in project folders (easy to download/browse),
- keeps a library of uploaded sample images/videos to use as references,
- tracks every job, its parameters and cost in SQLite with Alembic-managed schema versions,
- updates itself from GitHub (git pull, uv sync, migrate, restart) from the Settings page,
- installs on Windows (.bat only), Linux and macOS (.sh) with script installers.

## Decisions made in the planning session (2026-09-20)

| Topic | Decision |
|---|---|
| Name / repo | **VJHStudio**, `github.com/yllonnoce/VJHStudio`, branch `main` |
| Prior art | Start fresh. Borrow *patterns* only: ScenePlay `ops/app_update.py` (updater), `core/db_migrate.py` (boot migration), DungeonCrawler async poll/backoff |
| RunWare client | New official **`runware-sdk`** package (`from runware import Runware`, `await client.run({...task dict})`) |
| Providers | RunWare only, for both image and video (no Gemini Veo) |
| Users | Single user, localhost, no login |
| Port | **8080** default (configurable) |
| Frontend | Jinja2 + HTMX + Alpine.js, server rendered, no build step, assets vendored locally |
| Prompts | Structured form → deterministic template; optional "Polish" via `promptEnhance`; raw fields, composed and final prompt all saved |
| Models | Curated defaults JSON + live `modelSearch`; chosen models cached in SQLite with capabilities/price |
| Uploads | Reference for image-to-image and image-to-video; reusable tagged asset library |
| Organization | Projects → `outputs/<project-slug>/`; flat gallery filtered by project/type/model/date |
| Installer | `install.sh` (Linux/macOS) and `install.bat` (Windows). **No PowerShell (.ps1) anywhere.** Installs uv + git if missing, clones, `uv sync`, migrates, creates launcher, opens browser |
| Self-update | From Settings: show commit + commits behind; Update = backup DB → git stash → `git pull --ff-only` → `uv sync` → `alembic upgrade head` → restart |
| Database | SQLite via SQLAlchemy 2.x + Alembic; migrations run at boot; refuse to start on failure; backup before update/migration |
| API key | Key stash: env `RUNWARE_API_KEY` overrides; otherwise plain file `data/secrets/runware_api_key` with owner-only permissions (0600 / Windows icacls), never in git, never logged, masked in UI |
| Uninstall | `uninstall.sh` and `uninstall.bat` required: remove venv, launcher/shortcuts, optionally the checkout; keep the data dir unless the user opts in |
| Cost | `includeCost` on every task; per-job cost stored; totals per project/day; balance in header |
| Model dropdowns | Three price-sorted dropdowns (most expensive first), price shown in the label in the model type's native unit: **image** $/image at 1024x1024; **video** $/second at 720p (+ $/5 s clip); **text** (prompt polish) $/1M tokens in and out. Prices seeded from a curated catalogue JSON; observed `cost` from real jobs refines the estimate |

## Verified facts about `runware-sdk` (v1.6.10, Python ≥3.11, MIT)

- `from runware import Runware`; `Runware(api_key=..., transport="websocket"|"rest", timeout=ms, poll_timeout=ms)`; `async with` optional (`connect()`/`close()`). One client is safe to share across concurrent tasks.
- `await client.run(task_dict, RunOptions(timeout, cancel_event: asyncio.Event, validate, on_result, on_progress))` → **always `list[dict]`**. Sets `deliveryMethod="async"` by default and polls internally (0.5 s → ×1.5 → cap 10 s) until done; default budget 20 min; raises `RunwareError(code="timeout")`.
- `cancel_event` is client-side only: the server keeps running and **bills** the task.
- `RunwareError`: `.code` ∈ {validation, auth, quota, rateLimit, safety, provider, timeout, notFound, serverError, connection, aborted, unknown}, `.retryable`, **`.parameter`** (dotted path of the rejected field, e.g. `inputs.referenceImages`), `.message`, `.validation_errors`.
- Helpers (all return `list[dict]`): `model_search`, `media_storage({"operation":"upload","media": data_uri})` → `mediaUUID`, `account_management({"operation":"getDetails"})` → `balance.amount`, `get_task_details`, `stream` (LLM SSE).
- Local file paths anywhere in a task dict are auto base64-encoded by the SDK.
- Task shapes: image `inputs.seedImage`/`inputs.maskImage` + top-level `strength`; video `inputs.frameImages: [{"image": <uuid|url>, "frame": "first"|"last"}]`, `inputs.referenceImages`, `inputs.referenceVideos`, `providerSettings.<provider>`. `promptEnhance` = `{"prompt"(≤300 chars),"promptMaxLength"(12-400),"promptVersions"(1-5)}` — **no `model` field**: verified live 2026-09-20 that sending `model` (e.g. `"runware:llama-3-1-8b@prompt-enhancer"`) is rejected ("Invalid value for 'model' parameter...") while the identical task without it succeeds; the model is fixed server-side. A requested `promptVersions` is an upper bound, not a guarantee — a live `promptVersions: 2` call returned a single row.
- Output URLs expire (7 days default) → **download immediately**. Video `outputType` is URL only.
- **Free public catalog API** (`client.content.*`, GET `https://content.runware.ai/...`, no key): `list_models({"category":"image"|"video"|"text", "capability":..., "search":..., "sort":...})` → items with `model`(slug), `air`, `name`, `creator`, `capabilities[]`, `status`, `pricingOverview`, `pricingExamples[]`; `get_model_pricing(slug_or_air)` → adds `pricingMeasured[] {configuration:"1024x1024 · 28 steps", price: float}` (image), `pricingRates[] {amount, unit: "durationSecond"|"inputToken"|"outputToken"|"cachedInputToken", label}` (video/text), `category[]`. Fields beyond the TypedDicts are observed, not documented → parse defensively, fall back to `pricingExamples[].price` strings.
- No server-side sort by price; sort client-side. Rate limits are soft; 2-4 concurrent tasks recommended.

## Architecture (recommended approach)

Single FastAPI process, sync SQLAlchemy over SQLite (WAL), asyncio job runner inside the app lifespan, one `Runware` client opened per job (transport from settings), Jinja2 pages + HTMX partials + Alpine for client state. No Node, no Celery, no Redis.

### Package layout

```
VJHStudio/
  pyproject.toml, uv.lock, alembic.ini, version.py, run.py, README.md, .gitignore
  start.sh, start.bat, install.sh, install.bat, uninstall.sh, uninstall.bat
  scripts/restart_helper.bat
  migrations/ (env.py bound to models.Base.metadata, render_as_batch=True; versions/0001_initial.py)
  vjhstudio/
    main.py        CLI entry (host/port from env, boot(), uvicorn.run)
    config.py      env vars, Paths (data dir resolution), RESTART_EXIT_CODE=75
    secrets.py     api key file read/write (0600), effective_api_key() (env overrides)
    db.py          engine (WAL, foreign_keys=ON), SessionLocal, session() ctx
    boot.py        dirs → backup-if-migrating → alembic upgrade → app_meta → seed catalog → orphan jobs
    models/        base, project, prompt, catalog, asset, job, output, setting(+app_meta), usage
    schemas/       pydantic: ImageRequest, VideoRequest, PromptForm, JobView
    runware/       client.py (open_client), tasks.py (pure task-dict builders), errors.py (code→message),
                   runner.py (retry + parameter-fallback policy), results.py, download.py (stream to .part → rename + sidecar json),
                   catalog_api.py (content API: list + pricing → normalized PriceInfo)
    services/      projects, prompts (compose/negative/polish), catalog (seed, refresh prices, sorted lists),
                   assets (sha256 dedupe, lazy mediaStorage upload, 6-day re-upload), generate (validate→Job→submit),
                   jobs (JobRunner), outputs (gallery queries, remix), costs, settings, backup, migrate, update, restart, gitinfo
    web/           app.py (create_app, lifespan), deps.py, routes/{pages,generate,jobs,gallery,prompts,catalog,assets,projects,settings,system,files}.py,
                   templates/, static/ (vendored htmx, alpine, css)
    data/curated_models.json   shipped price snapshot + curated defaults (fallback when offline)
  tests/
```
Rules: `runware/` and `services/` never import `web/`; routes are thin.

### Data model (SQLite, Alembic-managed)

- **projects**: name, slug (unique; folder name), description, default_image_model, default_video_model, is_archived. Seeded `default`.
- **prompts**: project_id (nullable), title, kind (image|video), form_json (raw builder fields), composed_prompt, final_prompt, negative_prompt, polish_json ({source, versions[], chosen_index, cost, model}), final_edited, is_favourite, use_count, last_used_at.
- **catalog_models**: air (unique), slug, name, kind (image|video|text), provider/creator, architecture, capabilities_json, default_width/height/steps/cfg, hero_image_url, **price_unit** (`per_image`|`per_second`|`per_1m_tokens`), **price_primary** (float used for sorting: $/image @1024², $/s @720p no audio, $/1M output tokens), **price_in** / **price_out** (text only, $/1M), **price_tiers_json** (all measured/rate rows), price_source (`content_api`|`curated`|`observed`), price_updated_at, provider_settings_schema (curated), source (`curated`|`search`|`content`), is_favourite, is_hidden, raw_json, last_seen_at.
- **assets**: filename (`<sha12>.<ext>` under uploads/), original_name, kind, mime, size_bytes, width, height, sha256 (unique), tags (`,a,b,` normalized), notes, media_uuid, media_url, media_uploaded_at.
- **jobs**: id (uuid4 = taskUUID), project_id, prompt_id, kind, status (queued|running|succeeded|failed|cancelled), model_air, request_json (typed request; remix reloads this), task_json (exact dict sent, after fallback drops), dropped_params_json, progress, status_text, attempts, error_code, error_message, cost, runware_task_uuid, cancel_requested, created/started/finished_at. Indexes: status; (project_id, created_at).
- **outputs**: job_id, project_id, kind, filename (`20260920-143012-a1b2c3.png`), rel_path (`<slug>/<filename>`), sidecar_rel_path, model_air, prompt_text, negative_prompt, params_json, seed, width, height, duration_s, cost, source_url, file_size, is_favourite, is_missing, created_at. Indexes: (project_id, created_at), model_air, is_favourite.
- **settings** (key/value): runware.transport, runware.timeout_s, jobs.concurrency, paths.outputs_dir, defaults.image_model, defaults.video_model, defaults.polish_model, defaults.output_format_image/video, defaults.negative_prompt, prompt.polish_mode (promptEnhance|textInference).
- **app_meta** (key/value): schema_revision, app_version_last_boot, git_commit_last_boot, catalog_seed_version, catalog_prices_refreshed_at, account.balance, account.balance_at, last_backup_at.
- **usage_entries**: job_id, project_id, task_type, model_air, cost, day (UTC date), created_at. One row per costed result. Totals = SUM GROUP BY project / day.

### RunWare adapter

- `tasks.py`: pure builders `build_image_task(ImageRequest, task_uuid, media_map)`, `build_video_task(...)`, `build_prompt_enhance`, `build_polish_text(model, composed)`, `build_media_upload`. Always `outputType="URL"`, `includeCost=True`. `extra_json` escape hatch merged last (cannot override taskType/taskUUID/model) = the "fully flexible" knob.
- `runner.run_with_policy(client, task, timeout_s, cancel_event, on_progress)`: max 5 attempts (a rejection is not billed). `validation` with `err.parameter` set (RunWare's `unsupportedParameter` names the field, e.g. `inputs.seedImage`; fall back to the regex `unsupported use of '?([A-Za-z0-9]+)'?` on the message) and not in {model, positivePrompt, taskType, taskUUID} → apply a **conversion rule** before a plain drop: `inputs.seedImage` rejected → move the value to `inputs.referenceImages=[v]` and drop `strength`; `strength` rejected → drop only strength; `inputs.referenceImages` rejected → drop and flag `reference_dropped` for the UI; anything else → delete that dotted key. New `taskUUID` per retry; every drop/conversion recorded in `dropped_params_json` and the sidecar. `rateLimit` backoff [2,5,15] s; `connection` [1,3,8] s; `serverError` once after 5 s; everything else raises. `errors.classify()` maps codes to user-facing text (401/auth → "check key in Settings", quota → "out of credit at my.runware.ai").
- **Model-family rules** (learned from ScenePlay, encoded in `services/catalog.family(model)` from the content API `architecture`/`creator` and the curated JSON): *instruction-native* (architecture or creator contains `gemini`, `gpt`, `openai`) → input image goes in `inputs.referenceImages`, never `seedImage`/`strength`; no `negativePrompt`, no `steps`/`CFGScale`/`scheduler`; prompt passes through raw. *Diffusion* (FLUX, SD, civitai checkpoints) → `inputs.seedImage` + `strength` (default 0.8), negative prompt supported, steps/CFG/scheduler offered. Unknown → diffusion defaults and let the fallback loop discover. Pre-flight: refuse to enqueue when the catalog says the AIR's kind is not the requested mode (text/lora/vae/audio picked as an image model) with a message naming a valid alternative.
- **Dimensions**: image width/height rounded to multiples of 64 and clamped 512–2048 for diffusion models (aspect ratio clamped to 2:1); instruction-native and video models use the allowed size/resolution lists from the catalog instead of rounding.
- **Prompt limits**: `positivePrompt` capped at 2900 chars with suffix-safe truncation; optional "No text in image" toggle (default on for image mode) appends the no-text suffix and adds the text/watermark tokens to the negative prompt.
- `download.py`: httpx stream → `.part` → `os.replace`; 2 retries; sidecar `.json` with app_version, job, project, model, prompt, negative, params, seed, cost, task_sent, dropped_params, source_url, created_at.
- `catalog_api.py`: `fetch_models(category)` + `fetch_pricing(slug)` → `PriceInfo(unit, primary, price_in, price_out, tiers)`. Normalization rules:
  - image: primary = `pricingMeasured` row matching `1024x1024` with no LoRA, else first measured, else min parsed `pricingExamples[].price`.
  - video: primary = `pricingRates` row with unit `durationSecond` whose label contains `720p` and not `audio`, else the lowest `durationSecond` rate; also store `per_5s = primary*5`.
  - text: price_in = inputToken×1e6, price_out = outputToken×1e6, primary = price_in + price_out (sort key).
  - Missing/unparseable → price_primary NULL, shown as "price unknown" and sorted last.

### Job runner (`services/jobs.py`)

asyncio.Queue + single dispatcher + Semaphore(concurrency, default 3, live-adjustable). Per job: load → resolve asset ids to mediaUUIDs (lazy `media_storage` upload, cached 6 days) → build task → persist task_json → `run_with_policy` with `on_progress` (in-memory snapshot, DB write ≤ every 2 s) → download to `outputs/<slug>/` → write Output + UsageEntry rows → succeeded. `cancel` sets the asyncio.Event (UI says "Stop waiting; RunWare still bills a submitted video"). Boot marks `running` → `failed(orphaned)` and re-submits `queued`. Prompt polish and model search run inline (30 s timeout), not as jobs.

**Working progress bar (user, 2026-09-20; design choice)**: each job card has a real `<progress>` bar. Source of truth, in order: (1) RunWare `progress` 0-100 from `on_progress` when the model emits it; (2) otherwise an **estimate**: `expected_ms` = the catalog's `latencyMs` for the closest `pricingExamples` configuration (stored per model as `typical_latency_ms`, refined by observed durations of finished jobs, per model+kind rolling average), and the bar shows `min(90, 100 * (1 - exp(-elapsed/expected_ms)))` computed server-side on each 2 s poll (so it also works without JS timers); (3) 100 on success. Below the bar a stage label from `status_text`: queued → submitting → rendering (n%) → downloading → done/failed, plus elapsed and ETA ("~40 s left" when estimated). Cancelled/failed bars turn to the danger colour. `jobs` gains `expected_ms` (Phase 3 migration).

**Job completion notifications (user, 2026-09-20)**: all RunWare calls run as background jobs (this runner); the UI never blocks on them. When a job reaches `succeeded`/`failed`/`cancelled` the queue partial emits `HX-Trigger: job-finished` with the job id, title and status; `app.js` shows an in-app toast (with thumbnail for images) and, if the user has granted permission, a browser desktop notification via the Notifications API (`Notification.requestPermission()` is asked once from a "Notify me when jobs finish" toggle in Settings, stored as `ui.notify_desktop`). Jobs that finish while no page is open are shown as unseen (badge count) on the next visit (`jobs.seen_at` column, Phase 3 migration).

### Prompt composition (mirrored in Alpine on the client)

Fields in fixed order: subject, style, mood, lighting, camera, composition, colour, extras. Each trimmed, inner whitespace collapsed, trailing `,.;` stripped, empties dropped, exact case-insensitive duplicate fields dropped, joined with `", "`. No labels inserted. Negative = user negative tokens + (if enabled) `defaults.negative_prompt` tokens, de-duplicated, joined with `", "`. Deterministic; identical input → identical output.

Polish: mode `promptEnhance` (composed truncated to 300 chars, 1-3 versions, max length 300) or `textInference` with the chosen text model and a fixed system prompt ("rewrite as a single vivid diffusion prompt, ≤ 120 words, no preamble"; 3 versions via 3 calls or one call asking for three numbered lines). Nothing saved by polish itself; on Generate/Save the Prompt row stores form_json, composed, final, negative, polish_json.

### Model dropdowns (image / video / polish-text)

- Populated from `catalog_models` where kind matches and not hidden, **ordered by price_primary DESC NULLS LAST**, label = `"{name} — ${price}/{unit}"` (text shows `$in / $out per 1M`). Favourites pinned on top within the sort? No: keep pure price order; favourites get a ★ marker only (user asked for price order).
- **Refresh prices** button (Settings and Models page): `catalog.refresh_from_content_api()` pulls `list_models` for image, video, text (status live, native only), then `get_model_pricing` per model with a small concurrency (5), upserts `catalog_models` (source=`content`), stamps `catalog_prices_refreshed_at`. Auto-refresh at boot if older than 7 days and network available (best-effort, background, never blocks boot).
- Shipped `curated_models.json` = a snapshot of the same normalized shape (seeded at first boot so the dropdowns work offline), plus per-model `provider_settings_schema` and allowed resolutions/durations for the UI presets on the handful of curated video models (Veo 3.1 `google:3@2`, Kling 3.0 Std `klingai:kling-video@3-standard`, Seedance 2.0 `bytedance:seedance@2.0`, Wan 2.7 `alibaba:wan@2.7`, LTX-2.3 `lightricks:ltx@2.3`) and image models (FLUX.1 dev `runware:101@1`, Nano Banana Pro `google:4@2`, GPT Image 2).
- Observed cost: after each job, `costs.observe(model_air, cost, params)` keeps a rolling average per (model, kind) in `price_tiers_json.observed`; shown as "last actual: $x" next to the estimate.
- Live `modelSearch` (community checkpoints/LoRAs) still available on the Models page; added models get price_primary NULL until the content API knows them.

### Routes (summary; full table in the backend design)

Pages: `/`, `/generate/image`, `/generate/video` (`?remix=<output_id>`, `?prompt=<id>`), `/gallery`, `/prompts`, `/models`, `/assets`, `/projects`, `/settings`, `/system`, `/restarting`.
HTMX partials under `/hx/...`: prompt/compose, prompt/polish, model-options?air=, jobs/active (poll 2 s, stops when idle), jobs/{id}, gallery (filters, 48/page), outputs/{id}, prompts, models?kind=&sort=price, models/search, assets, assets/picker, projects/{id}/costs, header/balance (60 s), system/update-check, system/update-log, system/backups.
Actions: POST `/generate/{image|video}`, `/jobs/{id}/cancel|retry`, `/outputs/{id}/favourite`, DELETE `/outputs/{id}`, `/outputs/{id}/download|remix`, `/prompts...`, `/models/add|refresh-prices|{id}/favourite|hide`, `/assets/upload|{id}/tags|{id}/push`, `/projects...`, `/settings`, `/settings/api-key`, `/settings/api-key/test`, `/system/update|restart|backup`.
JSON: `/api/health` ({ok, version, commit, schema, pid}), `/api/jobs`, `/api/jobs/{id}`, `/api/outputs/{id}`, `/api/models?kind=`.
Files: `/files/outputs/{slug}/{filename}`, `/files/uploads/{filename}` via FileResponse with traversal guard (outputs dir is a runtime setting, so not a StaticFiles mount).

### Migrations, backups, versioning

- Alembic at repo root; `migrations/env.py` uses `Base.metadata`, batch mode for SQLite. No `create_all`; `0001_initial` creates everything. CLI `python -m vjhstudio.migrate upgrade|current|head`.
- Boot: if `current != head` and DB exists → `backup_db("pre-migrate")` → upgrade; failure → exit code 3 (launcher loop stops, message names the backup).
- Backups via `sqlite3 .backup()` API into `data/backups/studio-YYYYMMDD-HHMMSS-<label>.db`; rotation keeps 10 per label (pre-migrate, pre-update, manual).
- `app_meta` mirrors schema revision, app version and git commit per boot; `/system` and footer show them.
- Discipline: models + autogenerated revision committed together; `tests/test_migrations.py` asserts empty→head works and autogenerate diff is empty.

### Self-update and restart

`services/update.py` (runs in a thread, step log polled by the UI): git-install check → record old SHA → `backup_db("pre-update")` (mandatory) → `git stash` if dirty → `git pull --ff-only` → `uv sync --frozen` (fail ⇒ `git reset --hard old`, re-sync) → `uv run python -m vjhstudio.migrate upgrade` (fail ⇒ reset code, re-sync, restore DB from the pre-update backup) → `git stash pop` → restart. Git is forced non-interactive (`GIT_TERMINAL_PROMPT=0`, ssh BatchMode); auth failures get a "sign in once with git pull in a terminal" message.

**Update notice (manual update, never automatic)**: a background task runs `check_updates()` 60 s after boot and every 6 h (skipped when offline or not a git install) and stores `{behind, commits, checked_at}` in `app_meta`. When `behind > 0` the header shows an "Update available (N)" badge linking to Settings → Updates, where the commit list and the **Update now** button are; Check for updates runs the same check on demand. Nothing is pulled until the user clicks Update now.

`services/restart.py`: if `VJHSTUDIO_LAUNCHER=1` → `os._exit(75)` and the launcher loop relaunches (both OS). Windows without launcher → detached `scripts/restart_helper.bat <pid>` (waits via `tasklist`, then `start start.bat`). POSIX without launcher → `os.execv`. `/restarting` page polls `/api/health` until the commit changes.

### Configuration

Env: `RUNWARE_API_KEY` (overrides stash), `VJHSTUDIO_DATA_DIR` (default `<repo>/data` inside the checkout, git-ignored), `VJHSTUDIO_HOST` (127.0.0.1), `VJHSTUDIO_PORT` (**8080**), `VJHSTUDIO_LOG_LEVEL`, `VJHSTUDIO_LAUNCHER`, `VJHSTUDIO_UV`. Data dir: `vjh.db`, `backups/`, `uploads/`, `outputs/`, `secrets/api_key` (0600; dir 700). Precedence: env > settings table > defaults.

Deps: fastapi, uvicorn[standard], jinja2, python-multipart, sqlalchemy≥2, alembic, pydantic≥2, httpx, runware-sdk, pillow, python-slugify. Dev: pytest, pytest-asyncio, ruff.

### Testing

`tests/fakes/fake_runware.py` (scripted `run()` per taskType, records tasks, honours cancel_event, emits progress; `content` fake returns canned pricing JSON) injected via `create_app(client_factory=...)`; httpx MockTransport for downloads. Suites: config/secrets perms, migrations (empty→head, autogenerate clean), prompt compose determinism, task builders exact dicts, runner policy (parameter fallback ×3, backoffs, cancel), download (.part/rename/sidecar), jobs (lifecycle, cancel, orphan, concurrency), assets (dedupe, lazy upload, 6-day re-upload), catalog (seed idempotent, price normalization for image/video/text, sort order), routes (HX vs page, remix prefill, gallery filters), update (command sequence, rollback paths, restart exit code). CI: GitHub Actions matrix Linux + Windows.

### Backup, restore and merge (`services/archive.py`, Settings → Backups, CLI `backup`/`restore`)

Archive = zip `vjhstudio-backup-YYYYMMDD-HHMMSS.zip` in `data/backups/`:
- `manifest.json` {app_version, schema_revision, created_at, includes: [db, uploads, outputs], counts per table, host}
- `vjh.db` always (consistent snapshot via sqlite backup API)
- `uploads/…` and `outputs/<slug>/…` (+ sidecars) only when the checkboxes are ticked (outputs can be GBs)
- Streamed with `zipfile` (ZIP64, stored for media, deflated for db) to avoid RAM blow-up.

**Restore (replace)**: safety backup → pause JobRunner (refuse if jobs running) → replace `vjh.db` (remove `-wal/-shm`) → extract files (overwrite) → `alembic upgrade head` (archive may be older schema) → restart. A db-only archive marks outputs/assets whose files are absent as `is_missing`.

**Merge (additive union by natural keys)**: extract archive db to temp → `alembic upgrade head` on the temp copy → import in dependency order inside one transaction, with id remapping:
| table | natural key | on match | on new |
|---|---|---|---|
| projects | slug | keep ours | insert; mkdir outputs/<slug> |
| catalog_models | air | keep ours | insert |
| assets | sha256 | keep ours | insert + copy file if present in archive |
| prompts | sha256(kind, final_prompt, negative_prompt, canonical form_json) | keep ours | insert (project remapped) |
| jobs | id (uuid) | skip | insert (terminal statuses only; queued/running become failed/orphaned) |
| outputs | (project slug, filename) | keep ours | insert + copy file & sidecar if present, else `is_missing` |
| usage_entries | only for newly inserted jobs | – | insert |
| settings / app_meta / secrets | never merged | | |
Dry-run first: `preview_merge(zip)` returns per-table {new, existing, missing_files}; UI shows the table and asks to confirm. Safety backup before the real merge. Import from any zip (upload through the UI or a path via CLI).

UI: Settings → Backups: Create (checkboxes db/uploads/outputs), list with size/date/contents, Download, Restore, Merge (preview → confirm), Import file, Delete. CLI: `vjhstudio backup [--uploads] [--outputs]`, `vjhstudio restore <zip> [--merge] [--yes]`.

## UI design (Jinja2 + HTMX + Alpine, Pico CSS, dark default)

**Theme requirement (user, 2026-09-20)**: modern look with *colour binding* like the ScenePlay_Flask project: CSS design tokens on `:root` (background, surface, text, accent, border, radius, shadow), a user-selectable palette/accent stored as the `ui.theme`/`ui.accent` settings and applied via `data-theme`/`data-accent` attributes on `<html>` before first paint, mirrored for light and dark. Ported from ScenePlay_Flask's stylesheet and theme JS (see Phase 1 Task 12).


Vendored under `vjhstudio/web/static/vendor/`: htmx 2.0.x, Alpine 3.x (+ focus plugin), Pico CSS 2.x; `css/app.css` (≤300 lines), `js/app.js` (composePrompt, generateForm, restartWatcher, dropzone, htmx error→toast). Icons: `icon.ico`, `icon.png`, `favicon.svg`. No CDN.

Conventions: pages extend `base.html`; partials start with `_` and are included on first paint and returned by HTMX later (one source of truth). 422 re-renders the partial with inline errors (`HX-Retarget` when needed); other errors → JSON `{"error"}` → toast. Any response may append an OOB toast. `HX-Trigger: jobs-changed` after job create/cancel/retry.

**Header**: nav, balance chip (`/hx/header/balance`, every 60 s; states: amount / "No API key" / offline), active-jobs badge (OOB-refreshed), theme toggle (sets `data-theme`, persists via settings).

**Generate** (`/generate/image`, `/generate/video`; tabs switch mode):
- Left: prompt builder (subject, style, mood, lighting, camera, composition, colour, details, negative), live composed preview (Alpine, same rules as Python), "Polish with AI" (→ `/hx/prompt/polish` cards with "Use this"), "Save prompt" dialog.
- Middle: **model dropdown sorted most-expensive-first with price in the label** (`FLUX.1 dev — $0.0038/img`, `Veo 3.1 — $0.20/s ($1.00/5 s)`), search box (`/hx/models/search` → "Add"), adaptive parameter panel (`/hx/model-options?air=` re-fetched on model/mode change; image: size presets + custom, steps, CFG, seed+lock, results, format; video: duration, resolution preset, fps if supported, audio toggle, provider settings from schema), reference chips with roles (reference / first frame / last frame) + picker dialog (asset grid + inline upload), project select with inline create, **cost estimate** (`/hx/generate/estimate`: image = price@1024² × (w·h/1024²) × n; video = matching per-second rate × duration; "≈" and "n/a" when unknown), Generate button (disabled without API key or empty prompt).
- Right: queue panel `/hx/jobs/active` polling every 2 s only while jobs are active (server renders the trigger; outerHTML swap so polling stops itself), job cards (progress, elapsed, cancel "Stop waiting" for video, thumbnails, cost, Retry, Remix, error `<details>`).
- Prefill: `?remix=<output_id>`, `?prompt=<id>`, `?ref=asset:<id>|output:<id>` → server renders an `initial` JSON blob; Alpine loads it; URL cleaned with replaceState. Draft autosaved to localStorage otherwise.
- Polish dropdown: text-model select (price-sorted, `$in / $out per 1M`) shown when polish mode is `textInference`; `promptEnhance` mode needs no model.

**Gallery**: filter form (project, type, model, date range, favourites, prompt search; pushes URL), grid 60/page with "Load more", lightbox `<dialog>` with media, prompt, params, seed, cost, job link; actions Download, Favourite, Remix, Use as reference, Copy folder path, Delete (confirm). Keys: Esc, ←/→, `f`.

**Prompts**: search/tag/favourite filters, rows expand to raw fields / composed / polished; Load into form, Duplicate, Favourite, Delete.

**Assets**: drag-and-drop multi-upload with progress, type/tag/search filters, cards show RunWare-uploaded badge (mediaUUID cached), tag editor, Use as reference, Delete (confirm names jobs using it).

**Projects**: create inline, rename, archive toggle, per-row outputs count, cost, folder path with copy.

**Settings**: API key (masked, Save, Test → balance), transport, defaults (image/video/polish models, negative prompt, results), paths & concurrency, theme, **Models** (Refresh prices button + last refreshed), **Updates** (version, commit, Check → behind count + commit subjects, Update now → live log every 1 s → `HX-Redirect: /restarting`), **Backups** (section above).

**Restarting page**: standalone; polls `/api/health` every 1 s, waits until it first fails then returns with a different `boot_id`, then redirects; after 120 s shows manual start instructions.

Accessibility: labels on every input, `role=tablist`, native `<dialog>` (Esc/focus trap), Ctrl/Cmd+Enter submits from textareas, `aria-live` queue and toasts, `prefers-reduced-motion`.

Empty/error states: no API key banner + disabled Generate; offline chip; failed job shows RunwareError code+message; parameter rejection shows field-level error; empty catalog for mode → "search above to add one"; upload too large → toast (`max_upload_mb` default 200).

## Installers and launchers (Windows = .bat only; Linux/macOS = .sh)

Shared: install dir `~/VJHStudio` / `%USERPROFILE%\VJHStudio` (override `VJHSTUDIO_HOME`); `data/` inside it (git-ignored); tools live where upstream installers put them (uv `~/.local/bin/uv`, `%USERPROFILE%\.local\bin\uv.exe`; Windows fallback MinGit at `%LOCALAPPDATA%\Programs\MinGit`); Python is uv-managed (no system Python needed); scripts export `VJHSTUDIO_UV`/`VJHSTUDIO_GIT` so the in-app updater uses the same binaries; port default **8080** (`VJHSTUDIO_PORT` or first arg); the server opens the browser (`serve --open`), restarts pass `--no-browser`; if 8080 is busy and answers as VJHStudio, just open the browser; otherwise try the next 10 ports and print.

CLI (`vjhstudio` console script): `serve [--port] [--open|--no-browser]`, `migrate`, `backup`, `restore`, `version`, `doctor`.

- **install.sh** (`curl -fsSL https://raw.githubusercontent.com/yllonnoce/VJHStudio/main/install.sh | bash`): `main(){…}; main "$@"` wrapper; git (macOS → `xcode-select --install` then re-run; Linux → apt/dnf/pacman/zypper via sudo), uv (`astral.sh/uv/install.sh --no-modify-path`), clone or `pull --ff-only`, `uv sync --frozen`, `vjhstudio migrate`, shortcut (Linux `~/.local/share/applications/vjhstudio.desktop` + Desktop copy; macOS `~/Desktop/VJHStudio.command`), then `exec start.sh` unless `--no-start`. Flags `--no-start --branch --dir`.
- **install.bat** (`curl.exe -fsSLo %TEMP%\install.bat …/install.bat && %TEMP%\install.bat`): cmd + `curl.exe` + `tar.exe` (+ `winget` if present, `cscript` optional). git via winget or portable MinGit (release JSON parsed with `findstr`/`for /f`, extracted with tar); uv via winget or `uv-x86_64-pc-windows-msvc.zip` (ARM64 variant when `%PROCESSOR_ARCHITECTURE%`==ARM64); clone/pull; `uv sync --frozen`; migrate; Desktop shortcut via a generated `.vbs` (`WScript.Shell.CreateShortcut`, icon, minimised) with `.url` fallback; `start "" start.bat` unless `/nostart`. Runs from `%TEMP%`, never from the checkout.
- **start.sh**: resolve uv/git, export env incl. `VJHSTUDIO_LAUNCHER=1`, loop: `uv run --frozen vjhstudio serve --port $PORT $OPEN`; exit 75 → `OPEN=--no-browser`, sleep 1, continue; else exit with code. Wrapped in `main()` so git pull cannot corrupt a running script.
- **start.bat**: copies itself to `%TEMP%\rs_start_%RANDOM%.bat` and `call`s the copy (the running file is never the one git rewrites); the copy resolves uv/git, sets env, and loops with labels on `errorlevel 75`; other codes print and `pause`.
- **scripts/restart_helper.bat**: fallback when the server was not started by a launcher: waits for the PID to exit (`tasklist`), then `start "" /min start.bat --no-browser`.
- **uninstall.sh / uninstall.bat**: POST `/api/shutdown` (localhost only), wait, remove `.venv`, remove shortcuts (.desktop, .command, .lnk/.url on Desktop and OneDrive Desktop), keep `data/` unless `--purge` / `/purge` with a typed `DELETE` confirmation, print that the checkout folder and uv/git are left for the user to delete.
- **Install choices (added 2026-09-20)**: the installer asks two plain yes/no questions: (1) "Start VJHStudio automatically when you log in (run as a service)?" and (2) "Create a desktop link?". Either, both or neither. Service = Linux `~/.config/systemd/user/vjhstudio.service` (`systemctl --user enable --now`), macOS `~/Library/LaunchAgents/com.yllonnoce.vjhstudio.plist` (`launchctl load`), Windows `schtasks /Create /SC ONLOGON /TN VJHStudio /TR "<home>\start.bat --no-browser"` (no PowerShell). The choice is written to `data/install.json` so the uninstaller knows exactly what to remove. Service mode runs `start.sh --no-browser` / `start.bat --no-browser`; the desktop link opens the browser.
- **Simplicity rule (user, 2026-09-20)**: installation files and their descriptions must be very simple: one obvious path per OS, short scripts with plain-English comments, README install section as 3-5 numbered steps.
- **Uninstall** removes the service (disable + delete unit/plist/task), the desktop link, `.venv`, and `data/install.json`; keeps `data/` unless the user opts in.
- **macOS notes (added 2026-09-20)**: Apple TN3179 states the "Local Network" privacy permission covers only broadcast-capable interfaces (Wi-Fi/Ethernet), never loopback, and command-line tools started from Terminal are exempt. So the app always opens and prints `http://127.0.0.1:<port>/` (never `localhost`, which Safari may resolve to `::1` while uvicorn listens on IPv4). `vjhstudio doctor` on macOS prints: the 127.0.0.1 note; "if macOS asks to allow Local Network access: System Settings > Privacy & Security > Local Network"; and the Gatekeeper step for a downloaded launcher (right-click > Open once, or `xattr -d com.apple.quarantine <file>`). The README macOS section repeats these three lines.
- Bootstrap docs in README: one-liners, SmartScreen "Run anyway" note, Gatekeeper right-click→Open note, OneDrive Desktop note.

## How implementation will proceed

After approval: `git init` in `/mnt/Transfer/5FD1E1415AC80F29/dev/RunWare`, commit this design as `docs/superpowers/specs/2026-09-20-vjhstudio-design.md`, then use the superpowers writing-plans skill to produce a step-by-step plan for Phase 1 and execute it with TDD; each later phase gets its own plan. First coding step in Phase 1 is a 20-line probe against the real `runware-sdk` (run one cheap `promptEnhance`, one `content.get_model_pricing`) to confirm return shapes before the adapter is written.

## Implementation phases (each a separate implementation-plan chunk)

1. **Skeleton**: pyproject/uv, package layout, config/paths, secrets stash, db + Alembic `0001_initial`, boot, `/api/health`, base template, Settings page with API key + test balance, CLI `serve|migrate|version|doctor`, start.sh/start.bat, tests scaffold. → app boots on 8080, key saved, balance shown.
2. **Catalog & pricing**: content API client, normalization, curated JSON snapshot, price-sorted dropdown partials, Refresh prices, Models page with live modelSearch add. Tests on normalization/sort.
3. **Image generation**: prompt compose (Python + JS mirror + fixtures), Generate page (image), task builder, runner policy, JobRunner, download + sidecar, queue panel, projects, gallery, remix, cost meter/usage. Tests with FakeRunware.
4. **Assets & video**: uploads + asset library, lazy mediaStorage upload, reference picker, video mode (frameImages, provider settings, duration/resolution presets), cancel semantics, estimate for video.
5. **Prompt library & polish**: save/load/duplicate/favourite/search, promptEnhance and textInference polish with text-model dropdown.
6. **Self-update, backups, restore/merge, installers**: update.py + restart.py + restarting page, archive create/restore/merge with dry-run, uninstallers, install scripts, README. GitHub Actions CI (Linux + Windows matrix).

## Verification

- `uv run pytest` green on Linux and Windows (CI matrix).
- Fresh install on each OS via the one-liner: browser opens at `http://127.0.0.1:8080`, Settings → key → Test shows balance.
- Generate an image with FLUX.1 dev, a video with LTX-2.3 (cheapest) using an uploaded first frame; files appear under `data/outputs/<project>/` with sidecars; gallery filters, remix and download work; cost rows match `includeCost` values; header balance drops.
- Dropdowns: image/video/text lists ordered by price desc with prices in labels; Refresh prices updates `catalog_prices_refreshed_at`.
- Push a trivial commit to `main`; Settings → Check shows 1 behind; Update now → log → restart → `/api/health` shows the new commit; DB backup present in `data/backups/`.
- Add a migration `0002`, update again: pre-migrate backup created, schema revision advances.
- Backup with outputs → restore on a second machine → merge a second archive → counts in preview match inserted rows; no duplicates by slug/sha256/filename.
- Uninstall keeps `data/`; `--purge` removes it only after typing DELETE.

## Model constraints (Phase 7, added 2026-09-22)

**Problem.** Two live video jobs failed in ways the app should have prevented: Kling VIDEO 3.0 4K only accepts 3840x2160, 2160x3840 or 2880x2880 (the app sent the generic 720p preset, and the runner's fallback then dropped `width` alone, ending in "Missing required parameter: width/height"); Runway Aleph 2.0 is a video-to-video editor that requires `inputs.video`, which the form cannot supply. The catalog knew neither fact.

**Free sources of truth.** RunWare validates every request *before* billing and its rejection text spells out the rule, in this order: unknown parameter names → missing required fields → prompt → values (sizes, durations). Verified probes (balance unchanged):
- an unknown key (`vjhProbe: 1`) → `Unsupported use of 'vjhProbe' parameter. … Allowed values are: 'includeCost', 'taskUUID', … 'width', 'height', 'duration', 'providerSettings', 'fps', …` → the model's accepted parameter names;
- `width: 1, height: 1` → either `Unsupported use of width/height parameters. … Supported values are: '3840x2160', '2160x3840', '2880x2880'` (Kling; Wan uses `'1280*720'`), or `Invalid value for 'width' parameter. Video width must be an integer value between 128 and 2048, in multiples of 64.` (LTX, FLUX), or `Unsupported width/height combination for this model architecture.` (Veo: no list);
- a model that needs an input → `Missing required parameter: 'inputs.video'`.
- The public docs page `https://runware.ai/docs/models/<slug>` (slug = content API `model` field; needs a browser-like User-Agent, no key) has one `<dl class="component-APIParameter" id="request-<name>">` per parameter with `<span data-name="type|required|min|max|step|default|const|items|paired">` attributes, an "Allowed values" list of `<code>` items for enumerated parameters (Veo duration 4/6/7/8), and a `component-ModelDimensions` table whose `<code class="dimension-value">` cells alternate label ("4K (16:9)") and size ("3840x2160").

**Probe safety rules (binding).** A probe must never be a request RunWare could accept. (1) The parameter probe always carries the unknown key, which is rejected first. (2) The size probe is sent only when the parameter probe listed both `width` and `height`, always with `width: 1, height: 1` (RunWare's minimum is 128 everywhere), a valid prompt (≥ 3 characters), no `inputs`, and no other fields. (3) Models whose parameter list has no width/height get no second probe. (4) The harvest reads the account balance before and after and stops with a loud error if it changed. (5) Nothing else is ever sent by the harvest. (6) Added 2026-09-22 after the first live run: some providers validate provider-side and ACCEPT the unknown-key probe (Gemini Omni Flash, Luma Ray 3.2, Riverflow 2.0/2.5 Pro each generated and billed a result, $3.40 in total); models whose AIR starts with `google:gemini`, `luma:` or `sourceful:` are therefore never probed (docs page only), any SDK error that can only happen after submission (a polling timeout, a provider or server error, an unknown code) is treated as an accepted probe and stops the harvest, the balance is read before and after EVERY model's probes so a run stops after at most one charge, and a model whose probe was billed is marked `constraints_json.probe.blocked` and never probed again. A "fully-formed request as a final check" is explicitly out of scope: RunWare validates the prompt before sizes, so no field is known to be checked last, and the search for one is what cost $1.17 on 2026-09-22.

**Storage.** `catalog_models.constraints_json` (JSON, nullable) and `constraints_updated_at`:
```json
{"params": ["width","height","duration","fps","providerSettings", "..."],
 "inputs": {"video": {"required": true}, "frameImages": {"required": false, "min_items": 1, "max_items": 2}},
 "dims": {"mode": "list", "list": [[3840,2160],[2160,3840],[2880,2880]], "labels": {"3840x2160": "4K (16:9)"}},
 "duration": {"type": "integer", "min": 3, "max": 15, "step": 1, "default": 5, "values": [4,6,7,8]},
 "fps": {"min": 1, "max": 120, "default": 25}, "steps": {"min": 1, "max": 100, "default": 28}, "strength": {"min": 0, "max": 1, "step": 0.01, "default": 0.8},
 "sources": {"docs": "2026-09-22T10:00:00", "api": "2026-09-22T10:00:05", "observed": null}}
```
`dims.mode` is `list` (enumerated), `rule` (`min`/`max`/`step`) or `unknown`. Merge precedence per field: API probe > docs page > curated `tiers.video` > nothing; an `observed` correction from a real job (below) overrides the `dims` block. Keys the sources did not supply are absent, never invented.

**Harvest.** `services/constraints.harvest()` walks the visible image and video catalog (or a given list of AIRs): fetch + parse the docs page (5 concurrent), then the two API probes (sequential, respecting the safety rules), merge, store, and report per model `{air, docs: ok|missing|error, api: ok|skipped|error, dims_mode}`. Triggered from Models → "Harvest constraints" (background task with a 2 s status partial, like Refresh prices) and from `vjhstudio probe [--kind image|video] [--air AIR …] [--no-docs] [--no-api]`. Never automatic.

**Generate page.** Sizes: `list` mode → a single select of labelled sizes (the current or default first), no free inputs; `rule` mode → the presets that satisfy the rule plus width/height inputs carrying `min`/`max`/`step`; `unknown` → today's behaviour (curated `tiers.video.dims`, else generic presets). Durations: `values` → select; `min`/`max`/`step` → number input with those attributes; neither → curated list or the generic input. Model dropdowns exclude video models that can neither text-to-video nor image-to-video (video-only editors and upscalers: no `io:text-to-video` and no `io:image-to-video`, or `inputs.video.required`); they stay on the Models page with the badge "video-to-video only — not supported yet". Image-to-video-only models (no `io:text-to-video`) carry a "needs a first frame" badge in the dropdown label and the parameters panel. Pre-flight in `services/generate`: refuse to enqueue when the model requires `inputs.video` ("This model edits an existing video. VJHStudio cannot supply one yet.") or when it has no text-to-video capability and no first frame was chosen ("This model needs a first-frame image. Add one under References."). Estimates use the chosen size's tier as before.

**Runner.** `width`/`height` are never dropped. A validation rejection whose message carries `Supported values are: '…'` is parsed into a size list and the task is retried once with the nearest listed size (smallest aspect-ratio difference, then closest area), recorded as `{"field": "width/height", "action": "corrected", "from": [w,h], "to": [w2,h2]}`; a `between A and B, in multiples of N` message snaps each side into range and to the multiple. The job service persists a correction into the model's `constraints_json.dims` (`mode: list` with the parsed list, or `rule`) with `sources.observed` set, so the next visit to the Generate page already offers the right sizes.

**Curated snapshot.** `curated_models.json` version 3 ships `constraints` for the curated models (Kling 3.0 Standard/4K sizes, LTX rule, Veo sizes and durations, Wan sizes, FLUX rule, Nano Banana Pro sizes) so a fresh install behaves before any harvest. Version bump to 0.3.0 with a CHANGELOG entry and a README "Models" paragraph explaining the Harvest button and that it is free.
