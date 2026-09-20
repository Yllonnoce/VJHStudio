# VJHStudio Phase 4 (Assets & video) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload sample images and videos into a tagged asset library, use them as references (image-to-image, first/last frame for video), and generate videos through RunWare with the same background job pipeline, progress bar, gallery and cost tracking as images.

**Architecture:** `services/assets.py` stores uploads (sha256-deduplicated) under `data/uploads/`, makes thumbnails, and lazily uploads a file to RunWare media storage (`client.media_storage`) caching `media_uuid` for 6 days. The JobRunner resolves asset ids → media UUIDs before building the task (`media` map already accepted by `build_image_task`). `schemas/video.py::VideoRequest` + `runware/tasks.py::build_video_task` produce `videoInference` tasks (`inputs.frameImages`, `providerSettings`, duration/resolution/fps from the catalog presets). The Generate page gains a Video tab and a reference picker; outputs of kind `video` get an mp4 file, a poster thumbnail (first frame via Pillow is impossible without ffmpeg → use the browser `preload="metadata"` and a generic poster) and the same gallery card with a `<video>` element.

**Tech Stack:** as before, plus `python-multipart` (already), Pillow for image dims/thumbs; no ffmpeg dependency (optional `imageio-ffmpeg` is NOT added).

**Spec:** `docs/superpowers/specs/2026-09-20-runwarestudio-design.md` — "Uploads", "Data model → assets", "RunWare adapter" (video task shape, provider settings, frameImages `{"image": <uuid>, "frame": "first"|"last"}`), "UI design → Generate (video mode, reference picker), Assets page", "Model-family rules", "Working progress bar".

## Global Constraints

- Package `vjhstudio`; commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .` clean before every commit; `runware/` and `services/` never import `web/`; DB-only handlers sync `def`; no CDN; no network in tests (FakeRunware + MockTransport).
- Uploads: accepted image MIME `image/png, image/jpeg, image/webp`; video `video/mp4, video/webm, video/quicktime`; max size = setting `uploads.max_mb` (default 200) → 413 with a message; stored as `data/uploads/<sha256[:12]>.<ext>` (re-upload of identical bytes returns the existing row); image dims via Pillow; video dims None; thumbnail `data/thumbs/asset-<id>.jpg` for images, none for video (card shows a `<video preload="metadata" muted>`).
- Tags stored as `,a,b,` normalized (lowercase, trimmed, de-duplicated); filter with `LIKE '%,tag,%'`.
- RunWare media upload: `client.media_storage({"operation": "upload", "media": "data:<mime>;base64,<...>"})` → `[{"mediaUUID": ..., "mediaURL": ...}]`; cache `media_uuid`/`media_url`/`media_uploaded_at`; re-upload when older than 6 days; failure → `RunwareError` classified and surfaced as the job error (`error_code="upload"` message "Could not upload <name> to RunWare: …").
- Video task (spec): `taskType="videoInference"`, `taskUUID`, `model`, `positivePrompt`, `outputType="URL"`, `outputFormat` (MP4|WEBM), `includeCost=True`, `duration` (from the model's `video.durations`, default the middle value), `width`/`height` from the chosen resolution preset (`720p`→1280x720, `1080p`→1920x1080, `4K`/`4k`→3840x2160, `480p`→854x480; a `resolution` string is sent ONLY as `resolution` when the curated preset says so — default: send width/height), optional `fps` (only when the model lists `fps`), optional `seed`, `negativePrompt` only when the model is not instruction-family (video models ignore it: omit by default), `inputs.frameImages: [{"image": <uuid>, "frame": "first"}, {..."last"}]` when set, `inputs.referenceImages` when set, `providerSettings: {<provider>: {...}}` from the model's `provider_settings_schema` values (provider key = AIR prefix before `:` mapped: `google`→`google`, `klingai`→`klingai`, `bytedance`→`bytedance`, `alibaba`→`alibaba`, `lightricks`→`lightricks`); `extra_json` merged last. Runner: same policy; `deliveryMethod` is left to the SDK (async by default) with `timeout_s` from `runware.timeout_s`.
- Video outputs: `outputs.kind="video"`, ext from `outputFormat`, `duration_s` from the request, `width/height` from the request; sidecar has the same keys plus `duration`; gallery card renders `<video src=… preload="metadata" muted controls>` with the generic poster `/static/img/video-poster.svg`; `thumb_rel_path` None.
- Estimate for video: `price_primary × duration` (per_second models) with the "with audio" rate when `generateAudio`/`sound` is on and a matching rate label exists in `tiers.rates` (label contains "with audio"); n/a when unknown.
- Progress: video jobs use the same estimated bar (`expected_ms` from `typical_latency_ms`, e.g. Veo ≈ 90 s) with stage labels; cancel copy says "Stop waiting (RunWare still bills a submitted video)".
- Schema change (migration `0003_phase4`): `jobs.kind` already exists; add `outputs.poster_rel_path VARCHAR(300) NULL` (reserved, nullable) and index `ix_assets_tags` on `assets.tags`. Nothing else.

## File Structure

```
vjhstudio/services/assets.py            store_upload(session, paths, filename, content, mime, tags) -> Asset ; list(session, kind, tag, q, page) ; get ; set_tags ; delete(session, paths, id) ; abs_path ; thumb_path ; normalize_tags ; ensure_media_uuid(client, session_factory, paths, asset_id) -> str (async) ; media_map(client, session_factory, paths, ids) -> dict[int,str]
vjhstudio/schemas/video.py              VideoRequest (pydantic)
vjhstudio/runware/tasks.py              + build_video_task(req, task_uuid, media, model_row) ; RESOLUTIONS ; provider_key(air)
vjhstudio/services/generate.py          + enqueue_video(session_factory, paths, req)
vjhstudio/services/jobs.py              + kind dispatch (image|video) ; media resolution before build ; video persist (no thumbnail)
vjhstudio/services/outputs.py           + video-aware prepare (ext, duration_s, no thumb)
migrations/versions/0003_phase4.py
vjhstudio/web/routes/assets.py          GET /assets ; GET /hx/assets ; POST /assets/upload ; POST /assets/{id}/tags ; DELETE /assets/{id} ; POST /assets/{id}/push ; GET /hx/assets/picker ; GET /files/uploads/{filename} ; GET /files/asset-thumbs/{name}
vjhstudio/web/routes/generate.py        + GET /generate/video ; POST /generate/video ; /hx/model-options?mode=video ; estimate for video ; reference chips in initial
vjhstudio/web/templates/pages/assets.html, assets/_grid.html, _card.html, _tag_editor.html, _picker.html ; generate/_video_params.html, _refs.html ; gallery/_card.html (video branch) ; static/img/video-poster.svg
tests/test_assets.py, test_video_tasks.py, test_video_jobs.py, test_web_assets.py, test_web_video.py
```

---

### Task 1: Asset library service (store, tags, thumbnails, delete, listing)

**Files:** Create `vjhstudio/services/assets.py`, `tests/test_assets.py`; Modify `vjhstudio/models/asset.py` (no column changes; add `__table_args__` index `ix_assets_tags`), `migrations/versions/0003_phase4.py` (autogenerate: index + `outputs.poster_rel_path`), `vjhstudio/models/output.py` (`poster_rel_path`).

**Interfaces (produces):**
- `assets.ALLOWED_IMAGE = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}`, `assets.ALLOWED_VIDEO = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov"}`
- `assets.UploadError(Exception)` with `.status` (413 too large, 415 unsupported)
- `assets.normalize_tags(text: str) -> str` (`"Fox, animals , fox"` → `",fox,animals,"`), `assets.tags_list(tags: str) -> list[str]`
- `assets.store_upload(session, paths, *, original_name, content: bytes, mime: str, tags: str = "", max_mb: int = 200) -> tuple[Asset, bool]` (`created` flag; dedupe by sha256; writes `data/uploads/<sha12>.<ext>`; image dims via Pillow; thumbnail `data/thumbs/asset-<sha12>.jpg` for images)
- `assets.list_assets(session, *, kind=None, tag=None, q=None, page=1, per_page=48) -> tuple[list[Asset], int]` (newest first; `q` matches original_name/notes)
- `assets.get(session, id)`, `assets.set_tags(session, id, tags) -> Asset`, `assets.set_notes`, `assets.delete(session, paths, id) -> bool` (file + thumb + row; refuse (`return False`) when referenced by a queued/running job's request (`seed_image_asset_id`/`reference_asset_ids`/`first_frame_asset_id`/`last_frame_asset_id` in `request_json`))
- `assets.abs_path(paths, asset) -> Path`, `assets.thumb_rel(asset) -> str | None`, `assets.public_urls(asset) -> dict(url, thumb_url)` (`/files/uploads/<filename>`, `/files/asset-thumbs/asset-<sha12>.jpg`)

**Tests (representative; write the full file):** store png (created True, dims 64x64, thumb exists), re-store same bytes (created False, same id), store mp4 bytes (`b"\x00\x00\x00\x18ftypmp42"` prefix; dims None, no thumb), unsupported mime → UploadError 415, oversize (max_mb=0 with 1-byte content... use `max_mb=1` and 2 MB content) → 413, tags normalize/list, list filters (kind/tag/q/paging), delete removes files, delete refused when a queued job references it.

- [ ] Write tests → fail → implement → migration 0003 via the Alembic snippet (scratch db) → `test_models_match_migrations` green → full suite green, ruff clean → commit `feat: asset library service with dedupe, tags, thumbnails`.

---

### Task 2: RunWare media upload and job media resolution

**Files:** Modify `vjhstudio/services/assets.py` (+ `ensure_media_uuid`, `media_map`, `MEDIA_TTL_DAYS = 6`), `vjhstudio/services/jobs.py` (resolve `media` before `build_image_task`; new failure class `upload`), `tests/fakes/fake_runware.py` (media_storage already scripted), `tests/test_assets.py`, `tests/test_jobs.py`.

**Interfaces:** `async assets.ensure_media_uuid(client, session_factory, paths, asset_id) -> str` (returns cached uuid when `media_uploaded_at` is newer than 6 days; else reads the file, builds the data URI, calls `client.media_storage({"operation":"upload","media": data_uri})`, stores `mediaUUID`/`mediaURL`/now; raises `RunwareError` through); `async assets.media_map(client, session_factory, paths, ids: Iterable[int]) -> dict[int, str]` (skips unknown ids). JobRunner: inside the `async with client_factory(...)` block, before building the task, `media = await assets.media_map(client, self._sf, self._paths, plan.asset_ids)`; an upload `RunwareError` → job `failed`, `error_code="upload"`, message `Could not upload <original_name> to RunWare: <classified message>`.

**Tests:** ensure_media_uuid uploads once then caches (fake records one call; second call no call); expired cache (set `media_uploaded_at` 7 days ago) re-uploads; a job with `seed_image_asset_id` sends `inputs.seedImage == "<uuid>"` in the task (`fake.calls`); upload failure → job failed with code `upload` and the asset name in the message.

- [ ] Tests → implement → green → commit `feat: lazy RunWare media upload for reference assets`.

---

### Task 3: VideoRequest, video task builder, video jobs

**Files:** Create `vjhstudio/schemas/video.py`, `tests/test_video_tasks.py`, `tests/test_video_jobs.py`; Modify `vjhstudio/runware/tasks.py`, `vjhstudio/services/generate.py` (+ `enqueue_video`), `vjhstudio/services/jobs.py` (kind dispatch), `vjhstudio/services/outputs.py` (video prepare), `vjhstudio/services/costs.py` (no change), `vjhstudio/web/static/img/video-poster.svg` (simple play-triangle SVG).

**Interfaces:**
- `VideoRequest(BaseModel)`: `project_id`, `prompt_id=None`, `model`, `form: PromptForm` (no_text default False for video), `final_prompt=None`, `duration: float = 5`, `resolution: str = "720p"`, `fps: int | None = None`, `seed: int | None = None`, `output_format: Literal["MP4","WEBM"] = "MP4"`, `first_frame_asset_id: int | None`, `last_frame_asset_id: int | None`, `reference_asset_ids: list[int] = []`, `provider_settings: dict = {}`, `extra_json: dict = {}`, `title: str | None`; validator duration 1..30.
- `tasks.RESOLUTIONS = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)}` (case-insensitive lookup), `tasks.provider_key(air) -> str` (prefix before `:`), `tasks.build_video_task(req, task_uuid, media, model_row: dict) -> dict` where `model_row` is the `/api/models`-style dict (`tiers.video`, `provider_settings_schema`, `capabilities`); rules in Global Constraints; `duration` clamped to the model's list when present (nearest); `fps` only when `tiers.video.fps` exists.
- `generate.enqueue_video(session_factory, paths, req) -> Job` (model must be `kind == "video"`; title; `expected_ms` from `costs.expected_ms`; `request_json` = `req.model_dump()`; `kind="video"`).
- JobRunner: `_begin` reads `job.kind`; for video builds `VideoRequest` and `build_video_task`; `_persist` for video: `download_items(..., ext=req.output_format.lower())`, `outputs.prepare(..., kind="video", duration_s=req.duration, width/height from RESOLUTIONS)` with no thumbnail; sidecar adds `duration`; `costs.record_usage(task_type="videoInference")`.

**Tests:** build_video_task shapes (t2v basic; i2v with first+last frames → `inputs.frameImages` two entries with `"frame":"first"/"last"`; providerSettings `{"google": {"generateAudio": True}}`; duration clamped to the model list; fps only when listed; resolution mapping; negativePrompt omitted); enqueue_video rejects an image model; end-to-end video job with the fake returning `[{"videoURL": "http://x/v.mp4", "cost": 0.8}]` and a MockTransport serving bytes → output kind video, file `.mp4`, `duration_s == 5`, no thumb, usage row task_type videoInference.

- [ ] Tests → implement → green → commit `feat: video requests, task builder and video jobs`.

---

### Task 4: Assets page, uploads, tag editor, reference picker, file routes

**Files:** Create `vjhstudio/web/routes/assets.py`, templates `pages/assets.html`, `assets/_grid.html`, `assets/_card.html`, `assets/_tag_editor.html`, `assets/_picker.html`, `tests/test_web_assets.py`; Modify `web/app.py` (router), `_header.html` (nav: add Assets after Gallery), `web/urls.py` (+ asset urls), CSS (dropzone, asset grid), `app.js` (dropzone: drag/drop assigns files to the input and submits; upload progress via `htmx:xhr:progress`).

**Routes:** `GET /assets` (page: dropzone form `hx-post="/assets/upload" hx-encoding="multipart/form-data" hx-target="#asset-grid" hx-swap="outerHTML"`, filters kind/tag/q, grid), `GET /hx/assets?kind=&tag=&q=&page=` (grid), `POST /assets/upload` (multipart `files` (multiple) + `tags`; each file → `store_upload`; errors collected into a toast; returns grid), `POST /assets/{id}/tags` (form `tags` → card), `POST /assets/{id}/notes`, `DELETE /assets/{id}` (200 empty; 409 JSON when referenced), `POST /assets/{id}/push` (force `ensure_media_uuid` → card with the "uploaded to RunWare" badge), `GET /hx/assets/picker?kind=image&role=reference|first|last|seed` (compact grid; each item is a `<button type="button" data-asset-id data-thumb data-name data-role>` that dispatches a `ref-picked` window event — JS in Task 5), `GET /files/uploads/{filename}` and `GET /files/asset-thumbs/{name}` (containment guard, inline).

**Tests:** page renders; upload two PNGs + one duplicate → grid shows 2 cards and a toast mentions the duplicate; oversize (set `uploads.max_mb` to 1 via settings then upload 2 MB) → 413 message in a toast/partial; unsupported type → 415 message; tags update; delete → 200 then 404 on file route; delete refused (409) when a queued job references the asset; picker returns buttons with `data-asset-id`; file routes reject traversal.

- [ ] Tests → implement → green → commit `feat: assets page with uploads, tags, picker and file routes`.

---

### Task 5: Generate page video mode and reference chips; gallery video cards; estimate

**Files:** Modify `web/routes/generate.py` (`GET /generate?mode=video`, `POST /generate/video`, model-options `mode=video` → `generate/_video_params.html`, estimate for video, `initial` carries `refs`), templates `pages/generate.html` (mode tabs Image/Video; refs block), new `generate/_video_params.html` (duration select from presets, resolution select, fps when listed, provider settings from schema (bool → checkbox, enum → select, number → input), output format, seed), new `generate/_refs.html` (chips with role select + remove; "+ Add" opens a `<dialog id="ref-picker">` loading `/hx/assets/picker`), `gallery/_card.html` + `gallery/_detail.html` (video branch: `<video preload="metadata" muted controls poster="/static/img/video-poster.svg">`, "Use as reference" for images), `app.js` (`refs` state in `generateForm`: add/remove/role; `ref-picked` handler; hidden inputs `seed_image_asset_id`, `first_frame_asset_id`, `last_frame_asset_id`, `reference_asset_ids[]` rendered by Alpine `x-for`), CSS; `tests/test_web_video.py`.

**Behaviour:** the Video tab switches `mode` (Alpine) and re-fetches `#model-params` with `mode=video` (video models only in the select); `POST /generate/video` parses into `VideoRequest` (provider settings fields are posted as `ps.<key>`), 422 → `_video_params.html` with errors + `HX-Retarget: #model-params`; success → queue panel + `jobs-changed`. Estimate route handles `mode=video` (`duration`, provider audio flag). Remix of a video output pre-fills the Video tab (`initial.mode = "video"`). Gallery `kind=video` filter works; the card shows the poster and plays inline; download serves the mp4 as attachment. Cancel button label for video jobs: "Stop waiting".

**Tests:** `GET /generate?mode=video` shows the video model select with Veo/LTX and `name="duration"`; model-options for `lightricks:ltx@2.3` includes `name="fps"`, for `google:3@2` includes `ps.generateAudio`; estimate for Veo 5 s no audio → "$1.00", with audio → "$2.00"; `POST /generate/video` with the fake returning a videoURL → job succeeds, `/api/jobs` shows `kind == "video"` and an output url ending `.mp4`; `/hx/gallery?kind=video` renders a `<video`; remix of the video output → `initial` has `"mode": "video"` and `"duration": 5`; 422 on `duration=99` with retarget.

- [ ] Tests → implement → green; smoke with a real key on a scratch port: one LTX-2.3 3 s 720p clip (≈ $0.12) → `.mp4` in `data-*/outputs/default/`, gallery plays it → commit `feat: video generation mode, reference chips, video gallery cards`.

---

## Phase 4 exit criteria

- Upload a photo on the Assets page, tag it, use it as the seed image for FLUX (img2img) and as the first frame for an LTX video; both jobs succeed with real files and sidecars.
- Video jobs show the estimated progress bar and stage text; cancel says "Stop waiting".
- The gallery filters by video and plays clips inline; download works.
- Assets referenced by queued jobs cannot be deleted; RunWare uploads are cached and re-done after 6 days.
- Suite green, ruff clean.
