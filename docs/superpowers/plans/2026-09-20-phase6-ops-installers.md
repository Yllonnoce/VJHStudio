# VJHStudio Phase 6 (Self-update, backups, installers) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VJHStudio can update itself from GitHub with a rollback-safe step log and a restart, can pack/restore/merge its whole state as a zip archive, and installs (and uninstalls) itself on Linux/macOS/Windows with two plain yes/no questions.

**Architecture:** `services/update.py` wraps `gitinfo.run_git` + `uv` subprocesses; `check_updates()` is read-only (fetch + rev-list) and its result is cached in `app_meta` by a background task (60 s after boot, then every 6 h) so the header can show an "Update available (N)" badge; `run_update(state)` executes the spec's ordered steps in a worker thread, appending `Step` rows to an `UpdateState` singleton the UI polls once a second, and rolls back code (`git reset --hard <old>` + re-sync) and data (restore the pre-update backup) on failure. `services/archive.py` writes/reads `vjhstudio-backup-*.zip` (manifest + sqlite snapshot + optional media, ZIP64, streamed): `restore_replace` swaps the whole data dir and restarts; `preview_merge`/`merge` do an additive union by natural keys with id remapping inside one transaction. Installers are short shell/batch scripts (no PowerShell), recording their choices in `data/install.json` so the uninstallers know what to undo.

**Tech Stack:** stdlib `zipfile`/`sqlite3`/`subprocess`/`threading`, existing Alembic + SQLAlchemy layers, Jinja2 + HTMX partials, bash + cmd scripts. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-20-runwarestudio-design.md` — "Self-update and restart" (incl. "Update notice (manual update, never automatic)"), "Backup, restore and merge", "Migrations, backups, versioning", "Installers and launchers" (incl. "Install choices", "Simplicity rule", "Uninstall", "macOS notes"), "UI design → Settings (Updates, Backups)", "Restarting page".

## Global Constraints

- Package `vjhstudio`; commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .` clean before every commit; `runware/` and `services/` never import `web/`; DB-only handlers are sync `def` (FastAPI threadpool); routes stay thin.
- **No PowerShell and no `.ps1` anywhere** — `tests/test_scripts.py` greps every shipped script; Windows scripts cannot be executed in this environment, so they are verified by static assertions only (content greps + `bash -n` for the `.sh` files).
- **No network in tests.** Git-touching tests run against a temp bare repo created in the test (`git init --bare` + a clone, `monkeypatch` the repo root); `uv`/`subprocess` calls into uv are monkeypatched; `check_updates` in web tests is monkeypatched.
- Git is always non-interactive: reuse `gitinfo.GIT_ENV` (`GIT_TERMINAL_PROMPT=0`, `GIT_SSH_COMMAND="ssh -oBatchMode=yes"`). Return codes are trusted (unlike ScenePlay's `ops/app_update.py`, which had a SIGCHLD reaper); output text is only used to *classify* a failure (auth needed vs. other).
- Restart uses exit code 75 via `services/restart.request_restart()` (unchanged); data dir is `<repo>/data`, git-ignored, so `data/install.json` never lands in git.
- The checkout is on an NTFS mount with `core.filemode=false`: every new `.sh` file needs `git update-index --chmod=+x <file>` before the commit, and its test asserts the executable bit only when `sys.platform != "win32"`.
- Never merge or export `settings`, `app_meta` or anything under `data/secrets/`. An archive contains no API key.
- Backups keep the existing naming: `backup.backup_db(paths, label)` → `data/backups/vjh-<stamp>-<label>.db`, rotated to 10 per label. Archives are `data/backups/vjhstudio-backup-<stamp>.zip`.

## File Structure

```
vjhstudio/services/update.py       check_updates ; UpdateState/Step ; run_update ; start_update ; check_and_store ; read_notice ; uv_bin ; git_auth_needed
vjhstudio/services/archive.py      create_archive ; manifest_of ; list_archives ; archive_path ; delete_archive ; import_archive(_file) ; restore_replace ; preview_merge ; merge
vjhstudio/services/migrate.py      + __main__ CLI (upgrade|current|head)
vjhstudio/services/gitinfo.py      + run_git(..., cwd=None)
vjhstudio/web/routes/system.py     + /hx/system/updates, /system/update/check, /system/update, /hx/system/update-log, /restarting,
                                     /hx/system/backups, /system/backup, /system/backups/{name}/{download,restore,preview-merge,merge}, import, DELETE
vjhstudio/web/templates/settings/_updates.html, _update_log.html, _backups.html, _merge_preview.html
vjhstudio/web/templates/pages/restarting.html ; partials/_update_badge.html
vjhstudio/web/static/js/app.js     + restartWatcher()
vjhstudio/main.py                  + update, backup, restore subcommands
install.sh, install.bat, uninstall.sh, uninstall.bat, CHANGELOG.md
tests/test_update.py, test_web_update.py, test_archive.py, test_merge.py, test_installers.py (+ test_scripts.py, test_cli.py, test_version.py edits)
```

---

### Task 1: Update service and `vjhstudio update` CLI

**Files:** Create `vjhstudio/services/update.py`, `tests/test_update.py`; Modify `vjhstudio/services/gitinfo.py` (optional `cwd`), `vjhstudio/services/migrate.py` (module `__main__`), `vjhstudio/main.py` (`update` subcommand), `tests/test_cli.py`.

**Interfaces (produces):**
- `gitinfo.run_git(args: list[str], timeout: int = 30, cwd: Path | None = None) -> subprocess.CompletedProcess[str]` (defaults to `REPO_ROOT`).
- `update.uv_bin() -> str` (`VJHSTUDIO_UV` → `shutil.which("uv")` → `"uv"`), `update.GIT_LOGIN_HELP: str`, `update.ZIP_INSTALL_HELP: str`.
- `update.git_auth_needed(text: str) -> bool` (matches "could not read username/password", "terminal prompts disabled", "authentication failed", "permission denied (publickey", "host key verification failed").
- `update.check_updates(repo: Path | None = None) -> dict` → `{"git": bool, "current": str, "behind": int, "commits": list[str], "error": str}`; `current` = `"<short> <subject>"`; `commits` = subjects of `HEAD..@{u}`, max 20; non-git checkout → `git=False` + `error=ZIP_INSTALL_HELP`; auth failure → `error=GIT_LOGIN_HELP`; any other failure → `"Could not check for updates: <stderr>"`.
- `@dataclass(frozen=True) update.Step: title: str; detail: str = ""; ok: bool = True; at: datetime`.
- `class update.UpdateState`: `__init__(self, on_step: Callable[[Step], None] | None = None)`; attrs `running: bool`, `ok: bool | None`, `steps: list[Step]`, `started_at`, `finished_at`, `message: str`; `add(title, detail="", ok=True) -> Step`; `snapshot() -> dict` (`{"running","ok","message","steps":[{"title","detail","ok"}]}`); thread-safe via an internal `threading.Lock`. Module singleton `update.STATE = UpdateState()`.
- `update.run_update(state: UpdateState, paths: Paths, repo: Path | None = None) -> bool` — the spec sequence, judged on return codes: git guard → `backup.backup_db(paths, "pre-update")` + `rotate` (failure ⇒ stop, nothing changed) → record `old_sha` (`git rev-parse HEAD`) → `git stash` when `git status --porcelain` is non-empty → `git pull --ff-only` (timeout 300) → `uv sync --frozen` (timeout 900; failure ⇒ `git reset --hard <old_sha>`, `uv sync --frozen`, `git stash pop`, return False) → `uv run --frozen python -m vjhstudio.services.migrate upgrade` as a subprocess (timeout 600; failure ⇒ reset + re-sync + copy the pre-update backup over `paths.db` after deleting `-wal`/`-shm`, `git stash pop`, return False) → `git stash pop` (conflict = warning step, not a failure) → POSIX only `chmod +x` on `*.sh` → `state.message = "Update complete — restarting VJHStudio."`, return True. Every step appends to `state`; auth failures append `GIT_LOGIN_HELP`.
- `update.start_update(paths: Paths, state: UpdateState | None = None) -> bool` — refuses (returns False) when `state.running`; otherwise resets the state and runs `run_update` in a daemon thread.
- `update.check_and_store(session_factory, repo: Path | None = None) -> dict` — runs `check_updates` and writes `app_meta` keys `update.behind`, `update.commits` (JSON list), `update.checked_at` (ISO), `update.error`.
- `@dataclass(frozen=True) update.Notice: behind: int; commits: list[str]; checked_at: str; error: str` + `update.read_notice(session) -> Notice`.
- `services/migrate.py`: `def main(argv: list[str] | None = None) -> int` handling `upgrade|current|head` against `config.resolve_paths().db`, plus `if __name__ == "__main__": sys.exit(main())`.
- `main.py`: `cmd_update(args)` — `--check` prints `"N commits behind"` + subjects and returns 0; otherwise streams steps through an `UpdateState(on_step=print_step)`, returns 0 on success / 1 on failure, and prints "Restart VJHStudio to run the new version."; registered as `vjhstudio update [--check]`.

**Tests (`tests/test_update.py`, `tests/test_cli.py`; write the full files):**
- Fixture `git_repo(tmp_path)`: `git init --bare origin.git`, clone it, commit a file, push; returns the clone path. All git tests pass `repo=<clone>`.
- `check_updates` on a clone with 2 new upstream commits → `behind == 2`, `commits` are the two subjects newest-first, `error == ""`, `current` starts with the local short sha.
- `check_updates` on a directory with no `.git` → `{"git": False, "behind": 0}` and `error == ZIP_INSTALL_HELP`.
- `check_updates` when `run_git` returns a fake "could not read Username" failure (monkeypatched) → `error == GIT_LOGIN_HELP`.
- `git_auth_needed` true for each of the five phrases, false for "fatal: not a git repository".
- `run_update` happy path against the temp repo with `uv` calls monkeypatched to success → returns True, the clone's HEAD equals origin's, steps contain "Safety backup", "Downloading update", "Installing packages", "Database migration", a `vjh-*-pre-update.db` exists, and `state.ok is True`.
- `run_update` with the uv-sync step monkeypatched to fail → returns False, HEAD is back at `old_sha`, re-sync was called a second time, last step `ok is False`, no restart requested.
- `run_update` with the migrate subprocess monkeypatched to fail → returns False, HEAD back at `old_sha`, `paths.db` bytes equal the pre-update backup bytes, `-wal`/`-shm` gone.
- `run_update` refuses and stops when `paths.db` is missing (backup step fails) → no git command ran (`run_git` spy empty after the guard).
- `run_update` restores local edits: dirty tracked file is stashed before the pull and present again after `stash pop`.
- `start_update` twice in a row → second call returns False and the step list is not reset; `snapshot()` is JSON-serialisable.
- `check_and_store` writes the four `app_meta` keys; `read_notice` round-trips them (`behind == 2`, `commits == [...]`).
- CLI: `main(["update", "--check"])` with `check_updates` monkeypatched prints "2 commits behind" and returns 0; `main(["update"])` with `run_update` monkeypatched to False returns 1.
- `python -m vjhstudio.services.migrate upgrade` equivalent: `migrate.main(["upgrade"])` on a temp `VJHSTUDIO_DATA_DIR` creates the db and returns 0; `migrate.main(["current"])` prints the head revision.

- [ ] Write tests → fail → implement → full suite green, ruff clean → commit `feat: self-update service with rollback and the update CLI`.

---

### Task 2: Updates UI, header badge, background check and the restarting page

**Files:** Create `vjhstudio/web/templates/settings/_updates.html`, `settings/_update_log.html`, `pages/restarting.html`, `partials/_update_badge.html`, `tests/test_web_update.py`; Modify `vjhstudio/web/routes/system.py`, `vjhstudio/web/app.py` (background check + `render_globals`), `vjhstudio/web/templates/_header.html`, `pages/settings.html`, `static/js/app.js`, `static/css/app.css`.

**Interfaces (produces):**
- Routes (all sync `def` except the restarting page's health poll, which is client-side):
  `GET /hx/system/updates` → `settings/_updates.html`; `POST /system/update/check` → runs `update.check_and_store` and re-renders the same partial; `POST /system/update` → `update.start_update(...)` then `settings/_update_log.html` (polling shell, `hx-get="/hx/system/update-log" hx-trigger="every 1s" hx-swap="outerHTML"`); 409 partial with "An update is already running." when refused; `GET /hx/system/update-log` → `_update_log.html`, and when the run has finished successfully it calls `restart_svc.request_restart()` once and returns the partial with header `HX-Redirect: /restarting`; `GET /restarting` → `pages/restarting.html` (standalone, no header polling), context `{"boot_id": app.state.boot.boot_id, "port": app.state.port}`.
- `app.state.render_globals()` gains `"update_behind": int` (read from `app_meta` in the session it already opens); `deps._DEFAULT_GLOBALS` gains `"update_behind": 0`; `partials/_update_badge.html` renders `<a href="/settings#updates">Update available (N)</a>` only when `update_behind > 0`, included in `_header.html` before the jobs badge.
- `web/app.py` lifespan: `_update_watch()` — `await asyncio.sleep(60)` then a loop of `await asyncio.to_thread(update.check_and_store, session_factory)` + `await asyncio.sleep(6 * 3600)`; started only when `app.state.auto_refresh` and `env.get("VJHSTUDIO_OFFLINE") != "1"` and `gitinfo.is_git_install()`; cancelled on shutdown next to the existing catalog task; every exception logged and swallowed.
- `app.js`: `window.restartWatcher(bootId)` — polls `/api/health` every 1 s; state machine `waiting → down → up`; navigates to `/` as soon as a health response carries a different `boot_id`; after 120 s swaps in the manual-start instructions block (`#restart-manual`).
- `pages/settings.html` gains `<section id="updates">` (includes `_updates.html`) and `<section id="backups">` (Task 3 partial), both above Maintenance.

**Tests (`tests/test_web_update.py`):**
- `GET /settings` contains "Updates", the current version, the short commit and a "Check for updates" button.
- `POST /system/update/check` with `update.check_and_store` monkeypatched to return `behind=3` → the partial lists the three commit subjects and shows "3 commits behind".
- Check with `error=GIT_LOGIN_HELP` → the help text is rendered and no Update button is shown.
- `POST /system/update` with `start_update` monkeypatched to True → 200, body contains `hx-trigger="every 1s"` and `/hx/system/update-log`; a second POST with `start_update` returning False → 409 and "already running".
- `GET /hx/system/update-log` mid-run (state with two steps, `running=True`) → both step titles present, no `HX-Redirect`; after `ok=True` → response header `HX-Redirect == "/restarting"` and `request_restart` (monkeypatched) called exactly once, including on a second poll.
- Finished-with-failure state → the log shows the failing step, no redirect, no restart call.
- Header badge: with `app_meta["update.behind"] = "2"` → `GET /` contains "Update available (2)"; with `"0"` → it does not.
- `GET /restarting` → 200, contains the current `boot_id` and `restartWatcher(`; `/api/health` already returns `boot_id` (asserted here too).
- Background watcher: `create_app(..., auto_refresh=False)` starts no update task (`asyncio.all_tasks` count unchanged over the lifespan) — keeps the suite offline.

- [ ] Tests → implement → green, ruff clean → commit `feat: Settings Updates section, update badge and restarting page`.

---

### Task 3: Archive create, download/import/delete and restore-replace

**Files:** Create `vjhstudio/services/archive.py`, `vjhstudio/web/templates/settings/_backups.html`, `tests/test_archive.py`; Modify `vjhstudio/web/routes/system.py`, `vjhstudio/main.py` (`backup`, `restore`), `vjhstudio/web/templates/pages/settings.html`, `tests/test_cli.py`.

**Interfaces (produces):**
- `archive.ARCHIVE_PREFIX = "vjhstudio-backup-"`, `archive.MANIFEST = "manifest.json"`, `class archive.ArchiveError(RuntimeError)`.
- `@dataclass(frozen=True) archive.ArchiveInfo: path: Path; name: str; created_at: datetime; size_bytes: int; includes: list[str]; counts: dict[str, int]; app_version: str; schema_revision: str`.
- `archive.create_archive(session_factory, paths, *, uploads: bool = False, outputs: bool = False) -> Path` — snapshot the db with the sqlite backup API into a temp file, then stream into `data/backups/vjhstudio-backup-<YYYYMMDD-HHMMSS>.zip` (`allowZip64=True`): `manifest.json` and `vjh.db` deflated, media `ZIP_STORED`; arcnames `uploads/<filename>`, `outputs/<rel_path>` (+ each sidecar), `thumbs/<name>`; outputs root honours the `paths.outputs_dir` setting via `projects.root_for`; manifest = `{"app_version", "schema_revision", "created_at", "host", "includes": [...], "counts": {table: n}}`.
- `archive.manifest_of(zip_path) -> dict` (raises `ArchiveError` when the file is not a zip, has no manifest, or has no `vjh.db`), `archive.list_archives(paths) -> list[ArchiveInfo]` (newest first, unreadable zips skipped), `archive.archive_path(paths, name) -> Path` (rejects any name with a separator or not matching `vjhstudio-backup-*.zip` → `ArchiveError`), `archive.delete_archive(paths, name) -> bool`, `archive.import_archive(paths, filename, content: bytes) -> Path` (validates the manifest, writes with a unique name).
- `@dataclass(frozen=True) archive.RestoreResult: safety_backup: Path; counts: dict[str, int]; missing_outputs: int; missing_assets: int`.
- `archive.restore_replace(paths, zip_path, *, engine=None, jobs_running: int = 0) -> RestoreResult` — raises `ArchiveError("Stop the running jobs first…")` when `jobs_running`; `backup.backup_db(paths, "pre-restore")` + rotate; `engine.dispose()` when given; delete `vjh.db-wal`/`-shm`; extract `vjh.db` (to `.part` then `os.replace`); extract media with a containment guard (any member escaping the data dir → `ArchiveError`); `migrate.upgrade(paths.db)`; reopen a short-lived engine and flag `outputs.is_missing` / delete nothing for assets whose files are absent (assets get `is_missing`-equivalent handling by counting only), returning the counts.
- Routes: `GET /hx/system/backups` → `_backups.html` (create form with `uploads`/`outputs` checkboxes + the archive table); `POST /system/backup`; `GET /system/backups/{name}/download` (FileResponse, `Content-Disposition: attachment`); `POST /system/backups/import` (multipart `file`); `DELETE /system/backups/{name}`; `POST /system/backups/{name}/restore` → runs the restore with `jobs_running=len(app.state.runner.active_ids())`, then `request_restart()` and `HX-Redirect: /restarting`; `ArchiveError` → 422 partial with the message.
- CLI: `vjhstudio backup [--uploads] [--outputs]` prints the archive path and size; `vjhstudio restore <zip> [--merge] [--yes]` — without `--yes` prints what will happen and asks `Type yes to continue:`; replace mode prints the safety backup path; returns 0/1.

**Tests (`tests/test_archive.py`, `tests/test_cli.py`):**
- Create with `uploads=False, outputs=False` → the zip holds exactly `manifest.json` + `vjh.db`; manifest `includes == ["db"]`, `counts["projects"] == 1`, `app_version == __version__`, `schema_revision == migrate.head()`.
- Create with both flags on a fixture with 1 asset file, 1 output + sidecar → members include `uploads/<name>`, `outputs/default/<file>`, `outputs/default/<file>.json`; the media entries use `ZIP_STORED` and `vjh.db` uses `ZIP_DEFLATED`; `zipfile.ZipFile(...).testzip() is None`.
- `manifest_of` on a random zip and on a text file → `ArchiveError`; `archive_path(paths, "../etc/passwd")` and `"x.zip"` → `ArchiveError`.
- `list_archives` sorts newest first and skips a corrupt zip without raising.
- Round trip: create with everything → wipe the data dir (db + files) → `restore_replace` → every project/output/asset row is back, the output file exists on disk, `is_missing` is False, and a `vjh-*-pre-restore.db` exists.
- db-only archive restored over an empty outputs dir → `missing_outputs == 1` and the output row has `is_missing True`.
- `restore_replace(..., jobs_running=1)` → `ArchiveError` and the db file is untouched (mtime/bytes unchanged).
- A zip containing a member named `../../evil.txt` → `ArchiveError`, nothing written outside the data dir.
- Settings page: `GET /settings` contains "Backups"; `POST /system/backup` with both checkboxes creates a file and the table lists it with its size; download returns `application/zip` with the attachment header; import of a non-archive → 422 "not a VJHStudio backup"; DELETE removes it.
- Restore route with the runner idle and `request_restart` monkeypatched → `HX-Redirect: /restarting`, restart called once.
- CLI: `main(["backup", "--uploads"])` on a temp data dir prints a path that exists; `main(["restore", str(zip), "--yes"])` returns 0 and restores; without `--yes` and stdin "no" → returns 1 and nothing changed.

- [ ] Tests → implement → green, ruff clean → commit `feat: backup archives with restore-replace, download and import`.

---

### Task 4: Additive merge with dry-run preview

**Files:** Create `vjhstudio/web/templates/settings/_merge_preview.html`, `tests/test_merge.py`; Modify `vjhstudio/services/archive.py`, `vjhstudio/web/routes/system.py`, `vjhstudio/main.py` (`restore --merge`).

**Interfaces (produces):**
- The merge keys prompts by the stored `prompts.content_hash` column (`_prompt_key` reads it, falling back to `prompts.hash_of(row)` for an archive written before the column existed), so the merge and the app's own dedupe can never drift apart.
- `@dataclass(frozen=True) archive.TableCounts: new: int = 0; existing: int = 0; missing_files: int = 0`.
- `@dataclass(frozen=True) archive.MergeReport: counts: dict[str, TableCounts]; errors: list[str]; app_version: str; schema_revision: str; created_at: str; dry_run: bool` + `total_new` property.
- `archive.preview_merge(session_factory, paths, zip_path) -> MergeReport` (`dry_run=True`, writes nothing anywhere) and `archive.merge(session_factory, paths, zip_path) -> MergeReport` (safety backup `pre-merge` first; one `db.session_scope` transaction; file copies after the flush).
- Internal `_incoming(zip_path)` context manager: extract `vjh.db` to a temp dir, `migrate.upgrade(temp_db)` (the archive may be older), yield a read session over it plus the open `ZipFile`; always cleaned up.
- Union rules, in dependency order, exactly as the spec table: `projects` by `slug` (new → insert + mkdir `outputs/<slug>`), `catalog_models` by `air`, `assets` by `sha256` (new → insert + copy `uploads/<filename>`, absent file counts `missing_files`), `prompts` by the stored `content_hash` (falling back to `prompts.hash_of`; project id remapped), `jobs` by `id` (existing → skip; new → insert, `queued`/`running` rewritten to `failed` with `error_code="orphaned"`), `outputs` by `(project slug, filename)` (new → insert with remapped `project_id`/`job_id`, copy file + sidecar, else `is_missing=True` + `missing_files`), `usage_entries` only for job ids inserted by this merge. `settings`, `app_meta` and anything under `secrets/` are never read from the archive. Outputs whose job id is unknown after remapping are skipped and recorded in `errors`.
- Routes: `POST /system/backups/{name}/preview-merge` → `settings/_merge_preview.html` (per-table new/existing/missing table + "Merge now" button posting to the merge route + Cancel that re-renders `_backups.html`); `POST /system/backups/{name}/merge` → `_backups.html` with a summary message ("Merged: 3 projects, 12 outputs, 2 files missing") and `HX-Trigger: jobs-changed`.
- CLI: `vjhstudio restore <zip> --merge [--yes]` prints the preview table, asks for confirmation unless `--yes`, then merges and prints the summary.

**Tests (`tests/test_merge.py`):**
- Fixture: build archive A from a data dir with projects `default` + `alpha`, 2 prompts, 1 asset, 1 job + 1 output + 1 usage row, uploads and outputs included; then a second, empty-ish data dir to merge into.
- `preview_merge` into an empty install → `counts["projects"].new == 2`, `counts["outputs"].new == 1`, `errors == []`, and **nothing** changed: row counts before/after identical, no `pre-merge` backup file.
- `merge` into an empty install → the preview's `new` numbers equal the rows actually inserted; the output file and its sidecar exist under `outputs/<slug>/`; the usage row is present; a `vjh-*-pre-merge.db` exists.
- Merging the same archive twice → the second report has `new == 0` and `existing` equal to the first `new` for every table; total row counts unchanged (idempotent).
- Project slug collision with different content → local project kept (name unchanged), incoming outputs land in the local project (`project_id` remapped to the existing row).
- Asset present locally by `sha256` under a different `original_name` → `existing`, file not overwritten (bytes unchanged).
- Prompt with identical kind/final/negative/form but a different title → `existing`; changing one form field → `new`.
- Job id collision → skipped; a `queued` job in the archive is inserted as `failed`/`orphaned`; its usage rows are not imported when the job was skipped.
- Output `(slug, filename)` collision → `existing` and the local file is not touched; a new output whose file is missing from a db-only archive → inserted with `is_missing True` and `missing_files == 1`.
- The archive's `settings`/`app_meta` never leak: set `ui.theme` differently in both installs → after the merge the local value stands; `app_meta["schema_revision"]` unchanged.
- Routes: preview returns a table with the per-table numbers and a "Merge now" button; merge returns the summary text; a merge with `ArchiveError` → 422 with the message.
- CLI: `main(["restore", str(zip), "--merge", "--yes"])` returns 0 and prints the summary.

- [ ] Tests → implement → green, ruff clean → commit `feat: additive archive merge with dry-run preview`.

---

### Task 5: Installers, uninstallers and the service/desktop choices

**Files:** Create `install.sh`, `install.bat`, `uninstall.sh`, `uninstall.bat`, `tests/test_installers.py`; Modify `tests/test_scripts.py` (the no-PowerShell sweep covers the four new scripts), `.gitignore` (nothing new — `data/` already covers `data/install.json`).

**Interfaces (produces):**
- `install.sh` — `main() { … }; main "$@"` wrapper, `set -u`, `${HOME:-}` everywhere, flags `--dir <path>` (default `${VJHSTUDIO_HOME:-$HOME/VJHStudio}`), `--branch <name>` (default `main`), `--no-start`, `--service`/`--no-service`, `--desktop`/`--no-desktop`. Steps, each with a plain-English comment: (1) ensure git — macOS `xcode-select --install`, Linux `apt`/`dnf`/`pacman`/`zypper` via sudo; (2) ensure uv — `curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path`; (3) `git clone` or `git -C <dir> pull --ff-only`; (4) `uv sync --frozen`; (5) `uv run --frozen python run.py migrate`; (6) ask the two questions with `read -r -p` (defaults "n"; skipped when a flag was given or stdin is not a tty); (7) service = Linux `~/.config/systemd/user/vjhstudio.service` (`ExecStart=<dir>/start.sh --no-browser`, `Restart=always`) + `systemctl --user daemon-reload` + `enable --now` (+ a printed `loginctl enable-linger` hint), macOS `~/Library/LaunchAgents/com.yllonnoce.vjhstudio.plist` + `launchctl load -w`; (8) desktop link = Linux `~/.local/share/applications/vjhstudio.desktop` (+ a copy on `~/Desktop`, `chmod +x`), macOS `~/Desktop/VJHStudio.command`; (9) write `data/install.json` = `{"version":1,"home":"<dir>","os":"linux|macos","service":true|false,"desktop":true|false,"installed_at":"<iso>"}`; (10) `exec ./start.sh` unless `--no-start` or the service was enabled — then print `http://127.0.0.1:8080/`.
- `install.bat` — cmd only, `curl.exe`/`tar.exe`/`winget` (+ `cscript` for the shortcut); git via `winget install --id Git.Git` else portable MinGit into `%LOCALAPPDATA%\Programs\MinGit`; uv via `winget install --id astral-sh.uv` else the msvc zip (ARM64 variant when `%PROCESSOR_ARCHITECTURE%`==ARM64); clone/pull into `%VJHSTUDIO_HOME%` (default `%USERPROFILE%\VJHStudio`); `uv sync --frozen`; migrate; the same two questions with `set /p`; service = `schtasks /Create /SC ONLOGON /TN VJHStudio /TR "\"<dir>\start.bat\" --no-browser" /F`; desktop link = a generated `%TEMP%\vjh_shortcut.vbs` run with `cscript //nologo` (`WScript.Shell.CreateShortcut`, icon `vjhstudio\web\static\img\icon.ico`, window style 7) with a `.url` file fallback when `cscript` is missing; write `data\install.json` with `echo` lines; `start "" start.bat` unless `/nostart`. Runs from `%TEMP%`, never from the checkout.
- `uninstall.sh` / `uninstall.bat` — stop the app first (`curl -s -X POST http://127.0.0.1:<port>/api/shutdown`, wait up to 10 s), read `data/install.json` (`grep`/`findstr` for `"service": true` / `"desktop": true`), remove the service (`systemctl --user disable --now vjhstudio` + delete the unit / `launchctl unload` + delete the plist / `schtasks /Delete /TN VJHStudio /F`), remove the desktop link (`.desktop`, `.command`, `.lnk`/`.url` on Desktop **and** OneDrive Desktop), remove `.venv`, remove `data/install.json`, keep `data/` unless `--purge` / `/purge`, which asks the user to type `DELETE` before removing it; finish by printing that the checkout folder, uv and git were left in place.

**Tests (`tests/test_installers.py`, static + syntax only — no execution, no network):**
- All four scripts exist; combined with `start.sh`/`start.bat`/`scripts/restart_helper.bat` none contains `powershell` or `.ps1` (case-insensitive).
- `bash -n install.sh` and `bash -n uninstall.sh` exit 0 (`subprocess.run`, skipped when `bash` is absent).
- `install.sh`: contains `main "$@"`, `set -u`, no bare `$HOME/` (only `${HOME:-}`), `uv sync --frozen`, `astral.sh/uv`, `--ff-only`, `systemctl --user`, `launchctl`, `install.json`, and exactly the two question strings ("automatically when you log in" and "desktop link"), each appearing once.
- `install.sh` and `uninstall.sh` are executable (`stat.S_IXUSR`, asserted only off Windows) — set with `git update-index --chmod=+x`.
- `install.bat`: contains `schtasks /Create /SC ONLOGON`, `/TN VJHStudio`, `curl.exe`, `tar.exe`, `cscript`, `.vbs`, `.url`, `uv sync --frozen`, `install.json`; contains `@echo off` and no `%~dp0` execution from the checkout (it copies to `%TEMP%`).
- `uninstall.sh`/`uninstall.bat`: reference `/api/shutdown`, `install.json`, `.venv`, `--purge`/`/purge` and the literal `DELETE`; `systemctl --user disable`/`schtasks /Delete` present; neither contains `rm -rf "$HOME"` or an unguarded `rmdir /s /q %USERPROFILE%`.
- `install.json` shape is documented in both installers: each contains the keys `"home"`, `"service"`, `"desktop"`, `"os"`.

- [ ] Write tests → fail → write the scripts → `git update-index --chmod=+x install.sh uninstall.sh` → green, ruff clean → commit `feat: install and uninstall scripts with optional autostart and desktop link`.

---

### Task 6: README install section, version 0.2.0 and CHANGELOG

**Files:** Create `CHANGELOG.md`; Modify `README.md`, `vjhstudio/__init__.py`, `tests/test_version.py`, `tests/test_scripts.py` (README assertions), `vjhstudio/main.py` (`doctor` prints the install mode).

**Interfaces (produces):**
- `vjhstudio.__version__ = "0.2.0"`.
- `CHANGELOG.md` — "Keep a Changelog" style: `## 0.2.0 — 2026-09-20` with Added (self-update with rollback and update badge, backup archives with restore and merge, installers/uninstallers with autostart and desktop link, `update`/`backup`/`restore` CLI commands) and `## 0.1.0` for the phases 1-5 baseline.
- `README.md` — a rewritten "Install" section following the simplicity rule: 3-5 numbered steps per OS ending in one installer line (`curl -fsSL …/install.sh | bash` on Linux/macOS, `curl.exe -fsSLo %TEMP%\install.bat …/install.bat && %TEMP%\install.bat` on Windows), then a short paragraph in plain language: "The installer asks two questions: whether to start VJHStudio automatically when you log in, and whether to put a link on your desktop. Answering no to both is fine — you can always start it with start.sh / start.bat." Plus new sections "Updating" (Settings → Updates → Check → Update now; the app restarts itself; the manual `git pull` alternative), "Backups" (Settings → Backups; what each checkbox includes; restore replaces everything, merge only adds), "Uninstall" (`./uninstall.sh`, `uninstall.bat`, `--purge` types DELETE) — the existing macOS notes block (127.0.0.1, Local Network, Gatekeeper) is kept verbatim.
- `doctor` gains one line: `install     : service=yes/no desktop=yes/no (data/install.json)` or `not installed by the installer`.

**Tests:**
- `tests/test_version.py`: `__version__ == "0.2.0"`, `/api/health` reports it, the footer renders it, and `CHANGELOG.md` has a `## 0.2.0` heading whose section mentions "update", "backup" and "install".
- `tests/test_scripts.py`: README contains `install.sh`, `install.bat`, `uninstall`, both question sentences in plain language, the three macOS note lines, and `http://127.0.0.1:8080` (never a bare `localhost:8080`).
- `tests/test_cli.py`: `doctor` output contains `install` and does not crash when `data/install.json` is missing; with a hand-written `install.json` it prints `service=yes`.

- [ ] Tests → implement → green, ruff clean → commit `chore: version 0.2.0, changelog and installer documentation`.

---

## Phase 6 exit criteria

- Push a commit to `main`: the header shows "Update available (1)" within 6 h (or immediately after Check), Settings → Updates lists the subject, **Update now** streams the step log, the app restarts and `/api/health` reports the new commit; `data/backups/` holds a `pre-update` backup.
- A forced failure in the uv-sync or migrate step leaves the checkout at the old commit, the database byte-identical to the pre-update backup, and the running app untouched.
- Settings → Backups creates an archive with uploads and outputs, downloads it, restores it onto a wiped data dir (files and rows back, app restarts), and merges a second archive: the preview counts equal the rows actually inserted, a repeated merge inserts nothing, and no setting or API key crosses over.
- `vjhstudio update --check`, `vjhstudio backup --uploads --outputs` and `vjhstudio restore <zip> --merge --yes` all work from the terminal.
- A fresh `install.sh` run answers both questions, enables the systemd user service and the desktop link, records them in `data/install.json`, and `uninstall.sh` removes exactly those plus `.venv` while keeping `data/`; `--purge` removes `data/` only after `DELETE` is typed.
- No `.ps1` or `powershell` anywhere; suite green and ruff clean on Linux.
