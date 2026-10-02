# Changelog

All notable changes to VJHStudio are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- **The Music & SFX, Speech and 3D tabs caught up with Image and Video.** They remember what you
  typed (each tab on its own, in that browser) and have a **Reset** button; every submit is filed
  in the **Prompts** library and can be loaded back into its tab; each has a default model under
  **Settings**; and the 3D tab can upload a picture on the spot instead of sending you to Assets.
- A **Full screen** button on the 3D viewer. Press it again, or Esc, to come back.
- **Use VJHStudio from other devices on your network.** Start it with `./start.sh --network`
  (`start.bat --network` on Windows) and it shows the address to open on a phone, tablet or
  another computer. **Settings → Network access** shows whether the network is open and the
  address. There is no login, so only use it on a network you trust.
- **Remix, Download and Delete on every Gallery card**, for images, videos, music, speech and 3D
  objects alike. They used to be inside the details panel only.
- **Removing assets is easy to find.** Each asset has a delete cross on its picture, and you can
  tick several and remove them with one **Delete selected**. (Delete used to be the last button at
  the bottom of a very tall card.)
- **Several views for one 3D object.** Tripo, Meshy, Rodin Gen-2 and Hunyuan 3D Pro can build from
  more than one picture of the same object (front, side, back…), which gives them real shape for
  the sides they would otherwise guess. On those models the 3D tab lets you tick up to the
  model's limit; the first one you pick is the main view, and the tiles are numbered in pick order.
- **3D objects show what they look like.** Gallery cards, the queue and the home page now show a
  picture of each 3D object instead of a cube icon. RunWare sends only the model file, so the app
  takes the picture itself, in your browser, the first time the object appears on a page. An object
  built from one of your images shows that image until its own picture is ready.
- The Speech tab's **Voice** and **Language** are real dropdowns listing everything the model
  offers, with a filter box for the long lists (Inworld has 133 voices). Switching model resets
  them to that model's own.
- **Music & sound effects, speech and 3D objects.** Three new tabs on the Generate page beside
  Image and Video. Describe a piece of music or a sound; type a text and pick a voice to read it;
  describe an object or turn one of your pictures into a 3D model. Each form shows only the
  options the chosen model accepts (lyrics, length, voices, textures…), with a cost estimate.
- Music and speech play in their Gallery card. 3D objects open in a viewer you can rotate and
  zoom, with a download for the `.glb` file. The Gallery filter and the Models page cover the
  new kinds, and **Refresh prices** now also fetches RunWare's audio and 3D models (27 today).
- The Image, Video and Text lists on the Models page fold away under their headings, which now
  show how many models each holds. They start closed; the ones you open stay open in that browser.

### Changed

- The home page no longer has the "Create an image" and "Create a video" cards: five kinds of
  thing can be made now, and **Generate** in the menu leads to all of them.

### Fixed

- The 3D tab now opens on **Tripo 3D** instead of Hunyuan 3D Rapid. Building from a description on
  Hunyuan Rapid has been failing at the provider (about 16 minutes, then an error); the form says
  so when you pick that model. Building from an image on it still works.

- An asset that a running 3D job is using can no longer be deleted from under it, and refusing a
  delete no longer replaces the asset's card with a line of raw text.

- A seed too large for the chosen model no longer fails the job. Models accept different seed
  ranges (MiniMax Music stops at 1,000,000; ACE-Step goes to about 2 billion), and a seed carried
  over from one to another is now brought into range. The same seed always maps to the same
  value, so repeating a job still repeats it. The form shows each model's range.

- **MiniMax Music without lyrics no longer fails.** That model insists on being told how the
  vocals are handled, so its form now has a Vocals choice: instrumental (the default, nothing more
  needed), your own lyrics, or lyrics the model writes. Only "your own lyrics" needs the lyrics
  box, and the form says so before anything is sent.

- A job the model provider refuses now shows the provider's own reason instead of "Additional
  information below" with nothing below it, and explains the common one in plain words: a provider
  that is still busy with an earlier job of yours.
- Stopping a video, music, speech or 3D job says what really happens: the app stops waiting, but
  the job may still finish and be charged, and some providers will not start another until it has.

- **Retry** on a failed video job re-queues it as a video; it used to be rebuilt as an image
  request.

- **Video models that turned requests down now work, or say up front why they cannot.** A full
  sweep of the video catalog found 31 rejected jobs. Kling 3 Standard, Kling 2.6 Pro, MiniMax H3
  Max/Fast, HappyHorse 1.1 and SkyReels V4 refuse pixel sizes once a first frame is attached and
  want a resolution preset instead; MiniMax Hailuo 02, 2.3 Fast and 01 Live only make 6- or
  10-second clips; Grok Imagine 1.5 takes either a first frame or reference images, and no last
  frame; Vidu Q2 Turbo and Wan 2.6 Flash insist on a first frame although their docs call it
  optional. VJHStudio now retries each of these the right way (free — RunWare only charges for clips
  it makes) and remembers the rule on the model, so the next job is built correctly from the start.
- Models that need an audio track (VEED Fabric, Creatify Aurora, OmniHuman), a reference video
  (P-Video-Animate, Kling 2.6 Standard) or an input video (Runway Aleph) are hidden from the
  Generate dropdown and refused before anything is billed, with a badge on the Models page saying
  what they need.
- **Harvest constraints** now also reads the "Parameter Dependencies" rules and the resolution
  presets from a model's docs page.
- **Harvest constraints** no longer gives up on the whole run when one model misbehaves. A model
  that accepts a probe (and so makes a small charge) is marked and never asked again, the rest of
  that provider is left to its docs page for the run, and every other model is still harvested; a
  balance that moves mid-run, or any other failure on one model, is recorded on that model. The
  closing message lists what happened and shows the balance before and after.

## 0.5.0 — 2026-09-23

### Added

- **Use VJHStudio from an AI agent (MCP).** Goose, Claude Code, Claude Desktop — anything that
  speaks the Model Context Protocol — can browse your models, write prompts, queue images and
  videos into your projects and read the results back. Settings gains an **Automation (MCP)** card
  with the switch, the token and ready-made copy-paste blocks for each agent, and the README has a
  "Use VJHStudio from an agent (MCP)" section; `docs/goose/vjhstudio-recipe.yaml` is a Goose recipe
  to start from.
- A **daily spend cap** (default $2) and a limit on how many jobs a day an agent may queue. A job
  that would go past either is refused before anything is sent, and a job whose price VJHStudio
  cannot work out in advance is refused too.
- Jobs an agent queued are marked **via agent** on the queue cards, in the Queue page's history and
  in an image or video's details.
- An access token for agents on your network, kept beside your API key in `data/secrets` and shown
  in full only when you ask for it. `VJHSTUDIO_MCP_TOKEN` overrides the stored one.

### Changed

- The three automation settings live in the Automation card with proper labels instead of appearing
  as raw keys in the General form.

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
- The Models page can sort each list by price (default) or by name, and the model dropdowns on the Generate page have a Cost / A–Z switch (the choice is remembered).
- A Queue page (`/queue`): the live queue plus a history table of the last 50 finished jobs; the header chip links there instead of the Generate form.
- Upload a first frame (or seed/reference image) straight from the Generate page; models that need a first frame say so in the References section.
- Video outputs get a real thumbnail (a frame from the clip) in the gallery, the lightbox, job cards and the home page; existing videos are filled in on the next start (`vjhstudio thumbs` does it on demand).
- Move an image or video to another project from its details view; the file, its sidecar and its cost follow.
- Dialogs (gallery lightbox, asset picker, save prompt) open as full-screen sheets on phones; a web app manifest and icons so the app can be added to a phone's home screen.

### Changed

- Idea chips are now different for images and videos (video gets camera moves, motion and grading
  terms instead of painting styles). Style is one choice at a time, and a video model that makes
  its own sound adds "ambient sound", "spoken dialogue" and "background music" to Extras.
- Generate is now a two-column page: the prompt builder on the left, a sticky "Model & settings"
  rail on the right, and results below both. Every page has a title and subtitle, and the current
  page is highlighted in the navigation. Action buttons are inline instead of full-width bars, and
  prompt rows are more compact. Navigation wraps instead of overflowing on narrow screens, and the top bar stays put while the page scrolls underneath it.
- Every number box (duration, width, height, steps, CFG, settings) gets big − / + buttons instead of the tiny native spinner.
- Menu clicks swap only the page body; the top bar with its balance and queue chips stays in place.

### Fixed

- The output details no longer cover the top bar: the lightbox opens below the navigation and
  leaves it usable (Esc, the close button and a click on the dimmed area all still close it),
  and the gallery behind it stays put instead of scrolling away under the panel.
- Opening a second output's details showed the first one again; each click now reloads the panel.
- Tablet and laptop widths (576–1400 px) had no side gutter: content sat flush against the viewport edge. Every page keeps a 16 px gutter now.
- The video Resolution dropdown gets two thirds of its row so the full size label ("720p portrait (9:16) — 704×1280") is readable.
- "Mark seen" on the queue panel did nothing visible; it is now "Clear finished" and hides the finished jobs from the panel (the Queue page keeps the full history).
- A closed gallery lightbox painted a dim wash over the page on a cold load.
- The balance chip's load-time refresh re-armed itself on every poll response (about three requests a second); the polled chip no longer carries the load trigger.
- Every page now renders the last known balance in the header immediately; before, only Home and Settings passed it to the header, so other pages showed "Balance unknown" until the next poll.
- The header balance chip refreshes right after every page load as well, so no page shows a stale or unknown balance for a minute.
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
