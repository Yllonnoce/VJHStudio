import os, stat, sys
from pathlib import Path
from vjhstudio import config

def test_default_data_dir_is_repo_data():
    p = config.resolve_paths(env={})
    assert p.data == config.REPO_ROOT / "data"
    assert p.db == p.data / "vjh.db"
    assert p.api_key_file == p.data / "secrets" / "api_key"

def test_env_overrides_data_dir(tmp_path):
    p = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "d")})
    assert p.data == tmp_path / "d"
    assert p.outputs == tmp_path / "d" / "outputs"

def test_ensure_dirs_creates_layout(tmp_path):
    p = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    config.ensure_dirs(p)
    for d in (p.backups, p.uploads, p.outputs, p.secrets):
        assert d.is_dir()
    if sys.platform != "win32":
        assert stat.S_IMODE(p.secrets.stat().st_mode) == 0o700

def test_env_int_falls_back_on_garbage():
    assert config.env_int({"VJHSTUDIO_PORT": "abc"}, "VJHSTUDIO_PORT", 8080) == 8080
    assert config.env_int({"VJHSTUDIO_PORT": "9000"}, "VJHSTUDIO_PORT", 8080) == 9000
