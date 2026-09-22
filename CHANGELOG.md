# Changelog

All notable changes to VJHStudio are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.3.0 — 2026-09-22

### Fixed

- Harvest money safety after the first live run: a polling timeout now counts as an accepted (billed) probe and stops the run; the balance is checked after every model, not only at the end; providers that ignore unknown parameters (Gemini, Luma, Sourceful) are never probed and get docs-page constraints only; a model whose probe was billed is remembered and skipped forever.

### Added

- Model constraints harvest: Models → "Harvest constraints" learns what each catalog model
  actually accepts — sizes, durations and required inputs — from RunWare's public docs pages and
  from RunWare's own pre-billing validation errors. It never generates anything and never spends
  money; the balance is checked before and after and the run stops at once if it ever moves. Also
  available as `vjhstudio probe [--kind image|video] [--air AIR ...]`.
- Constraint-aware Generate page: once a model's sizes are known, Generate only offers the sizes
  that model accepts (a size list, or free width/height inputs within the model's rule) and durations
  render as a matching select or a bounded number input. Video-only editors that need an existing
  video are hidden from the model dropdown; models that can only animate an existing first frame are
  labelled "needs a first frame".
- Runner size correction: a job whose model rejects the posted size is retried once with the nearest
  size RunWare actually listed, and the correction is saved so the Generate page offers the right
  sizes next time — no probe or harvest required for that model to become size-correct.
- Curated snapshot v3: sizes and durations for Kling 3.0 Standard and 4K, LTX, Veo 3.1, Wan 2.7,
  FLUX.1 [dev] and Nano Banana Pro ship with a fresh install, before any harvest ever runs; a new
  Kling 3.0 4K catalog entry (`klingai:kling-video@3-4k`) was added alongside Kling 3.0 Standard.

### Fixed

- Kling VIDEO 3.0 4K jobs no longer get rejected for the wrong size: the app now knows it only
  accepts 3840x2160, 2160x3840 or 2880x2880.
- Video-only editors (video-to-video models with no text-to-video capability) are no longer offered
  on the Generate page as if they could start from a prompt.

## 0.2.0 — 2026-09-20

### Added

- Self-update from Settings → Updates: checks the update server, shows what is new, backs up the
  database, pulls, installs and migrates, then restarts the app. If any step fails, the backup is
  restored and the app is rolled back to the commit it was running before. The header shows an
  "Update available" badge when the app is behind.
- Backup archives: create a backup of the database with optional uploads and outputs, download it,
  restore it (replacing everything, with a safety copy taken first), or merge it (adding only what
  is missing, previewed before it runs, never copying settings or the API key).
- `install.sh` / `install.bat` and `uninstall.sh` / `uninstall.bat`: install Git and uv if missing,
  download the app, ask whether to start automatically at login and whether to add a desktop link,
  and start the app. Uninstalling removes exactly what was set up, keeping your data unless you
  pass `--purge` and confirm by typing `DELETE`.
- CLI commands: `vjhstudio update --check` / `vjhstudio update`, `vjhstudio backup`, and
  `vjhstudio restore <zip>` (with `--merge`).

## 0.1.0

Baseline release: Phases 1-5. Local image and video generation against RunWare.AI, projects and
prompts, the gallery, the prompt library with polish, remix and reference images, and the settings
and maintenance pages.
