import stat
import sys

import pytest

from vjhstudio import config, secrets


@pytest.fixture
def paths(tmp_path):
    p = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path)})
    config.ensure_dirs(p)
    return p


def test_round_trip_and_mode(paths):
    secrets.write_api_key(paths, "  abcdef1234  ")
    assert secrets.read_api_key(paths) == "abcdef1234"
    if sys.platform != "win32":
        assert stat.S_IMODE(paths.api_key_file.stat().st_mode) == 0o600


def test_env_overrides_file(paths):
    secrets.write_api_key(paths, "filekey")
    assert secrets.effective_api_key(paths, env={"RUNWARE_API_KEY": "envkey"}) == "envkey"
    assert secrets.key_source(paths, env={"RUNWARE_API_KEY": "envkey"}) == "env"
    assert secrets.key_source(paths, env={}) == "file"


def test_missing_key(paths):
    assert secrets.read_api_key(paths) is None
    assert secrets.effective_api_key(paths, env={}) is None
    assert secrets.key_source(paths, env={}) == "none"


def test_clear_and_empty_rejected(paths):
    secrets.write_api_key(paths, "k")
    secrets.clear_api_key(paths)
    assert secrets.read_api_key(paths) is None
    with pytest.raises(ValueError):
        secrets.write_api_key(paths, "   ")


def test_mask():
    assert secrets.mask("abcdef1234") == "••••••••1234"
    assert secrets.mask("ab") == "••••••••"
    assert secrets.mask(None) == ""


def test_write_api_key_creates_an_owner_only_secrets_dir(tmp_path):
    """write_api_key can run before ensure_dirs(), which is the other 0700 site."""
    p = config.resolve_paths(env={"VJHSTUDIO_DATA_DIR": str(tmp_path / "fresh")})
    assert not p.secrets.exists()
    secrets.write_api_key(p, "abcdef1234")
    assert secrets.read_api_key(p) == "abcdef1234"
    if sys.platform != "win32":
        assert stat.S_IMODE(p.secrets.stat().st_mode) == 0o700
