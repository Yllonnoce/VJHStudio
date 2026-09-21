import socket
import sys

import pytest
import uvicorn

from vjhstudio import __version__, config, main
from vjhstudio import boot as boot_mod
from vjhstudio.services import migrate, restart, update
from vjhstudio.web.app import create_app


def test_version_command(capsys):
    assert main.main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_migrate_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))
    assert main.main(["migrate"]) == 0
    assert (tmp_path / "vjh.db").exists()
    assert "schema" in capsys.readouterr().out


def test_doctor_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))
    assert main.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "data dir" in out and "api key" in out and "git" in out


def test_pick_port_skips_busy_foreign_port(monkeypatch):
    monkeypatch.setattr(main, "is_ours", lambda h, p: False)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    busy = s.getsockname()[1]
    try:
        port, ours = main.pick_port("127.0.0.1", busy, tries=3)
        assert port != busy and ours is False
    finally:
        s.close()


def test_pick_port_returns_free_port(monkeypatch):
    monkeypatch.setattr(main, "is_ours", lambda h, p: False)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    assert main.pick_port("127.0.0.1", free) == (free, False)


def test_pick_port_asks_is_ours_before_binding(monkeypatch):
    """On Windows a bind test can succeed on a listening port, so is_ours goes first."""
    calls = []
    monkeypatch.setattr(main, "is_ours", lambda h, p: True)
    monkeypatch.setattr(main, "_port_free", lambda h, p: calls.append(p) or True)
    assert main.pick_port("127.0.0.1", 8080) == (8080, True)
    assert calls == []


def test_port_free_never_relaxes_address_reuse_on_windows(monkeypatch):
    """On Windows SO_REUSEADDR would let the bind succeed on a listening port, so the
    Windows branch must not set it (it may set SO_EXCLUSIVEADDRUSE instead)."""
    calls = []

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def setsockopt(self, level, opt, value):
            calls.append(opt)

        def bind(self, addr):
            pass

    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.socket, "socket", lambda *a, **k: FakeSock())
    monkeypatch.setattr(main.socket, "SO_EXCLUSIVEADDRUSE", 12345, raising=False)
    assert main._port_free("127.0.0.1", 8080) is True
    assert socket.SO_REUSEADDR not in calls and 12345 in calls


def test_serve_boots_before_uvicorn_and_reports_migration_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))

    def fail_boot(_paths):
        raise migrate.MigrationFailed("x")

    def fail_run(*_args, **_kwargs):
        pytest.fail("uvicorn must not start")

    monkeypatch.setattr(boot_mod, "boot", fail_boot)
    monkeypatch.setattr(uvicorn, "run", fail_run)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    rc = main.main(["serve", "--port", str(free), "--no-browser"])
    assert rc == config.MIGRATION_FAIL_EXIT_CODE
    assert "x" in capsys.readouterr().err


async def test_create_app_with_boot_info_skips_reboot_in_lifespan(tmp_path, monkeypatch):
    paths = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    info = boot_mod.boot(paths)

    def fail_boot(_paths):
        raise AssertionError("boot.boot must not be called again")

    monkeypatch.setattr(boot_mod, "boot", fail_boot)
    app = create_app(paths, boot_info=info)
    async with app.router.lifespan_context(app):
        assert app.state.boot is info


def test_doctor_macos_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert main.main(["doctor"]) == 0
    assert "Local Network" in capsys.readouterr().out


def test_doctor_no_macos_notes_on_linux(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    assert main.main(["doctor"]) == 0
    assert "Local Network" not in capsys.readouterr().out


def test_lowercase_log_level_does_not_crash(monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_LOG_LEVEL", "debug")
    assert main.main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_log_level_normalises_and_falls_back():
    assert main.log_level({"VJHSTUDIO_LOG_LEVEL": "debug"}) == "DEBUG"
    assert main.log_level({"VJHSTUDIO_LOG_LEVEL": " Warning "}) == "WARNING"
    assert main.log_level({"VJHSTUDIO_LOG_LEVEL": "warn"}) == "WARNING"
    assert main.log_level({"VJHSTUDIO_LOG_LEVEL": "chatty"}) == "INFO"
    assert main.log_level({}) == "INFO"


@pytest.mark.skipif(sys.platform == "win32", reason="TIME_WAIT reproduction is POSIX-only")
def test_port_free_ignores_time_wait_after_restart():
    """After a restart the old listener's connections sit in TIME_WAIT for a while;
    the port must still count as free so the app comes back on the same port."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    cli = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    conn.close()  # server side closes first -> its side enters TIME_WAIT
    cli.close()
    srv.close()
    assert main._port_free("127.0.0.1", port) is True


def test_update_check_prints_pending_commits(monkeypatch, capsys):
    monkeypatch.setattr(
        update,
        "check_updates",
        lambda: {
            "git": True,
            "current": "abc1234 first commit",
            "behind": 2,
            "commits": ["third commit", "second commit"],
            "error": "",
        },
    )
    assert main.main(["update", "--check"]) == 0
    out = capsys.readouterr().out
    assert "2 commits behind" in out
    assert "third commit" in out and "second commit" in out


def test_update_check_prints_the_error_and_still_succeeds(monkeypatch, capsys):
    monkeypatch.setattr(
        update,
        "check_updates",
        lambda: {
            "git": False,
            "current": "",
            "behind": 0,
            "commits": [],
            "error": update.ZIP_INSTALL_HELP,
        },
    )
    assert main.main(["update", "--check"]) == 0
    assert update.ZIP_INSTALL_HELP in capsys.readouterr().out


def test_update_command_streams_steps_and_reports_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))

    def fail(state, _paths, **_kwargs):
        state.add("Downloading update", "boom", ok=False)
        return False

    monkeypatch.setattr(update, "run_update", fail)
    assert main.main(["update"]) == 1
    out = capsys.readouterr().out
    assert "Downloading update" in out
    assert "Restart VJHStudio" not in out


def test_update_command_asks_for_a_restart_on_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))

    def succeed(state, _paths, **_kwargs):
        state.add("Update complete")
        return True

    monkeypatch.setattr(update, "run_update", succeed)
    assert main.main(["update"]) == 0
    assert "Restart VJHStudio to run the new version." in capsys.readouterr().out


def test_migrate_module_cli_upgrades_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))
    assert migrate.main(["upgrade"]) == 0
    assert (tmp_path / "vjh.db").exists()
    capsys.readouterr()
    assert migrate.main(["current"]) == 0
    assert capsys.readouterr().out.strip() == migrate.head()
    assert migrate.main(["head"]) == 0
    assert capsys.readouterr().out.strip() == migrate.head()
    assert migrate.main(["nonsense"]) == 2


def test_update_command_never_re_execs_after_a_database_restore(tmp_path, monkeypatch, capsys):
    """The CLI holds no database handle: restarting would only re-run the update."""
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))
    fired = []
    monkeypatch.setattr(restart, "request_restart", lambda *a, **k: fired.append("restart"))
    seen = {}

    def restored(state, _paths, **kwargs):
        seen.update(kwargs)
        state.add(update.RESTORE_STEP, "vjh-20260920-000000-pre-update.db", ok=False)
        if kwargs.get("restart_on_restore", True):
            restart.request_restart()
        return False

    monkeypatch.setattr(update, "run_update", restored)
    assert main.main(["update"]) == 1
    assert seen == {"restart_on_restore": False}
    assert fired == []
    assert (
        "Database restored from the pre-update backup; start the app again."
        in capsys.readouterr().out
    )
