# Music & SFX, Speech and 3D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Three new generation kinds (`audio`, `speech`, `3d`) with tabs on Generate, catalog sections, queue, gallery players and a 3D viewer.

**Architecture:** One generic "media kind" path beside the image/video ones: request schemas → pure task builders → the existing runner → generic result parsing → the existing downloader and outputs service. Forms are server-rendered from each model's harvested `constraints_json.fields`.

**Tech Stack:** FastAPI, Jinja2, htmx, SQLAlchemy/SQLite, runware-sdk, pytest with `FakeRunware`, vendored `<model-viewer>`.

**Spec:** `docs/superpowers/specs/2026-10-01-audio-speech-3d-design.md`

**Execution:** native, in the authoring session (owner said "make it").

## Global Constraints

- Kind strings are exactly `audio`, `speech`, `3d` (≤ 8 chars; no migration).
- Task types: `audioInference` for audio and speech, `3dInference` for 3D. `outputType: "URL"`, `includeCost: true` always.
- No API probe is ever sent for the three kinds (docs pages only).
- No live RunWare call in tests. Paid live checks need the owner's go-ahead.
- Windows scripts stay `.bat`; installers untouched. Line length 100, ruff clean.

## Review Focus

1. A model with no harvested `fields` (search-added): the form must still offer the required box (prompt / text / image) and submit.
2. A 3D request with an image whose asset was deleted: refused before a job row exists.
3. A reply with no usable URL (3D `outputs` in an unexpected shape): the job fails with a message naming the reply, not a silent "succeeded" with zero files.
4. A voice not on the model's list (stale remix): sent as typed; RunWare's free rejection reaches the job card.
5. An old `/generate?remix=<id>` link for an audio/speech/3d output: lands on the matching tab, never the image form.

## File Structure

| File | Responsibility |
|---|---|
| `vjhstudio/schemas/media.py` (new) | `AudioRequest`, `SpeechRequest`, `Model3DRequest`, `MEDIA_KINDS`, `REQUEST_FOR_KIND` |
| `vjhstudio/runware/tasks_media.py` (new) | `build_media_task(kind, req, task_uuid, media, model_row) -> dict` and the three builders |
| `vjhstudio/runware/results.py` | `parse_items` reads `audioURL` and walks 3D `outputs` |
| `vjhstudio/runware/pricing.py` | units `per_output`, `per_second`, `per_1k_chars` for the new kinds |
| `vjhstudio/services/constraints.py` | `fields` in `merge_sources`; `media_fields(c)`; capability rule per kind; no probes for the new kinds |
| `vjhstudio/services/catalog.py` | `KINDS`, `CATEGORIES`, `audio_kind()`, labels, badges |
| `vjhstudio/services/generate.py` | `enqueue_media(session_factory, paths, kind, req, ...) -> Job` with pre-flight |
| `vjhstudio/services/jobs.py` | kind dispatch in `_begin`, `_build_task`, `_params`, `_persist` |
| `vjhstudio/services/media_forms.py` (new) | `form_ctx(session, kind, air, values, errors)`, `parse(kind, form, fields)`, `estimate(session, kind, air, form)` |
| `vjhstudio/web/routes/generate_media.py` (new) | `GET/POST /generate/{audio,speech,3d}`, `/hx/media/params`, `/hx/media/estimate` |
| `templates/pages/generate_media.html`, `generate/_media_params.html`, `generate/_media_estimate.html`, `generate/_mode_tabs.html` (new) | the page shell and per-kind fields |
| `templates/gallery/_card.html`, `_detail.html`, `pages/gallery.html`, `generate/_job_card.html`, `pages/models.html`, `partials/_recent_strip.html` | players, viewer, filter, headings |
| `static/vendor/model-viewer.min.js`, `static/img/audio.svg`, `static/img/model3d.svg` | viewer and icons |
| `vjhstudio/data/curated_models.json` | the 27 models with `fields`, `inputs`, prices |

## Tasks

### Task 1: Catalog kinds, classification and prices
**Files:** `runware/pricing.py`, `services/catalog.py`, `web/routes/catalog.py`, `pages/models.html`; tests `tests/test_pricing.py`, `tests/test_media_catalog.py`.
**Produces:** `pricing.normalize_price(kind, pricing, listing)` returning unit `per_output|per_second|per_1k_chars` for the new kinds; `catalog.KINDS = ("image","video","text","audio","speech","3d")`; `catalog.CATEGORIES = ("image","video","text","audio","3d")`; `catalog.audio_kind(item: dict, constraints: dict | None) -> "audio"|"speech"`; `catalog.KIND_LABELS`.
- [x] Tests: MiniMax Music → `per_output` 0.15; Mirelo → `per_second` 0.00025; ACE-Step overview text → `per_second` 0.0001; xAI TTS → `per_1k_chars` 0.015; Gemini TTS → no primary; Tripo → `per_output` 0.3; `audio_kind` for all 20 audio models; refresh stores a speech model under kind `speech`; Models page shows the three new sections with counts.
- [x] Implement; run `pytest tests/test_pricing.py tests/test_media_catalog.py tests/test_web_models.py`.

### Task 2: Harvested fields and capability rules
**Files:** `services/constraints.py`; tests `tests/test_media_constraints.py`.
**Produces:** `constraints.PLUMBING_PARAMS`; `merge_sources` writes `out["fields"]`; `constraints.media_fields(c) -> dict`; `is_generate_capable` for the new kinds; harvest records `api: "skipped: docs page only for this kind"` and sends nothing for them.
- [x] Tests: fields stored from a docs dict (plumbing keys left out); capability true/false per kind and caps; a 3D model with required `inputs.image` stays capable; music cover (audio-to-audio only) is not; harvest over an audio row makes no `run` call.
- [x] Implement; run the file plus `tests/test_constraints.py tests/test_catalog_harvest.py`.

### Task 3: Request schemas and task builders
**Files:** `schemas/media.py`, `runware/tasks_media.py`; tests `tests/test_media_tasks.py`.
**Produces:** the three request models; `build_media_task(kind, req, task_uuid, media, model_row) -> dict`.
- [x] Tests: audio task shape (`audioInference`, prompt, format, `duration` only when the model lists it and clamped to its range, `settings.lyrics`/`instrumental` only when listed, everything sent when no fields are known); speech task shape (`speech.text/voice/language/speed`); 3D task shape (`3dInference`, `inputs.image` vs `inputs.images` by the model's inputs block, prompt omitted when blank); `extra_json` cannot override protected keys; validators (blank prompt/text, 3D with neither prompt nor image).
- [x] Implement; run the file.

### Task 4: Results, enqueue and the job runner
**Files:** `runware/results.py`, `services/generate.py`, `services/jobs.py`; tests `tests/test_media_jobs.py`, `tests/test_download.py` untouched.
**Consumes:** Task 3. **Produces:** `generate.enqueue_media(session_factory, paths, kind, req, *, source=..., estimate_usd=None) -> Job`; `results.parse_items` handling `audioURL` and `outputs`.
- [x] Tests: parse `audioURL`; parse several `outputs` shapes (`{"files":[{"url":…}]}`, `{"glb":"https://…"}`, nested), preferring the `.glb`; a job per kind succeeds end to end with the right file extension, an Output row of that kind, cost recorded, no thumbnail; a reply with no URL fails the job with a clear message; pre-flight refusals (Review Focus 2, plus image on a text-only 3D model, music cover model); retry re-validates the right request class.
- [x] Implement; run the file plus `tests/test_jobs.py tests/test_video_jobs.py`.

### Task 5: Generate tabs, forms and estimates
**Files:** `services/media_forms.py`, `web/routes/generate_media.py`, templates listed above, `pages/generate.html`, `web/app.py` (router), `static/css/app.css`; tests `tests/test_web_media.py`.
**Produces:** `GET /generate/{audio|speech|3d}`, `POST` the same, `GET /hx/media/params?kind=&air=`, `GET /hx/media/estimate`.
- [x] Tests: each page renders five tabs with the right one selected and only capable models; fields follow the model (lyrics for MiniMax Music, length range for ACE-Step, voices datalist for xAI, image picker for TRELLIS); a model with no fields still renders the required box (Review Focus 1); POST queues a job and returns the queue panel; POST without a key shows the no-key banner; validation errors re-render the fields with the message; estimates per unit; `/generate?remix=<audio output>` redirects to `/generate/audio?remix=` pre-filled (Review Focus 5); a typed voice not on the list is sent as typed (Review Focus 4); the Image/Video page shows the three extra tabs.
- [x] Implement; run the file plus `tests/test_web_generate.py tests/test_web_video.py`.

### Task 6: Gallery players, 3D viewer, job cards, home strip
**Files:** gallery templates, `web/routes/gallery.py`, `generate/_job_card.html`, `partials/_recent_strip.html`, `static/vendor/model-viewer.min.js` + `LICENSES.md`, icons, css; tests `tests/test_web_gallery.py` additions.
- [x] Tests: audio/speech card has `<audio controls>`; 3D card shows the icon; 3D detail has `<model-viewer` with the file URL and the vendored script tag; no "Use as reference" and no Size row for the new kinds; kind filter offers five kinds and filters; job card shows an icon for the new kinds; Remix links to the matching tab.
- [x] Vendor model-viewer; implement; run the file.

### Task 7: Curated snapshot, docs, version
**Files:** `data/curated_models.json` (version bump), README, CHANGELOG, spec cross-reference; tests `tests/test_catalog_service.py` additions.
- [x] Generate the 27 rows from the live docs pages with a scratch script (free GETs), add them with `fields`/`inputs`/prices; test that a fresh boot has capable audio, speech and 3D models and a text-to-3D default.
- [x] README "Music, speech and 3D" section; CHANGELOG; full suite, ruff.

### Task 8: Live checks (gated)
- [ ] With the owner's go-ahead only: one run each on ACE-Step Turbo, xAI TTS, Hunyuan 3D Rapid through the app's own pipeline against `data-debug`; pin the 3D `outputs` shape in `results.py` and its test; record the findings in the RunWare nuances.

## Outcome (2026-10-01)

Tasks 1–7 done in the authoring session: 1122 tests, ruff clean, the three tabs, the
Models sections and the Gallery checked in a scratch instance on a copy of `data-debug`
(no key). Deviations from the plan as written: the per-kind default model is a constant
(`media_forms.PREFERRED`) rather than a setting; SAM 3D is hidden (it requires a mask);
the Retry route was found rebuilding every job as an image request and now dispatches
by kind (video included). Not verified: how the forms and the 3D viewer *look* (no
headless browser was available) and Task 8.
