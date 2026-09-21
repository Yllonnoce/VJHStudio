"""Self-update service. No network: every git test runs against a temp bare repo."""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from vjhstudio import config, db
from vjhstudio.services import gitinfo, meta, migrate, restart, update

# The developer's own ~/.gitconfig (hooks, signing, default branch, aliases)
# must not reach these repos: every test repo configures what it needs itself.
TEST_GIT_ENV = dict(gitinfo.GIT_ENV, GIT_CONFIG_GLOBAL=os.devnull)


def git(*args, cwd=None) -> str:
    p = subprocess.run(
        [gitinfo.git_bin(), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=TEST_GIT_ENV,
        check=False,
    )
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _configure(repo: Path) -> None:
    git("config", "user.email", "test@vjhstudio.invalid", cwd=repo)
    git("config", "user.name", "VJHStudio Test", cwd=repo)
    git("config", "commit.gpgsign", "false", cwd=repo)


def _commit(repo: Path, name: str, subject: str) -> None:
    (repo / name).write_text(subject, encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-m", subject, cwd=repo)


@pytest.fixture
def git_repo(tmp_path) -> Path:
    """A clone of a temp bare 'origin', one commit in, upstream tracking set."""
    origin = tmp_path / "origin.git"
    git("init", "--bare", "-q", str(origin))
    git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work))
    _configure(work)
    git("checkout", "-q", "-b", "main", cwd=work)
    _commit(work, "README.md", "first commit")
    git("push", "-q", "-u", "origin", "main", cwd=work)
    clone = tmp_path / "clone"
    git("clone", "-q", str(origin), str(clone))
    _configure(clone)
    return clone


def upstream_commit(clone: Path, subject: str) -> None:
    """Add a commit to the 'remote' so the clone under test falls behind."""
    work = clone.parent / "work"
    _commit(work, subject.replace(" ", "_") + ".txt", subject)
    git("push", "-q", "origin", "main", cwd=work)


@pytest.fixture
def paths(tmp_path):
    p = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "data")})
    config.ensure_dirs(p)
    migrate.upgrade(p.db)
    return p


def fake_uv(monkeypatch, fail=None) -> list[list[str]]:
    """Record uv invocations; `fail(cmd, calls)` decides which ones return non-zero."""
    calls: list[list[str]] = []

    def run_cmd(cmd, cwd, timeout):
        calls.append(list(cmd))
        bad = bool(fail and fail(list(cmd), calls))
        return subprocess.CompletedProcess(cmd, 1 if bad else 0, "uv ok\n", "boom\n" if bad else "")

    monkeypatch.setattr(update, "run_cmd", run_cmd)
    return calls


def restart_spy(monkeypatch) -> list[str]:
    """Never let a test execv the test runner; record the request instead."""
    fired: list[str] = []
    monkeypatch.setattr(restart, "request_restart", lambda *a, **k: fired.append("restart"))
    return fired


def wait_until(pred, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


# --- check_updates ---------------------------------------------------------


def test_check_updates_lists_new_upstream_commits(git_repo):
    upstream_commit(git_repo, "second commit")
    upstream_commit(git_repo, "third commit")
    info = update.check_updates(repo=git_repo)
    assert info["git"] is True
    assert info["behind"] == 2
    assert info["commits"] == ["third commit", "second commit"]
    assert info["error"] == ""
    assert info["current"].startswith(git("rev-parse", "--short", "HEAD", cwd=git_repo))


def test_check_updates_up_to_date(git_repo):
    info = update.check_updates(repo=git_repo)
    assert info["behind"] == 0 and info["commits"] == [] and info["error"] == ""


def test_check_updates_on_zip_install(tmp_path):
    info = update.check_updates(repo=tmp_path)
    assert info["git"] is False
    assert info["behind"] == 0
    assert info["error"] == update.ZIP_INSTALL_HELP


def test_check_updates_reports_login_help(git_repo, monkeypatch):
    def fake(args, timeout=30, cwd=None):
        return subprocess.CompletedProcess(
            args,
            128,
            "",
            "fatal: could not read Username for 'https://x': terminal prompts disabled",
        )

    monkeypatch.setattr(gitinfo, "run_git", fake)
    info = update.check_updates(repo=git_repo)
    assert info["error"] == update.GIT_LOGIN_HELP
    assert info["behind"] == 0


def test_check_updates_reports_other_failures(git_repo, monkeypatch):
    def fake(args, timeout=30, cwd=None):
        return subprocess.CompletedProcess(args, 1, "", "fatal: unable to access remote")

    monkeypatch.setattr(gitinfo, "run_git", fake)
    info = update.check_updates(repo=git_repo)
    assert info["error"].startswith("Could not check for updates:")
    assert "unable to access remote" in info["error"]


def test_git_auth_needed_phrases():
    for text in (
        "fatal: could not read Username for 'https://github.com'",
        "fatal: could not read Password for 'https://github.com'",
        "terminal prompts disabled",
        "fatal: Authentication failed for 'https://github.com/x.git/'",
        "git@github.com: Permission denied (publickey).",
        "Host key verification failed.",
    ):
        assert update.git_auth_needed(text) is True
    assert update.git_auth_needed("fatal: not a git repository") is False


def test_redact_hides_an_embedded_git_credential():
    """A remote URL with a token in it is echoed back by git on a failure, and the
    step log is shown on screen and written to the console log."""
    text = (
        "fatal: unable to access 'https://eric:ghp_supersecret@github.com/x/y.git/': 403\n"
        "hint: try ssh://deploy:hunter2@code.example/x.git instead"
    )
    out = update.redact(text)
    assert "ghp_supersecret" not in out and "hunter2" not in out and "eric" not in out
    assert "https://***@github.com/x/y.git/" in out
    assert "ssh://***@code.example/x.git" in out
    # A URL with no credentials, and a plain sentence with a colon, are left alone.
    assert update.redact("https://github.com/x/y.git") == "https://github.com/x/y.git"
    assert update.redact("error: could not read: no such file") == (
        "error: could not read: no such file"
    )
    assert update.redact("") == ""


def test_a_failing_command_reports_no_credentials(git_repo, monkeypatch):
    """The redaction is wired into the messages, not just available as a helper."""
    leak = "fatal: unable to access 'https://eric:ghp_supersecret@github.com/x.git/'"

    def fake(args, timeout=30, cwd=None):
        return subprocess.CompletedProcess(args, 1, leak, leak)

    monkeypatch.setattr(gitinfo, "run_git", fake)
    info = update.check_updates(repo=git_repo)
    assert "ghp_supersecret" not in info["error"]
    assert "***@github.com" in info["error"]


def test_uv_bin_prefers_env(monkeypatch):
    monkeypatch.setenv("VJHSTUDIO_UV", "/opt/uv")
    assert update.uv_bin() == "/opt/uv"
    monkeypatch.delenv("VJHSTUDIO_UV")
    assert update.uv_bin().endswith("uv")


# --- run_update ------------------------------------------------------------


def test_run_update_happy_path(git_repo, paths, monkeypatch):
    upstream_commit(git_repo, "second commit")
    calls = fake_uv(monkeypatch)
    fired = restart_spy(monkeypatch)
    state = update.UpdateState()

    assert update.run_update(state, paths, repo=git_repo) is True

    assert git("rev-parse", "HEAD", cwd=git_repo) == git("rev-parse", "origin/main", cwd=git_repo)
    titles = " | ".join(s.title for s in state.steps)
    for expected in (
        "Safety backup",
        "Downloading update",
        "Installing packages",
        "Database migration",
    ):
        assert expected in titles
    assert list(paths.backups.glob("vjh-*-pre-update.db"))
    assert state.ok is True and state.running is False
    assert "restarting" in state.message.lower()
    assert [c for c in calls if c[1:3] == ["sync", "--frozen"]]
    assert [c for c in calls if "vjhstudio.services.migrate" in c]
    assert fired == []  # the caller restarts, never the service


def test_run_update_rolls_back_when_uv_sync_fails(git_repo, paths, monkeypatch):
    upstream_commit(git_repo, "second commit")
    old_sha = git("rev-parse", "HEAD", cwd=git_repo)
    calls = fake_uv(monkeypatch, fail=lambda cmd, calls: cmd[1] == "sync" and len(calls) == 1)
    fired = restart_spy(monkeypatch)
    state = update.UpdateState()

    assert update.run_update(state, paths, repo=git_repo) is False

    assert git("rev-parse", "HEAD", cwd=git_repo) == old_sha
    assert len([c for c in calls if c[1] == "sync"]) == 2  # rollback re-synced
    assert state.steps[-1].ok is False
    assert state.ok is False
    assert fired == []


def test_run_update_restores_database_when_migration_fails(git_repo, paths, monkeypatch):
    upstream_commit(git_repo, "second commit")
    old_sha = git("rev-parse", "HEAD", cwd=git_repo)
    wal = Path(str(paths.db) + "-wal")
    shm = Path(str(paths.db) + "-shm")
    wal.write_bytes(b"")
    shm.write_bytes(b"")
    fake_uv(monkeypatch, fail=lambda cmd, calls: "vjhstudio.services.migrate" in cmd)
    fired = restart_spy(monkeypatch)
    state = update.UpdateState()

    assert update.run_update(state, paths, repo=git_repo) is False

    assert git("rev-parse", "HEAD", cwd=git_repo) == old_sha
    saved = next(iter(paths.backups.glob("vjh-*-pre-update.db")))
    assert paths.db.read_bytes() == saved.read_bytes()
    assert not wal.exists() and not shm.exists()
    assert state.ok is False
    # The database file under the live engine was swapped: come back up on it.
    assert fired == ["restart"]
    assert state.steps[-1].title == "Restarting to load the restored database"


def test_run_update_skips_the_restart_when_the_caller_owns_it(git_repo, paths, monkeypatch):
    """`vjhstudio update` in a terminal restores the database but must not re-exec."""
    fake_uv(monkeypatch, fail=lambda cmd, calls: "vjhstudio.services.migrate" in cmd)
    fired = restart_spy(monkeypatch)
    state = update.UpdateState()

    assert update.run_update(state, paths, repo=git_repo, restart_on_restore=False) is False

    titles = [s.title for s in state.steps]
    assert update.RESTORE_STEP in titles  # the database still came back
    assert update.RESTART_STEP not in titles
    assert fired == []
    saved = next(iter(paths.backups.glob("vjh-*-pre-update.db")))
    assert paths.db.read_bytes() == saved.read_bytes()


def test_start_update_threads_the_restart_flag(paths, monkeypatch):
    restart_spy(monkeypatch)  # the worker restarts after a successful run
    seen: list[dict] = []

    def fake(state, _paths, **kwargs):
        seen.append(dict(kwargs))
        state.finish(True)
        return True

    monkeypatch.setattr(update, "run_update", fake)
    state = update.UpdateState()

    assert update.start_update(paths, state) is True  # web default: the app restarts itself
    assert wait_until(lambda: len(seen) == 1 and not state.running)
    assert seen[0] == {"restart_on_restore": True}

    assert update.start_update(paths, state, restart_on_restore=False) is True
    assert wait_until(lambda: len(seen) == 2 and not state.running)
    assert seen[1] == {"restart_on_restore": False}


def test_run_update_does_not_restart_when_the_restore_fails(git_repo, paths, monkeypatch):
    fake_uv(monkeypatch, fail=lambda cmd, calls: "vjhstudio.services.migrate" in cmd)
    fired = restart_spy(monkeypatch)

    def boom(_saved, _db, **_kw):
        raise OSError("read-only data dir")

    monkeypatch.setattr(update.shutil, "copy2", boom)
    state = update.UpdateState()

    assert update.run_update(state, paths, repo=git_repo) is False
    assert fired == []
    assert any(s.title == "Restoring the database failed" for s in state.steps)


def test_run_update_stops_when_backup_fails(git_repo, tmp_path, monkeypatch):
    empty = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "nodb")})
    config.ensure_dirs(empty)
    seen: list[list[str]] = []

    def spy(args, timeout=30, cwd=None):
        seen.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gitinfo, "run_git", spy)
    monkeypatch.setattr(update, "run_cmd", lambda *a, **k: pytest.fail("uv must not run"))
    state = update.UpdateState()

    assert update.run_update(state, empty, repo=git_repo) is False
    assert seen == []
    assert state.steps[-1].ok is False and state.ok is False


def test_run_update_refuses_zip_install(tmp_path, paths):
    state = update.UpdateState()
    assert update.run_update(state, paths, repo=tmp_path) is False
    assert state.steps[0].ok is False
    assert update.ZIP_INSTALL_HELP in state.steps[0].detail


def test_run_update_stashes_and_restores_modified_tracked_files(git_repo, paths, monkeypatch):
    upstream_commit(git_repo, "second commit")
    readme = git_repo / "README.md"
    readme.write_text("local tinkering", encoding="utf-8")
    fake_uv(monkeypatch)
    restart_spy(monkeypatch)
    state = update.UpdateState()

    assert update.run_update(state, paths, repo=git_repo) is True

    titles = [s.title for s in state.steps]
    assert "Protecting local changes" in titles
    assert "Restoring local changes" in titles
    assert readme.read_text(encoding="utf-8") == "local tinkering"
    assert (git_repo / "second_commit.txt").exists()
    assert git("stash", "list", cwd=git_repo) == ""  # the stash was popped, not left behind


def test_run_update_ignores_untracked_files(git_repo, paths, monkeypatch):
    """`git stash` refuses untracked files: stashing nothing and then popping
    would restore an unrelated older stash over the user's checkout."""
    upstream_commit(git_repo, "second commit")
    git("stash", "list", cwd=git_repo)
    (git_repo / "notes.txt").write_text("my own scratch file", encoding="utf-8")
    fake_uv(monkeypatch)
    restart_spy(monkeypatch)
    state = update.UpdateState()

    assert update.run_update(state, paths, repo=git_repo) is True

    titles = [s.title for s in state.steps]
    assert "Protecting local changes" not in titles
    assert "Restoring local changes" not in titles
    assert "Local changes kept in the git stash" not in titles
    assert git("stash", "list", cwd=git_repo) == ""
    assert (git_repo / "notes.txt").read_text(encoding="utf-8") == "my own scratch file"


def test_run_update_never_pops_a_pre_existing_stash(git_repo, paths, monkeypatch):
    """An old stash entry from the user plus an untracked-only checkout: the
    update must leave that entry exactly where it is."""
    (git_repo / "README.md").write_text("earlier experiment", encoding="utf-8")
    git("stash", cwd=git_repo)
    kept = git("rev-parse", "refs/stash", cwd=git_repo)
    upstream_commit(git_repo, "second commit")
    (git_repo / "notes.txt").write_text("untracked", encoding="utf-8")
    fake_uv(monkeypatch)
    restart_spy(monkeypatch)
    state = update.UpdateState()

    assert update.run_update(state, paths, repo=git_repo) is True
    assert git("rev-parse", "refs/stash", cwd=git_repo) == kept
    assert git("status", "--porcelain", "--untracked-files=no", cwd=git_repo) == ""


# --- state -----------------------------------------------------------------


def test_start_update_refuses_a_second_run(paths, monkeypatch):
    restart_spy(monkeypatch)  # the worker restarts after a successful run
    gate = threading.Event()

    def slow(state, _paths, **_kwargs):
        state.add("working")
        gate.wait(5)
        state.finish(True)  # run_update closes its own state
        return True

    monkeypatch.setattr(update, "run_update", slow)
    state = update.UpdateState()
    try:
        assert update.start_update(paths, state) is True
        assert wait_until(lambda: bool(state.steps))
        assert update.start_update(paths, state) is False
        assert len(state.steps) == 1
        snap = state.snapshot()
        assert json.loads(json.dumps(snap)) == snap
        assert snap["running"] is True and snap["steps"][0]["title"] == "working"
    finally:
        gate.set()
    assert wait_until(lambda: not state.running)
    assert state.ok is True


def test_start_update_runs_again_after_a_finished_run(paths, monkeypatch):
    """The worker must not finish a state twice: a late finish from run 1 would
    reopen — or close — run 2's log."""
    restart_spy(monkeypatch)  # the worker restarts after a successful run
    runs: list[int] = []

    def quick(state, _paths, **_kwargs):
        runs.append(len(runs) + 1)
        state.add(f"run {runs[-1]}")
        state.finish(True)
        return True

    monkeypatch.setattr(update, "run_update", quick)
    state = update.UpdateState()

    assert update.start_update(paths, state) is True
    assert wait_until(lambda: not state.running and len(runs) == 1)

    assert update.start_update(paths, state) is True
    assert wait_until(lambda: not state.running and len(runs) == 2)

    assert [s.title for s in state.steps] == ["run 2"]  # begin() reset the log
    assert state.ok is True and state.finished_at is not None


def test_state_streams_steps_to_the_callback():
    seen = []
    state = update.UpdateState(on_step=seen.append)
    state.add("one", "detail")
    state.add("two", ok=False)
    assert [s.title for s in seen] == ["one", "two"]
    assert state.snapshot()["steps"] == [
        {"title": "one", "detail": "detail", "ok": True},
        {"title": "two", "detail": "", "ok": False},
    ]


# --- notice ----------------------------------------------------------------


def test_check_and_store_round_trips_the_notice(paths, monkeypatch):
    factory = db.make_session_factory(db.make_engine(paths.db))
    monkeypatch.setattr(
        update,
        "check_updates",
        lambda repo=None: {
            "git": True,
            "current": "abc1234 first commit",
            "behind": 2,
            "commits": ["third commit", "second commit"],
            "error": "",
        },
    )

    info = update.check_and_store(factory)
    assert info["behind"] == 2

    with db.session_scope(factory) as s:
        assert meta.get(s, "update.behind") == "2"
        assert json.loads(meta.get(s, "update.commits")) == ["third commit", "second commit"]
        assert meta.get(s, "update.checked_at")
        assert meta.get(s, "update.error") == ""
        notice = update.read_notice(s)
    assert notice.behind == 2
    assert notice.commits == ["third commit", "second commit"]
    assert notice.checked_at and notice.error == ""


def test_read_notice_defaults_on_an_empty_database(paths):
    factory = db.make_session_factory(db.make_engine(paths.db))
    with db.session_scope(factory) as s:
        notice = update.read_notice(s)
    assert notice == update.Notice(0, [], "", "")


def test_redact_hides_a_token_only_credential():
    """A personal access token is often the whole userinfo part, with no colon in it."""
    out = update.redact(
        "fatal: unable to access 'https://ghp_supersecret@github.com/x/y.git/': 403"
    )
    assert "ghp_supersecret" not in out
    assert "https://***@github.com/x/y.git/" in out
    # The colon form still goes, and an ordinary URL is still left alone.
    assert update.redact("https://eric:pw@github.com/x") == "https://***@github.com/x"
    assert update.redact("https://github.com/x/y.git") == "https://github.com/x/y.git"


# --- the restart claim -----------------------------------------------------


def test_claim_restart_is_won_by_exactly_one_caller():
    """The worker and every log poll race for it; two re-execs would be very bad."""
    state = update.UpdateState()
    state.begin()
    state.finish(True)
    wins: list[int] = []
    gate = threading.Barrier(8)

    def go():
        gate.wait(5)
        if state.claim_restart():
            wins.append(1)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert wins == [1]
    assert state.claim_restart() is False
    assert state.begin() is True  # a new run gets a fresh claim
    assert state.claim_restart() is True


def test_worker_restarts_after_a_successful_run(paths, monkeypatch):
    """Nobody may have the log open: the new code is on disk but this process is
    still running the old one, so the worker itself asks for the restart."""
    fired = restart_spy(monkeypatch)

    def succeeded(state, _paths, **_kwargs):
        state.add("Database migration")
        state.finish(True)
        return True

    monkeypatch.setattr(update, "run_update", succeeded)
    state = update.UpdateState()
    assert update.start_update(paths, state) is True
    assert wait_until(lambda: not state.running and fired == ["restart"])
    assert fired == ["restart"]
    assert state.claim_restart() is False  # the run's one claim is spent


def test_worker_restart_and_a_log_poll_never_fire_twice(paths, monkeypatch):
    fired = restart_spy(monkeypatch)
    polls: list[bool] = []

    def succeeded(state, _paths, **_kwargs):
        state.finish(True)
        # A browser poll lands the instant the run is marked finished.
        polls.append(state.claim_restart())
        return True

    monkeypatch.setattr(update, "run_update", succeeded)
    state = update.UpdateState()
    assert update.start_update(paths, state) is True
    assert wait_until(lambda: not state.running and len(polls) == 1)
    assert polls == [True]
    assert fired == []  # the poll won the claim, so the worker stood down


def test_worker_never_restarts_a_failed_run(paths, monkeypatch):
    fired = restart_spy(monkeypatch)

    def failed(state, _paths, **_kwargs):
        state.add("Downloading update", "fatal", ok=False)
        state.finish(False)
        return False

    monkeypatch.setattr(update, "run_update", failed)
    state = update.UpdateState()
    assert update.start_update(paths, state) is True
    assert wait_until(lambda: not state.running)
    assert fired == []
    assert state.claim_restart() is True  # nothing claimed it


def test_worker_does_not_restart_when_the_caller_owns_the_restart(paths, monkeypatch):
    """The flag the CLI-style caller passes turns the worker's restart off too."""
    fired = restart_spy(monkeypatch)

    def succeeded(state, _paths, **_kwargs):
        state.finish(True)
        return True

    monkeypatch.setattr(update, "run_update", succeeded)
    state = update.UpdateState()
    assert update.start_update(paths, state, restart_on_restore=False) is True
    assert wait_until(lambda: not state.running)
    assert fired == []
