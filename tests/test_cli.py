import socket
from runwarestudio import __version__, main

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
