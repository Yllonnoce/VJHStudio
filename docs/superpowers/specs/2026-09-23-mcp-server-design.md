# VJHStudio — MCP server design (Phase 9)

Date: 2026-09-23. Extends `2026-09-20-runwarestudio-design.md`. Approved in chat: VJHStudio exposes its capabilities over the Model Context Protocol (MCP) so an agent host (Goose, Claude Desktop, Claude Code, OpenCode, Open WebUI …) on this machine or another one on the LAN can drive the studio in plain language. The web app is unchanged for people who do not use it; automation is an optional entry point with a spend cap built into the tools.

## Goals

- One set of tools over the existing services. Every job an agent starts goes through the same queue, downloads, sidecars, posters, cost rows, projects and harvested constraints as a browser job.
- Remote control: the running app serves MCP over streamable HTTP at `/mcp`, protected by a bearer token, so a host on another machine reaches it at `http://<host>:8080/mcp`. The app already binds to the LAN for phones (README "On your phone or tablet").
- Local hosts that prefer a child process get `vjhstudio mcp` over stdio: the same tools, an in-process job runner, no web app needed.
- Money safety lives in the tools, not in the host: a daily cap on estimated + actual spend from agent jobs, a daily job count, both refusing before anything is queued.
- Everything an agent did is visible: jobs carry `source = "mcp"`, the Queue and Gallery show a "via agent" chip.

## Non-goals

- No deletion tools, no settings tools, no API-key tools in this version.
- No uploads from remote hosts (an agent can only reference assets that already exist; `list_assets` finds them). A follow-up may add `asset_from_output` and URL uploads.
- No OAuth. A static bearer token is enough for a single-user LAN app.
- No per-host permissions; one token, one cap.

## Dependency

`mcp>=2,<3` (official Python SDK, v2 API: `from mcp.server.mcpserver import MCPServer`; tests use `from mcp.client import Client` with the server object passed directly, which runs in memory). Already added to `pyproject.toml` / `uv.lock`.

## 1. Data and settings

- Migration `0006_mcp`: `jobs.source` VARCHAR(8) NOT NULL DEFAULT `'web'` (batch mode). `Job.source: Mapped[str]`.
- Settings (`services/settings.SPEC`): `mcp.enabled` (bool, False), `mcp.daily_cap_usd` (float, 2.0), `mcp.max_jobs_per_day` (int, 20). Env overrides `VJHSTUDIO_MCP_ENABLED`, `VJHSTUDIO_MCP_DAILY_CAP_USD`, `VJHSTUDIO_MCP_MAX_JOBS_PER_DAY` follow the existing `Spec.env` mechanism.
- Token stash (`secrets.py`): `read_mcp_token(paths)`, `write_mcp_token(paths, token)`, `rotate_mcp_token(paths) -> str` (32 url-safe bytes from `secrets.token_urlsafe(32)`), file `data/secrets/mcp_token`, same 0600 / owner-only handling as the API key, never logged, masked in the UI except on explicit reveal/copy. `VJHSTUDIO_MCP_TOKEN` env overrides the file (same precedence pattern as the API key).

## 2. Cap and tagging (`services/automation.py`)

```python
class CapExceeded(ValueError): ...
@dataclass
class CapStatus: cap_usd: float; spent_usd: float; jobs_today: int; max_jobs: int
def status(session) -> CapStatus            # spent = SUM over today's jobs with source='mcp' of COALESCE(cost, estimated cost stored in request_json["_estimate"])
def check(session, estimate_usd: float | None) -> None   # raises CapExceeded with a one-sentence reason
def tag(job: Job, estimate_usd: float | None) -> None    # job.source = "mcp"; request_json["_estimate"] = estimate
```

Rules: an unknown estimate (`None`, price unknown) counts as exceeding the cap unless the cap is 0 (0 = "no cap", documented). A job that finished with a real `cost` uses that instead of its estimate. Day = UTC date of `created_at`, the same day `usage_entries` uses.

`enqueue_image` / `enqueue_video` gain a keyword `source: str = "web"` and `estimate_usd: float | None = None`; when `source == "mcp"` they call `automation.check` then `automation.tag` inside the same session before the job row is flushed, so a refusal never leaves a row behind.

## 3. Tools (`vjhstudio/mcp/server.py`)

`build_server(ctx: MCPContext) -> MCPServer` where `MCPContext` holds `session_factory`, `paths`, `runner` (a `JobRunner`), `env`, and `setting(key)`. Tools are plain functions closing over `ctx`, registered with `@server.tool()`. Every tool returns JSON-serialisable dicts/lists (structured output) and raises `ValueError` with a user sentence on refusal; the SDK turns that into a tool error the model can read.

| Tool | Arguments | Returns |
|---|---|---|
| `list_models` | `kind: "image"\|"video"`, `sort: "price"\|"name" = "price"`, `favourites_only: bool = False` | `[{air, name, price_label, price_usd, unit, accepts, needs_first_frame, sizes_known}]`; only generate-capable, non-hidden rows (same list as the dropdowns) |
| `model_details` | `air` | `{air, name, kind, family, accepts, roles: {role: {required, max}}, sizes: [{w,h,label}] or rule, durations, resolutions, provider_settings, price_label}` |
| `estimate` | `kind`, `air`, `width?`, `height?`, `number_results?`, `duration?`, `resolution?`, `audio?` | `{estimate_usd, rate, note}` |
| `generate_image` | `air`, `prompt`, `project: str\|int = "default"`, `width=1024`, `height=1024`, `number_results=1`, `negative_prompt=""`, `seed?`, `seed_image_asset_id?`, `reference_asset_ids=[]`, `title?` | `{job_id, estimate_usd, cap: CapStatus}` |
| `generate_video` | `air`, `prompt`, `project`, `duration=5`, `resolution="720p"`, `width?`, `height?`, `first_frame_asset_id?`, `last_frame_asset_id?`, `reference_asset_ids=[]`, `provider_settings={}`, `title?` | same |
| `job_status` | `job_id` | `{id, status, progress, status_text, error, cost, outputs: [output summary]}` |
| `wait_for_job` | `job_id`, `timeout_s: int = 300` (clamped 1–900) | same as `job_status` once terminal, or the current state with `timed_out: true`; polls every 2 s via `asyncio.sleep`, reports progress through `ctx.report_progress` |
| `cancel_job` | `job_id` | `{cancelled: bool}` |
| `list_jobs` | `status?`, `limit=20` | list of job summaries, newest first |
| `list_outputs` | `project?`, `kind?`, `search?`, `limit=24` | `[{id, kind, project, model, created_at, cost, width, height, duration_s, url, path}]` using `outputs.gallery` |
| `output_details` | `output_id` | prompt, negative, params, seed, cost, `url` (`/files/outputs/<rel>`), absolute `path` |
| `move_output` | `output_id`, `project` | `{id, project}` via `outputs.move` |
| `list_projects` | — | `[{id, name, slug, outputs, cost}]` |
| `create_project` | `name`, `description?` | `{id, name, slug}` |
| `list_assets` | `kind?`, `search?`, `limit=50` | `[{id, name, kind, width, height, tags}]` |
| `account` | — | `{balance_usd, balance_age_s, spent_today_usd, cap: CapStatus}` |

Resources: `vjhstudio://projects` (the `list_projects` payload as JSON) and `vjhstudio://outputs/recent` (last 24 outputs). Prompt: `plan_shoot(subject)` returning a short instruction that tells the model to list models, estimate, ask before exceeding a dollar, and put results in a named project.

Rules inside the tools:
- `project` accepts an id, a slug or a name; unknown → `ValueError("No project named …; call list_projects or create_project.")`.
- Sizes snap the way the web form does (`constraints.nearest_size`) and the reply says when it snapped.
- The prompt builder is bypassed: `final_prompt` is the agent's prompt as given; `form.subject` is set to it so titles and the prompt library keep working. `negative_prompt` goes to `form.negative`; `use_default_negative` stays True for images.
- `url` fields are relative to the app root; the HTTP transport adds the absolute base from the request so a remote host can fetch files with the same token (files under `/files/` accept the bearer token as an alternative to being loopback — see §4).

## 4. Transports and auth

**HTTP (primary).** `web/app.py` mounts `server.streamable_http_app(streamable_http_path="/", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))` at `/mcp` when `mcp.enabled` is on at boot, wrapped in `BearerMiddleware`: every request to `/mcp*` must carry `Authorization: Bearer <token>` equal to the configured token (constant-time compare) or gets `401` with `WWW-Authenticate: Bearer`. The FastAPI lifespan enters `server.session_manager.run()` after the runner starts and leaves it before the runner stops. Toggling `mcp.enabled` or rotating the token takes effect on restart; the Settings page says so and offers the Restart button it already has.

`/files/outputs/*` and `/files/uploads/*` additionally accept the bearer token, so an agent on another machine can download what it made. Nothing else changes for browser users.

**stdio.** `vjhstudio mcp` boots (`boot.boot(paths)`), builds a `JobRunner` exactly as `create_app` does, `await runner.start(requeue=…)`, runs `server.run_stdio_async()`, and stops the runner on exit. All logging goes to stderr (stdout is the protocol channel). `mcp.enabled` is not required for stdio (the host launching the process is the consent).

## 5. Settings page, docs, visibility

- Settings → new "Automation (MCP)" card: enable checkbox, daily cap (USD, 0 = no cap), max jobs per day, token (masked, "Reveal", "Copy", "Regenerate"), the URL `http://<this host>:<port>/mcp`, and three copy-paste blocks: Goose (`type: streamable_http` extension YAML with `headers: {Authorization: "Bearer …"}`), Claude Code (`claude mcp add --transport http vjhstudio <url> --header "Authorization: Bearer …"`), and stdio (`cmd: <venv>/bin/vjhstudio`, `args: [mcp]`, Windows path variant). Saving posts to `POST /settings/automation`; regenerate posts to `POST /settings/automation/token`.
- README: section "Use VJHStudio from an agent" — what it is, the cap, enabling it, the Goose example, the stdio alternative, a warning that the token is a password.
- `docs/goose/vjhstudio-recipe.yaml`: a Goose recipe (title, description, instructions, extension block placeholder) for "product shots into a project".
- Queue page, queue panel cards and gallery detail show a small `via agent` chip when `job.source == "mcp"`.
- `/api/health` gains `"mcp": {"enabled": bool}`.

## 6. Testing

- `tests/test_automation.py`: cap arithmetic (estimate counted until cost known, cost replaces estimate, unknown estimate refused, cap 0 = unlimited, job count), tagging, refusal leaves no job row.
- `tests/test_mcp_tools.py`: in-memory `Client(server)`; `list_tools` names; `list_models` shape; `generate_image` with FakeRunware → job succeeds → `wait_for_job` returns outputs with url/path; `generate_video` needing a first frame refused with the sentence; cap refusal; project by name/slug/id; `move_output`; `account`.
- `tests/test_mcp_http.py`: app with `mcp.enabled`; no header → 401; wrong token → 401; right token → `initialize` succeeds via the SDK's streamable HTTP client over an httpx ASGI transport; `/files/outputs/…` with token from a non-loopback client → 200; disabled → `/mcp` 404.
- `tests/test_mcp_stdio.py`: `vjhstudio mcp` started as a subprocess with `VJHSTUDIO_DATA_DIR` pointing at a temp copy and `VJHSTUDIO_OFFLINE=1`; `Client(StdioServerParameters(...))` lists tools and calls `list_projects`.
- Settings/route tests for the Automation card, token regenerate, `via agent` chip, health field.
- Manual: the user attaches Goose on this machine with the HTTP block and runs one cheap image job.

## 7. Version

0.5.0, CHANGELOG entry "Automation: MCP server (HTTP with token, stdio), spend cap, via-agent tagging."
