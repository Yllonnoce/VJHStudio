import socket
import sys

import pytest
import uvicorn

from runwarestudio import __version__, config, main
from runwarestudio import boot as boot_mod
from runwarestudio.services import migrate
from runwarestudio.web.app import create_app

def test_version_command(capsys):
    assert main.main(["version"]) == 0
    assert __version__ in capsys.readouterr().out

def test_migrate_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNWARESTUDIO_DATA_DIR", str(tmp_path))
    assert main.main(["migrate"]) == 0
    assert (tmp_path / "studio.db").exists()
    assert "schema" in capsys.readouterr().out

def test_doctor_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNWARESTUDIO_DATA_DIR", str(tmp_path))
    assert main.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "data dir" in out and "api key" in out and "git" in out

def test_pick_port_skips_busy_foreign_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
    busy = s.getsockname()[1]
    try:
        port, ours = main.pick_port("127.0.0.1", busy, tries=3)
        assert port != busy and ours is False
    finally:
        s.close()

def test_pick_port_returns_free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    assert main.pick_port("127.0.0.1", free) == (free, False)

def test_serve_boots_before_uvicorn_and_reports_migration_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNWARESTUDIO_DATA_DIR", str(tmp_path))

    def fail_boot(_paths):
        raise migrate.MigrationFailed("x")

    def fail_run(*_args, **_kwargs):
        pytest.fail("uvicorn must not start")

    monkeypatch.setattr(boot_mod, "boot", fail_boot)
    monkeypatch.setattr(uvicorn, "run", fail_run)

    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    rc = main.main(["serve", "--port", str(free), "--no-browser"])
    assert rc == config.MIGRATION_FAIL_EXIT_CODE
    assert "x" in capsys.readouterr().err

async def test_create_app_with_boot_info_skips_reboot_in_lifespan(tmp_path, monkeypatch):
    paths = config.resolve_paths(env={"RUNWARESTUDIO_DATA_DIR": str(tmp_path)})
    info = boot_mod.boot(paths)

    def fail_boot(_paths):
        raise AssertionError("boot.boot must not be called again")

    monkeypatch.setattr(boot_mod, "boot", fail_boot)
    app = create_app(paths, boot_info=info)
    async with app.router.lifespan_context(app):
        assert app.state.boot is info

def test_doctor_macos_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNWARESTUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert main.main(["doctor"]) == 0
    assert "Local Network" in capsys.readouterr().out

def test_doctor_no_macos_notes_on_linux(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNWARESTUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    assert main.main(["doctor"]) == 0
    assert "Local Network" not in capsys.readouterr().out
