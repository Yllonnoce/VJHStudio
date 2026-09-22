# VJHStudio Phase 8 (Friendlier interface) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Generate page around writing (big text areas, idea chips, sticky settings rail, results below), turn the home page into a dashboard, and give every page the same header/button polish.

**Architecture:** Templates, CSS and the two vendored-free JS files change; routes only gain context (ideas JSON, dashboard data). No schema, request or service changes except one read-only dashboard helper. Idea chips are a pure text helper in `compose.js` (`vjhToggleIdea`) driven by Alpine on the existing `generateForm` component, so the posted form and `services/prompts.compose` are untouched.

**Tech Stack:** FastAPI + Jinja2, HTMX 2, Alpine 3, Pico CSS 2 (vendored), CSS custom properties from `themes.css`; pytest for routes; `node` (present at `~/.local/bin/node`) for the one JS helper test, skipped when absent; headless Chromium at `~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome` for screenshots.

**Spec:** `docs/superpowers/specs/2026-09-22-friendly-interface-design.md`

## Global Constraints

- `uv run --frozen pytest -q`, `uv run --frozen ruff check .`, `uv run --frozen ruff format --check .` clean before every commit; every commit message ends with the exact line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- No CDN, no new dependencies, no build step. Vendored htmx/Alpine/Pico untouched.
- Form field names, `x-model` bindings, posted values and every existing route stay as they are; existing tests must keep passing without weakening assertions (a test may change only when the markup it asserted on legitimately changed, and the new assertion must be at least as strong).
- Theme tokens (`--sp-*`) only; no hard-coded colours except transparent/overlays.
- Copy from the spec verbatim: hero cards "Create an image" / "Create a video"; empty state "Nothing generated yet. Create an image to get started."; builder hint "Fill in what you can; the app writes the prompt."; section titles "Describe", "Model & settings", "Results".
- Every page keeps working at 390 px wide (no horizontal scroll) and at 1440 px.
- Windows scripts: none touched.

---

### Task 1: Prompt builder rewrite — text areas and idea chips

**Files:**
- Create: `vjhstudio/data/prompt_ideas.json`, `tests/test_prompt_ideas.py`, `tests/test_compose_js.py`
- Modify: `vjhstudio/web/templates/generate/_prompt_builder.html`, `vjhstudio/web/static/js/compose.js`, `vjhstudio/web/static/js/app.js`, `vjhstudio/web/routes/generate.py` (page context gains `ideas`), `vjhstudio/web/static/css/app.css` (chip + textarea rules only)

**Interfaces (produces):**
- `vjhstudio/services/ideas.py`: `load_ideas() -> dict[str, list[str]]` (cached read of the JSON; keys exactly `style, mood, lighting, camera, composition, colour, extras`; every list 10–14 non-empty strings, no duplicates case-insensitively).
- `compose.js`: `vjhToggleIdea(text, phrase) -> string` and `vjhHasIdea(text, phrase) -> boolean` exported like `composePrompt` (both on `global` and the module object), plus `vjhAutosize(el)`.
- `generateForm`: methods `toggleIdea(field, phrase)`, `hasIdea(field, phrase)`; the `ideas` object is read from `<script id="prompt-ideas" type="application/json">`.

- [ ] **Step 1: Failing tests.** `tests/test_prompt_ideas.py`: `load_ideas()` returns the seven keys, 10–14 phrases each, all `str`, stripped, unique case-insensitively, none longer than 40 chars; `GET /generate/image` contains `id="prompt-ideas"`, a `<button type="button" class="idea"` for `golden hour` under lighting, `aria-pressed="false"`, and `<textarea name="subject"`, `<textarea name="extras"`, `<textarea name="negative"`; the composed-prompt "Copy" button is present. `tests/test_compose_js.py`: skip unless `shutil.which("node")`; run `node -e` loading `compose.js` and assert `vjhToggleIdea("", "golden hour") == "golden hour"`, `vjhToggleIdea("soft light", "golden hour") == "soft light, golden hour"`, `vjhToggleIdea("soft light, Golden Hour", "golden hour") == "soft light"`, `vjhHasIdea("a, b", "B") is True`, and that `composePrompt` still exists.
- [ ] **Step 2: Implement.** `prompt_ideas.json` content (write these exact lists):
  - style: oil painting, watercolour, film still, anime, 3D render, product photo, pencil sketch, pixel art, studio portrait, documentary photo, comic panel, isometric illustration
  - mood: serene, dramatic, playful, melancholic, tense, cosy, epic, mysterious, hopeful, eerie, nostalgic, energetic
  - lighting: golden hour, soft overcast, neon night, candlelit, harsh noon sun, rim light, studio softbox, moonlight, backlit silhouette, foggy morning, window light, volumetric rays
  - camera: 85mm portrait, wide angle, drone shot, macro close-up, low angle, over-the-shoulder, telephoto compression, fisheye, handheld, tripod long exposure, tilt-shift, slow dolly in
  - composition: rule of thirds, centred subject, leading lines, symmetry, negative space, frame within a frame, diagonal lines, close crop, wide establishing shot, layered foreground, reflection, birds-eye view
  - colour: muted earth tones, teal and orange, monochrome, pastel palette, high contrast, warm sepia, cool blues, neon pink and cyan, black and gold, desaturated, vivid saturated, autumn palette
  - extras: fine detail, sharp focus, 8k, film grain, shallow depth of field, motion blur, bokeh, matte finish, glossy, hand-drawn texture, cinematic, minimal

  `services/ideas.py`: `functools.lru_cache` loader from `importlib.resources`/`Path(__file__).parents[1] / "data" / "prompt_ideas.json"` (same pattern as the curated catalog loader — copy it). `routes/generate.py` `_page`: add `"ideas": ideas.load_ideas()` to the context and render `<script id="prompt-ideas" type="application/json">{{ ideas|tojson }}</script>` in `_prompt_builder.html`.

  Builder markup: Subject `<textarea name="subject" rows="3" x-model="fields.subject" placeholder="…" @input="vjhAutosize($el)">{{ form.get('subject','') }}</textarea>`; the six single-line fields full width each followed by `<div class="ideas" role="group" aria-label="Lighting ideas"><template x-for="p in ideas.lighting" :key="p"><button type="button" class="idea" :class="{active: hasIdea('lighting', p)}" :aria-pressed="hasIdea('lighting', p) ? 'true' : 'false'" @click="toggleIdea('lighting', p)" x-text="p"></button></template></div>` (render the buttons server-side too — a plain Jinja loop with `aria-pressed="false"` — so the tests and no-JS view see them; Alpine takes over the `active` state); Extras/Negative/Final as textareas. Section header: `<header class="card-head"><h3>Describe</h3><p class="hint">Fill in what you can; the app writes the prompt.</p></header>`. Copy button next to "Composed prompt": `<button type="button" class="secondary outline btn-sm" @click="copyComposed()">Copy</button>`.

  `compose.js`:
  ```js
  function _splitPhrases(text) { return String(text || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean); }
  function vjhHasIdea(text, phrase) { var p = String(phrase || '').trim().toLowerCase(); return _splitPhrases(text).some(function (t) { return t.toLowerCase() === p; }); }
  function vjhToggleIdea(text, phrase) {
    var p = String(phrase || '').trim(); if (!p) return String(text || '');
    var parts = _splitPhrases(text);
    var kept = parts.filter(function (t) { return t.toLowerCase() !== p.toLowerCase(); });
    if (kept.length !== parts.length) return kept.join(', ');
    return parts.concat([p]).join(', ');
  }
  function vjhAutosize(el) { if (!el || el.tagName !== 'TEXTAREA') return; el.style.height = 'auto'; var max = 12 * parseFloat(getComputedStyle(el).lineHeight || '20'); el.style.height = Math.min(el.scrollHeight, max) + 'px'; }
  ```
  `app.js` `generateForm`: `ideas: (function(){ try { return JSON.parse(document.getElementById('prompt-ideas').textContent); } catch (e) { return {}; } })()`, `toggleIdea(field, phrase) { this.fields[field] = window.vjhToggleIdea(this.fields[field], phrase); }`, `hasIdea(field, phrase) { return window.vjhHasIdea(this.fields[field], phrase); }`, `copyComposed() { try { navigator.clipboard.writeText(this.composed); } catch (e) {} }`; on `init` run `vjhAutosize` over every textarea in the form.

  CSS: `.ideas{display:flex;flex-wrap:wrap;gap:.35rem;margin:-.35rem 0 .9rem}` `.idea{width:auto;margin:0;padding:.15rem .6rem;font-size:.8rem;border-radius:1rem;background:var(--sp-surface-2);border:1px solid var(--sp-border);color:var(--sp-text)}` `.idea.active{background:var(--sp-accent);color:var(--sp-on-accent);border-color:var(--sp-accent)}` `textarea{field-sizing:content;min-height:2.5rem;resize:vertical}`.
- [ ] **Step 3: Run** the new tests + `tests/test_web_generate*.py` + full suite → PASS; ruff clean; smoke: serve on a spare port, `curl /generate/image | grep -c 'class="idea"'` ≥ 70.
- [ ] **Step 4: Commit** `feat(generate): text areas and idea chips in the prompt builder`.

---

### Task 2: Generate page layout — builder left, sticky rail right, results below

**Files:**
- Modify: `vjhstudio/web/templates/pages/generate.html`, `vjhstudio/web/templates/generate/_queue_panel.html` (wrapper only), `vjhstudio/web/static/css/app.css`, tests `tests/test_web_generate*.py` (add layout assertions)

**Interfaces (consumes):** Task 1's builder markup. Produces the CSS classes `.gen-layout`, `.gen-rail`, `.gen-results` used by the templates.

- [ ] **Step 1: Failing tests.** `GET /generate/video` contains `class="gen-layout"`, `<aside class="gen-rail"` containing the Generate button and the estimate, and `<section class="gen-results"` AFTER the closing of `.gen-layout` (assert `r.text.index('gen-results') > r.text.index('gen-rail')`); the queue panel id `queue-panel` still present exactly once; both mode tabs present; the page head `<header class="page-head">` with the subtitle "Describe what you want; VJHStudio writes the prompt.".
- [ ] **Step 2: Implement.** `generate.html`: `<header class="page-head"><h1>Generate</h1><p class="lead">Describe what you want; VJHStudio writes the prompt.</p></header>`, then `<div class="gen-layout">` containing the form with `display: contents` as now; the builder section (from Task 1) and `<aside class="gen-rail gen-card"><header class="card-head"><h3>Model &amp; settings</h3></header>…model, project, refs, params, estimate, submit…</aside>`; after the closing `</div>`: `<section class="gen-results">{% include "generate/_queue_panel.html" %}</section>`. CSS: `.gen-layout{display:grid;gap:1.25rem;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);align-items:start}` `.gen-rail{position:sticky;top:1rem;max-height:calc(100vh - 2rem);overflow-y:auto}` `.gen-results .queue-panel{position:static;max-height:none}` `.gen-results .queue-list{display:grid;gap:.85rem;grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}` (check the queue panel's list class name and use it) `@media (max-width:1100px){.gen-layout{grid-template-columns:1fr}.gen-rail{position:static;max-height:none}}`. Remove the old three-column `.gen-grid` rules that no longer apply (keep `.gen-card`, `.gen-modes`, `.gen-submit`).
- [ ] **Step 3: Run** full suite → PASS; ruff clean; screenshots of `/generate/video` at 1440 and 390 px (headless Chromium, `--window-size=1440,1000` and `--window-size=390,900`) saved under the task's report folder; check no horizontal scroll at 390 (`document.documentElement.scrollWidth <= 390` via `--dump-dom` is not available; eyeball the screenshot).
- [ ] **Step 4: Commit** `feat(generate): two-column layout with a sticky settings rail and results below`.

---

### Task 3: Home dashboard

**Files:**
- Create: `vjhstudio/web/templates/pages/index.html` (rewrite), `vjhstudio/web/templates/partials/_recent_strip.html`, `vjhstudio/services/dashboard.py`, `tests/test_web_home.py`
- Modify: `vjhstudio/web/routes/pages.py`, `vjhstudio/web/routes/gallery.py` (optional `?open=<id>`), `vjhstudio/web/static/css/app.css` (dashboard rules appended at the end)

**Interfaces (produces):** `dashboard.context(session, runner) -> dict` with keys `recent: list[Output]` (12 newest via `outputs.gallery(session, per_page=12)[0]`), `active_count: int` (`len(runner.active_ids())`), `spend_today: float` (`costs.today_spend`), `balance: BalanceInfo | None` (`account.cached_balance`), `outputs_total: int` (second element of `outputs.gallery`), `projects: list[dict]` (`projects.list_active` + `projects.totals(session, p.id)` → `{"id","name","outputs","cost"}`).

- [ ] **Step 1: Failing tests.** `GET /` with an empty DB: 200, contains "Create an image", "Create a video", `href="/generate/image"`, `href="/generate/video"`, "Nothing generated yet. Create an image to get started.", the stat chips "Spent today" and "Outputs", and NOT "arrive in the next phases". With three seeded outputs (use the fixtures/helpers `tests/test_web_gallery.py` uses): the strip shows three `gallery-card` thumbnails newest first and a "See all" link to `/gallery`; with an active job (use the job fixture from `tests/test_web_jobs.py`): the "In progress" section and `id="queue-panel"` are present; the projects section lists "Default" with its output count.
- [ ] **Step 2: Implement.** `services/dashboard.py` as above (no web imports). `routes/pages.index`: open one session, build the context, render. `index.html`: page head (title "VJHStudio", subtitle "v{{ app_version }}"), no-key banner, `<div class="hero-grid"><article class="hero-card"><h2>Create an image</h2><p>Photos, art and product shots from a description.</p><a role="button" href="/generate/image">Create an image</a></article><article class="hero-card"><h2>Create a video</h2><p>Short clips from text or from a first frame.</p><a role="button" href="/generate/video">Create a video</a></article></div>`, `<section class="dash-section"><header class="card-head"><h2>Recent</h2><a href="/gallery">See all</a></header>{% include "partials/_recent_strip.html" %}</section>` (the strip reuses `gallery/_card.html` for each output inside `<div class="recent-strip">`; each card links to `/gallery?open={{ o.id }}`; implement `?open=` in the gallery route by passing `open_id` to the template and, in `gallery.html`/`_grid.html`, an inline script that clicks the matching card's lightbox opener on load — if the lightbox opener is not trivially addressable, link to `/gallery` and say so in the report), "In progress" section (`{% if active_count %}` include `generate/_queue_panel.html`), stat chips row (`.stat-row` with `.stat` cards: "Balance" `${{ '%.2f'|format(balance.amount) }}` or "unknown", "Spent today", "Outputs"), projects table. CSS: `.hero-grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}` `.hero-card{padding:1.25rem}` `.hero-card [role=button]{width:100%;margin-top:.5rem}` `.recent-strip{display:grid;gap:.75rem;grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}` `.stat-row{display:grid;gap:.75rem;grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}` `.stat{padding:.75rem 1rem}` `.stat .value{font-size:1.4rem;font-weight:700}`.
- [ ] **Step 3: Run** full suite → PASS; ruff clean; screenshots of `/` at 1440 and 390 px with and without outputs.
- [ ] **Step 4: Commit** `feat(home): dashboard with create cards, recent outputs, queue and stats`.

---

### Task 4: Global polish — buttons, page heads, active nav, prompts rows

**Files:**
- Modify: `vjhstudio/web/static/css/app.css`, `vjhstudio/web/templates/_header.html`, `vjhstudio/web/templates/pages/{gallery,models,prompts,assets,projects,settings}.html`, `vjhstudio/web/templates/catalog/_refresh_status.html`, `catalog/_harvest_status.html`, `catalog/_row.html`, `prompts/_row.html` (or the prompt list partial's actual name), `vjhstudio/web/deps.py` (`render_globals` gains `current_path`), tests `tests/test_web_pages.py` (new or existing)

- [ ] **Step 1: Failing tests.** Every page listed renders `<header class="page-head">` with an `<h1>` and a `<p class="lead">`; `GET /models` marks the Models nav link `aria-current="page"` and no other link; `GET /` marks Home; the Models page has both action buttons inside one `<div class="actions">`; the prompts page no longer contains `class="block"` full-width "Save tags" (assert the button has class `btn-sm`); no template contains "arrive in the next phases".
- [ ] **Step 2: Implement.** `deps.render_globals`: `current_path = request.url.path`; `_header.html`: `{% set cur = current_path %}` and on each `<a>` `{% if cur == '/' and href == '/' or (href != '/' and cur.startswith(href)) %}aria-current="page"{% endif %}` (write it as a small Jinja macro `navlink(href, label)`); CSS: `header nav a[aria-current="page"]{color:var(--sp-accent);box-shadow:inset 0 -2px 0 var(--sp-accent)}`; buttons: `button,[role=button],input[type=submit]{width:auto}` then `.gen-submit,.hero-card [role=button],.block{width:100%}`; `.btn-sm{padding:.3rem .7rem;font-size:.85rem}`; `.page-head{margin:1.25rem 0 1rem}.page-head h1{margin:0 0 .25rem;font-size:1.9rem}.page-head .lead{margin:0;color:var(--sp-muted)}`; `.card-head{display:flex;justify-content:space-between;align-items:baseline;gap:.75rem;margin-bottom:.75rem}.card-head h2,.card-head h3{margin:0}`; `.actions{display:flex;gap:.75rem;flex-wrap:wrap;align-items:center}`; `main.container{max-width:1400px}`; type scale per spec. Models page: wrap the two status forms in `<div class="actions">` and put their `<small>` status lines beneath; star/hide buttons `btn-sm`. Prompts rows: compact header line, "Save tags" `btn-sm` inline with the tag input (`.tag-row{display:flex;gap:.5rem}`), actions in one `.actions` row.
- [ ] **Step 3: Run** full suite → PASS; ruff clean; screenshots of `/models`, `/prompts`, `/gallery`, `/settings` at 1440 and 390 px.
- [ ] **Step 4: Commit** `feat(ui): page heads, inline buttons, active navigation and compact prompt rows`.

---

### Task 5: Version 0.4.0, changelog, README

**Files:** `vjhstudio/__init__.py`, `CHANGELOG.md`, `README.md` ("Make your first image" mentions the idea chips and the home page cards), `tests/test_version.py`.

- [ ] Tests (`__version__ == "0.4.0"`, CHANGELOG `## 0.4.0`, README contains "idea" and "Create an image") → implement → full suite → commit `chore: version 0.4.0, changelog and README for the friendlier interface`.

---

## Exit criteria

- Generate: Subject/Extras/Negative/Final are text areas; every builder field except Subject has a chip row; clicking a chip toggles the phrase in the field and the composed preview updates; the settings rail stays visible while scrolling the builder; results appear under the two columns; 390 px has no horizontal scroll.
- Home: two create cards, the 12 most recent outputs, the queue when busy, stat chips, projects; no stale copy.
- Every page has a page head; the active nav link is highlighted; no full-width grey action bars remain on Models/Prompts.
- Suite green, ruff clean; screenshots attached to the task reports.
