"""Self-update: git pull + uv sync + migrations, with rollback and a step log.

The sequence is deliberately boring and always recoverable:

    safety backup -> stash local edits -> git pull --ff-only -> uv sync
    -> migrate (subprocess, new packages) -> stash pop -> chmod scripts

A failure after the pull rolls the code back (`git reset --hard <old sha>` +
re-sync) and, when the migration is the step that failed, puts the pre-update
database back as well, so the running install is never left half-updated.

Return codes are trusted here: nothing in this app reaps child processes
(unlike ScenePlay's ops/app_update.py). Output text is only read to *classify*
a git failure as "this machine is not logged in" versus anything else.

`run_update` itself never restarts: a test — or a background check — must never
be able to kill the process. The background worker started by `start_update`
does restart after a successful run (sharing one claim with the web log poll, so
it happens exactly once); the CLI calls `run_update` directly and only prints a
hint.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..config import REPO_ROOT, Paths
from ..models import utcnow
from . import backup, gitinfo, meta, restart

log = logging.getLogger(__name__)

GIT_LOGIN_HELP = (
    "Update server login required — this computer is not signed in to the code "
    "server, so it cannot download the new version. Open a terminal in the "
    'VJHStudio folder and run "git pull" once to sign in (or set up an SSH key '
    "or credential helper), then run the update again."
)
ZIP_INSTALL_HELP = (
    "This copy was installed from a ZIP (no .git folder), so it cannot update "
    "itself. Download the latest ZIP and run the installer again — your data "
    "folder is kept."
)

AUTH_MARKERS = (
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
    "authentication failed",
    "permission denied (publickey",
    "host key verification failed",
)

RESTORE_STEP = "Database restored from the safety backup"
RESTART_STEP = "Restarting to load the restored database"

# https://user:token@host (and https://token@host) -> https://***@host ; git echoes
# the remote URL back on a failure, and a token is often the whole userinfo part.
CREDENTIAL_RE = re.compile(r"://[^/\s@]+@")

PULL_TIMEOUT = 300
SYNC_TIMEOUT = 900
MIGRATE_TIMEOUT = 600
MAX_COMMITS = 20
DETAIL_CHARS = 2000


def uv_bin() -> str:
    return os.environ.get("VJHSTUDIO_UV") or shutil.which("uv") or "uv"


def git_auth_needed(text: str) -> bool:
    """True when git failed because this machine has no usable credentials."""
    low = (text or "").lower()
    return any(m in low for m in AUTH_MARKERS)


def run_cmd(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a fixed argv (uv) in the checkout. Patched out in tests — no network."""
    return subprocess.run(  # noqa: S603
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def _failed(cmd: list[str], error: Exception) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 1, "", f"{type(error).__name__}: {error}")


def _git(args: list[str], repo: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return gitinfo.run_git(args, timeout=timeout, cwd=repo)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _failed([gitinfo.git_bin(), *args], e)


def _uv(args: list[str], repo: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    cmd = [uv_bin(), *args]
    try:
        return run_cmd(cmd, repo, timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _failed(cmd, e)


def redact(text: str) -> str:
    """Strip an embedded git credential out of a message before it is shown or logged.
    A remote URL like https://user:token@host/repo.git is echoed verbatim by git on a
    failure, and the step log ends up on screen and in the console."""
    return CREDENTIAL_RE.sub("://***@", text or "")


def _msg(p: subprocess.CompletedProcess[str]) -> str:
    """The line that explains a failure: stderr if there is any, else stdout."""
    return redact((p.stderr or "").strip() or (p.stdout or "").strip())


def _detail(p: subprocess.CompletedProcess[str]) -> str:
    """Everything the command said, trimmed to something a UI can show."""
    return redact(((p.stdout or "") + "\n" + (p.stderr or "")).strip())[-DETAIL_CHARS:]


# --- read-only check -------------------------------------------------------


def check_updates(repo: Path | None = None) -> dict:
    """Fetch and report how far behind upstream this checkout is. Changes nothing."""
    repo = REPO_ROOT if repo is None else Path(repo)
    out: dict = {"git": True, "current": "", "behind": 0, "commits": [], "error": ""}
    if not (repo / ".git").exists():
        out["git"] = False
        out["error"] = ZIP_INSTALL_HELP
        return out
    fetch = _git(["fetch", "--quiet"], repo, timeout=60)
    if fetch.returncode != 0:
        out["error"] = _classify(_msg(fetch))
        return out
    cur = _git(["log", "-1", "--format=%h %s"], repo)
    if cur.returncode == 0:
        out["current"] = cur.stdout.strip()
    count = _git(["rev-list", "--count", "HEAD..@{u}"], repo)
    counted = count.stdout.strip()
    if count.returncode != 0 or not counted.isdigit():
        out["error"] = _classify(_msg(count) or counted)
        return out
    out["behind"] = int(counted)
    if out["behind"]:
        subjects = _git(["log", "--format=%s", "HEAD..@{u}"], repo)
        out["commits"] = [s for s in subjects.stdout.splitlines() if s.strip()][:MAX_COMMITS]
    return out


def _classify(text: str) -> str:
    if git_auth_needed(text):
        return GIT_LOGIN_HELP
    return f"Could not check for updates: {text}".strip()


# --- step log --------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    title: str
    detail: str = ""
    ok: bool = True
    at: datetime = field(default_factory=utcnow)


class UpdateState:
    """The live log of one update run. Written by a worker thread, polled by the UI."""

    def __init__(self, on_step: Callable[[Step], None] | None = None) -> None:
        self._lock = threading.Lock()
        self.on_step = on_step
        self.running: bool = False
        self.ok: bool | None = None
        self.steps: list[Step] = []
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.message: str = ""
        self._restart_claimed: bool = False

    def begin(self) -> bool:
        """Claim the state for a new run. False when one is already in flight."""
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.ok = None
            self.steps = []
            self.started_at = utcnow()
            self.finished_at = None
            self.message = ""
            self._restart_claimed = False
            return True

    def claim_restart(self) -> bool:
        """True for the first caller of this run, False for every caller after it.

        The worker asks for the restart as soon as a run succeeds, and every log
        poll asks too (the run may have finished before this code shipped). Both
        go through this one claim, so the app is only ever re-execed once."""
        with self._lock:
            if self._restart_claimed:
                return False
            self._restart_claimed = True
            return True

    def finish(self, ok: bool) -> None:
        with self._lock:
            self.running = False
            self.ok = ok
            self.finished_at = utcnow()

    def reset(self) -> bool:
        """Forget a finished run's log. False (and nothing cleared) while one runs."""
        with self._lock:
            if self.running:
                return False
            self.ok = None
            self.steps = []
            self.started_at = None
            self.finished_at = None
            self.message = ""
            self._restart_claimed = False
            return True

    def add(self, title: str, detail: str = "", ok: bool = True) -> Step:
        step = Step(title=title, detail=detail, ok=ok)
        with self._lock:
            self.steps.append(step)
        if self.on_step is not None:
            # A noisy callback (a broken SSE client, a print to a closed pipe)
            # must never abort the update itself.
            try:
                self.on_step(step)
            except Exception:  # noqa: BLE001
                log.warning("update step callback failed", exc_info=True)
        return step

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "ok": self.ok,
                "message": self.message,
                "steps": [{"title": s.title, "detail": s.detail, "ok": s.ok} for s in self.steps],
            }


STATE = UpdateState()


# --- the update itself -----------------------------------------------------


def run_update(
    state: UpdateState,
    paths: Paths,
    repo: Path | None = None,
    restart_on_restore: bool = True,
) -> bool:
    """Run the update. `restart_on_restore` is False for a terminal run: the CLI
    holds no database handle, and re-execing would just start the update again."""
    repo = REPO_ROOT if repo is None else Path(repo)
    if not state.running:
        state.begin()
    try:
        ok = _update(state, paths, repo, restart_on_restore)
    except Exception as e:  # noqa: BLE001 - a crash here must still close the log
        log.exception("update crashed")
        state.add("Update failed", str(e), ok=False)
        state.message = "Update failed — nothing was restarted."
        ok = False
    state.finish(ok)
    return ok


def _update(state: UpdateState, paths: Paths, repo: Path, restart_on_restore: bool) -> bool:
    if not (repo / ".git").exists():
        state.add("Not a git checkout", ZIP_INSTALL_HELP, ok=False)
        state.message = ZIP_INSTALL_HELP
        return False

    # 1. Safety backup first: no backup, no update. Nothing has been touched yet.
    try:
        saved = backup.backup_db(paths, "pre-update")
    except Exception as e:  # noqa: BLE001
        state.add("Safety backup failed", str(e), ok=False)
        state.message = "Stopping — refusing to update without a safety backup."
        return False
    state.add("Safety backup", saved.name)
    try:
        # Housekeeping only: an unlinkable old backup must never abort an update.
        backup.rotate(paths, "pre-update")
    except OSError:
        log.warning("pre-update backup rotation failed", exc_info=True)

    # 2. Remember where to roll back to.
    head = _git(["rev-parse", "HEAD"], repo)
    if head.returncode != 0 or not head.stdout.strip():
        state.add("Reading the current version failed", _msg(head), ok=False)
        state.message = "Stopping — nothing was changed."
        return False
    old_sha = head.stdout.strip()

    # 3. Set local edits to *tracked* files aside. Untracked files (data/, a
    #    stray note in the folder) are not git's business and are left alone —
    #    `git stash` would refuse to take them anyway, and stashing nothing and
    #    then popping would restore whatever an older stash happened to hold.
    stashed = False
    status = _git(["status", "--porcelain", "--untracked-files=no"], repo)
    if status.returncode == 0 and status.stdout.strip():
        before = _stash_ref(repo)
        stash = _git(["stash"], repo, timeout=60)
        if stash.returncode != 0:
            state.add("Protecting local changes", _detail(stash), ok=False)
            state.message = "Stopping — local changes could not be set aside."
            return False
        # Trust the ref, not the exit code: "No local changes to save" is a success.
        stashed = _stash_ref(repo) != before
        if stashed:
            state.add("Protecting local changes", _detail(stash))

    # 4. Fast-forward only: a diverged history stops here instead of auto-merging.
    pull = _git(["pull", "--ff-only"], repo, timeout=PULL_TIMEOUT)
    state.add("Downloading update", _detail(pull), ok=pull.returncode == 0)
    if pull.returncode != 0:
        if git_auth_needed(_detail(pull)):
            state.add("Update server login required", GIT_LOGIN_HELP, ok=False)
        _pop(state, repo, stashed)
        state.message = "Update aborted — the app was not changed."
        return False

    # 5. Dependencies for the new code.
    sync = _uv(["sync", "--frozen"], repo, SYNC_TIMEOUT)
    state.add("Installing packages", _detail(sync), ok=sync.returncode == 0)
    if sync.returncode != 0:
        _rollback_code(state, repo, old_sha)
        _pop(state, repo, stashed)
        state.add("Rolled back to the previous version", old_sha[:12], ok=False)
        state.message = "Update failed while installing packages — rolled back."
        return False

    # 6. Migrations run in a fresh process so they use the packages just synced.
    mig = _uv(
        ["run", "--frozen", "python", "-m", "vjhstudio.services.migrate", "upgrade"],
        repo,
        MIGRATE_TIMEOUT,
    )
    state.add("Database migration", _detail(mig), ok=mig.returncode == 0)
    if mig.returncode != 0:
        _rollback_code(state, repo, old_sha)
        restored = _restore_db(state, paths, saved)
        _pop(state, repo, stashed)
        state.add("Rolled back to the previous version", old_sha[:12], ok=False)
        state.message = "Update failed during the database migration — rolled back."
        if restored and restart_on_restore:
            # The file under the live engine has just been swapped: every open
            # connection now points at a database that is no longer there.
            # Coming back up on the restored file is the only safe state.
            state.add(RESTART_STEP, ok=False)
            restart.request_restart()
        return False

    _pop(state, repo, stashed)
    _chmod_scripts(state, repo)
    state.message = "Update complete — restarting VJHStudio."
    return True


def _stash_ref(repo: Path) -> str:
    """The sha of the newest stash entry, or "" when the stash is empty."""
    return _git(["rev-parse", "--verify", "--quiet", "refs/stash"], repo).stdout.strip()


def _pop(state: UpdateState, repo: Path, stashed: bool) -> None:
    if not stashed:
        return
    pop = _git(["stash", "pop"], repo, timeout=60)
    if pop.returncode == 0:
        state.add("Restoring local changes", _detail(pop))
        return
    # A conflict is a warning, not an update failure: the new code is installed
    # and the edits are still safe in the stash.
    state.add(
        "Local changes kept in the git stash",
        _detail(pop) + '\nRun "git stash pop" in the VJHStudio folder to get them back.',
        ok=False,
    )


def _rollback_code(state: UpdateState, repo: Path, old_sha: str) -> None:
    reset = _git(["reset", "--hard", old_sha], repo, timeout=120)
    state.add("Restoring the previous code", _detail(reset), ok=False)
    resync = _uv(["sync", "--frozen"], repo, SYNC_TIMEOUT)
    state.add("Reinstalling the previous packages", _detail(resync), ok=False)


def _restore_db(state: UpdateState, paths: Paths, saved: Path) -> bool:
    """Put the pre-update database back; WAL sidecars of the failed migration must go."""
    try:
        for suffix in ("-wal", "-shm"):
            Path(str(paths.db) + suffix).unlink(missing_ok=True)
        shutil.copy2(saved, paths.db)
    except OSError as e:
        state.add("Restoring the database failed", f"{e} (backup kept at {saved})", ok=False)
        return False
    state.add(RESTORE_STEP, saved.name, ok=False)
    return True


def _chmod_scripts(state: UpdateState, repo: Path) -> None:
    """New/updated launcher scripts arrive without the executable bit on NTFS mounts."""
    if sys.platform == "win32":
        return
    fixed: list[str] = []
    for f in sorted([*repo.glob("*.sh"), *repo.glob("scripts/*.sh")]):
        try:
            # Adds the executable bits only, and only to scripts we ship.
            os.chmod(f, f.stat().st_mode | 0o111)  # noqa: S103
        except OSError:
            continue
        fixed.append(f.name)
    if fixed:
        state.add("Launcher scripts made executable", ", ".join(fixed))


def _worker(
    state: UpdateState, paths: Paths, restart_on_restore: bool, restart_after: bool
) -> None:
    # run_update closes the state itself. Finishing again here would let a late
    # thread clobber the *next* run's state, so the only finish on this path is
    # the one that covers a run_update that never returned at all.
    try:
        ok = run_update(state, paths, restart_on_restore=restart_on_restore)
    except BaseException as e:
        log.exception("update thread crashed")
        state.add("Update failed", str(e), ok=False)
        state.finish(False)
        raise
    # The new code is on disk but this process is still running the old one, and
    # nobody may have the log open. The claim is shared with the log poll, so
    # whichever of the two gets here first is the only one that re-execs.
    if ok and restart_after and state.claim_restart():
        restart.request_restart()


def start_update(
    paths: Paths,
    state: UpdateState | None = None,
    restart_on_restore: bool = True,
    restart_after: bool | None = None,
) -> bool:
    """Start an update in the background. False when one is already running.

    `restart_after` (the restart on a *successful* run) defaults to
    `restart_on_restore`: a caller that owns the restart after a rollback owns
    the one after a success too."""
    state = STATE if state is None else state
    restart_after = restart_on_restore if restart_after is None else restart_after
    if not state.begin():
        return False
    threading.Thread(
        target=_worker,
        args=(state, paths, restart_on_restore, restart_after),
        daemon=True,
        name="vjh-update",
    ).start()
    return True


# --- cached notice ---------------------------------------------------------


@dataclass(frozen=True)
class Notice:
    behind: int
    commits: list[str]
    checked_at: str
    error: str


def check_and_store(session_factory: sessionmaker[Session], repo: Path | None = None) -> dict:
    """Run the read-only check and cache the answer in app_meta for the header badge."""
    info = check_updates(repo)
    with db.session_scope(session_factory) as s:
        meta.set(s, "update.behind", str(info["behind"]))
        meta.set(s, "update.commits", json.dumps(info["commits"]))
        meta.set(s, "update.checked_at", utcnow().isoformat())
        meta.set(s, "update.error", info["error"])
    return info


def behind_count(session: Session) -> int:
    """Just the badge number. The header renders on every page and on every badge
    poll, so it reads one row instead of the whole notice."""
    try:
        return int(meta.get(session, "update.behind") or 0)
    except ValueError:
        return 0


def read_notice(session: Session) -> Notice:
    behind = behind_count(session)
    try:
        commits = [str(c) for c in json.loads(meta.get(session, "update.commits") or "[]")]
    except (TypeError, ValueError):
        commits = []
    return Notice(
        behind,
        commits,
        meta.get(session, "update.checked_at") or "",
        meta.get(session, "update.error") or "",
    )
