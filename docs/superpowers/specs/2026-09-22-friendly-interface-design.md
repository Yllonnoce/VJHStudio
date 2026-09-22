# VJHStudio — Friendlier interface (Phase 8) design

Date: 2026-09-22. Extends `2026-09-20-runwarestudio-design.md` ("UI design"). Approved in chat: the Generate page is rebuilt around writing, the home page becomes a dashboard (option 1), idea chips **add** to a field rather than replace it, the rest of the app gets a light polish. The theme system, Pico, HTMX and Alpine stay; no new dependencies, no build step.

## Problems observed (screenshots 2026-09-22)

- Subject and Extras are single-line inputs, too small for real descriptions; six fields are squeezed into pairs.
- No help with ideas: a user who does not know what "lighting" could be gets an empty box.
- The home page is a Phase 1 placeholder ("Generate an image" button, "Gallery, Prompts and Assets arrive in the next phases").
- Stock Pico look: full-width grey action buttons (Refresh prices, Harvest constraints, Save tags), no page subtitles, no active state in the navigation, cards without hierarchy.

## 1. Generate page

**Layout (≥ 1100 px).** Two columns: the prompt builder on the left (`minmax(0, 1.6fr)`), a right rail (`minmax(0, 1fr)`) with, in order, Model, Project, References, Parameters, the estimate and the Generate button. The rail is `position: sticky; top: 1rem` so the button and price never scroll away. The queue/results panel moves **below** both columns as a full-width "Results" section (the existing `_queue_panel.html`, cards in a responsive grid, still polling `/hx/jobs/active`). Below 1100 px everything stacks in one column: builder, rail, results.

**Fields.** Order and widths: Subject (textarea, 3 rows, full width) → Style → Mood → Lighting → Camera → Composition → Colour (each a full-width single-line input with an idea row beneath) → Extras (textarea, 2 rows) → Negative (textarea, 2 rows, image mode only) → the two checkboxes → Composed prompt preview → Final prompt (textarea, 4 rows) → Polish → Save prompt. Text areas grow with their content: CSS `field-sizing: content` where supported plus a small JS autosize fallback (`vjhAutosize(el)` on `input`, capped at 12 rows). Names, `x-model` bindings and posted values do not change, so `services/prompts.compose` and every existing test keep working.

**Idea chips.** `vjhstudio/data/prompt_ideas.json`: `{"style": [...], "mood": [...], "lighting": [...], "camera": [...], "composition": [...], "colour": [...], "extras": [...]}`, 10–14 short phrases each, shipped with the app and passed to the template as `ideas`. Under each of those fields a row of `<button type="button" class="idea" :class="{active: hasIdea(field, phrase)}" @click="toggleIdea(field, phrase)">phrase</button>`. `toggleIdea` (Alpine, in `app.js`, pure helper `vjhToggleIdea(text, phrase) -> text` in `compose.js` for tests): split the field on commas, trim, compare case-insensitively; if present remove it, else append with `", "`; the field keeps user-typed phrases untouched. Subject has no chip row (it is the user's own idea) but one example placeholder. The composed preview updates live as before. Keyboard: chips are buttons, so Tab/Enter work; `aria-pressed` mirrors the active state.

**Section headers.** The builder card is titled "Describe" with a one-line hint ("Fill in what you can; the app writes the prompt."); the rail card is "Model & settings"; references keep their heading; results "Results". The composed prompt block gets a Copy button (`navigator.clipboard`, falls back silently).

## 2. Home dashboard (`/`)

Context built in `routes/pages.index` from existing services, one session: `recent = outputs.gallery(session, limit=12)` (newest first, any project), `active = jobs.active_ids()` count + the `_queue_panel` partial for live progress, `spend_today = costs.today_spend(session)`, `balance` from the cached `account.balance` in `app_meta` (the header chip's source), `projects = projects.list_active(session)` with `projects.totals` per row (outputs count, cost), `has_api_key`.

Layout: page title "VJHStudio" with the version subtitle; the no-key banner when needed; two hero cards side by side, "Create an image" → `/generate/image` and "Create a video" → `/generate/video`, each with a one-line description and a large button; a "Recent" strip (the 12 latest outputs as thumbnails using the gallery card markup, each linking to `/gallery?open=<id>` — a new query parameter that opens the lightbox on that output, or to `/gallery` if that costs more than a few lines) with a "See all" link; "In progress" (the queue panel, hidden when nothing is queued); a row of three stat chips: balance, spent today, outputs total; "Projects" (name, outputs, cost, link to `/projects`). Empty states: "Nothing generated yet. Create an image to get started." with the hero cards above it.

## 3. Global polish

- Buttons: Pico's block-level default is overridden so `button, [role=button]` are inline-width by default (`width: auto`); only `.gen-submit`, `.hero-card a[role=button]` and explicit `.block` stay full width. Models page: "Refresh prices" and "Harvest constraints" sit side by side with their status text beneath; the model rows' star/hide buttons become compact icon buttons.
- Page header pattern: `<header class="page-head"><h1>…</h1><p class="lead">…</p></header>` on every page (Generate: "Describe what you want; VJHStudio writes the prompt."; Gallery: "Everything you have made, newest first."; Models: "Sorted by price, most expensive first."; Prompts, Assets, Projects, Settings likewise).
- Navigation: the current page's link is marked `aria-current="page"` and styled with the accent underline; the logo links home.
- Cards: consistent `article > header` for titles; the prompt library rows use a compact header line (title, kind chip, project, favourite) and keep the actions in one row of inline buttons instead of a full-width "Save tags" bar.
- Spacing and type: page max width 1400 px; `h1` 1.9 rem, `h2` 1.35 rem, `h3` 1.1 rem; inputs 0.95 rem; a 4-px baseline (`.25rem` multiples).
- Stale copy removed: the home placeholder text.

## 4. Out of scope

A sidebar navigation, a new colour system, dark-only default, and any change to routes, request schemas or the job runner. The gallery lightbox, assets page and settings forms keep their behaviour; they only receive the header/button polish.

## 5. Verification

- Unit/route tests: chips helper (`vjhToggleIdea` mirrored in Python? no — JS only, tested through the rendered markup and a Node-free assertion on `compose.js` string? The Python suite renders the page and asserts the chip buttons and `ideas` JSON are present; the pure helper gets a tiny test via `tests/test_compose_js.py` running `node` if available, else skipped), `/` dashboard renders recent outputs, hero links, stat chips, empty state; `aria-current` on the active nav link; Generate page renders textareas for Subject/Extras/Negative/Final; the results section below the grid; existing generate/route tests unchanged.
- Visual check: headless Chromium screenshots of `/`, `/generate/video`, `/gallery`, `/models`, `/prompts` at 1440 and 390 px width, attached to the task reports.
- Version 0.4.0, CHANGELOG entry, README "Make your first image" steps updated (chips).
