# Changelog

All notable changes to VJHStudio are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.4.0 — 2026-09-22

### Added

- Idea chips under Style, Mood, Lighting, Camera, Composition, Colour and Extras on the Generate
  page: click a phrase to add it to that field, click it again to remove it. Subject, Extras,
  Negative and the final prompt are now growing text areas instead of single-line inputs.
- A home dashboard: "Create an image" / "Create a video" cards, your 12 most recent outputs, the
  active queue, balance/spend/output stats, and your projects, all on one page.
- `?open=<id>` on the gallery deep-links straight into the lightbox for that output.
- A portrait (9:16) twin for every video resolution preset.
- A Reset button on the Generate page that clears the form in the current mode.
- The Models page can sort each list by price (default) or by name.
- A Queue page (`/queue`): the live queue plus a history table of the last 50 finished jobs; the header chip links there instead of the Generate form.
- Upload a first frame (or seed/reference image) straight from the Generate page; models that need a first frame say so in the References section.
- Video outputs get a real thumbnail (a frame from the clip) in the gallery, the lightbox, job cards and the home page; existing videos are filled in on the next start (`vjhstudio thumbs` does it on demand).

### Changed

- Generate is now a two-column page: the prompt builder on the left, a sticky "Model & settings"
  rail on the right, and results below both. Every page has a title and subtitle, and the current
  page is highlighted in the navigation. Action buttons are inline instead of full-width bars, and
  prompt rows are more compact. Navigation wraps instead of overflowing on narrow screens, and the top bar stays put while the page scrolls underneath it.

### Fixed

- The output details no longer cover the top bar: the lightbox opens below the navigation and
  leaves it usable (Esc, the close button and a click on the dimmed area all still close it).
- Opening a second output's details showed the first one again; each click now reloads the panel.
- Tablet and laptop widths (576–1400 px) had no side gutter: content sat flush against the viewport edge. Every page keeps a 16 px gutter now.
- The video Resolution dropdown gets two thirds of its row so the full size label ("720p portrait (9:16) — 704×1280") is readable.
- "Mark seen" on the queue panel did nothing visible; it is now "Clear finished" and hides the finished jobs from the panel (the Queue page keeps the full history).
- The header balance chip asks RunWare again when its cached value is older than 30 seconds and after every finished job; if RunWare cannot be reached it shows the last known amount marked with a question mark. Before, the cache was only refreshed by the Test button in Settings.
- Headings and inputs no longer render with Pico's light-theme colours when a dark theme is active
  (the theme bridge now outranks Pico's specificity).
- No page scrolls horizontally at phone width.

## 0.3.0 — 2026-09-22

### Fixed

- Harvest money safety after the first live run: a polling timeout now counts as an accepted (billed) probe and stops the run; the balance is checked after every model, not only at the end; providers that ignore unknown parameters (Gemini, Luma, Sourceful) are never probed and get docs-page constraints only; a model whose probe was billed is remembered and skipped forever.

### Changed

- The RunWare transport now defaults to websocket (Settings → General still offers rest).

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
