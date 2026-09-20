import os, stat, sys
from vjhstudio.config import REPO_ROOT


def test_scripts_exist_and_have_no_powershell():
    for name in ("start.sh", "start.bat", "scripts/restart_helper.bat", "run.py"):
        p = REPO_ROOT / name
        assert p.exists(), name
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        assert "powershell" not in text and ".ps1" not in text, name


def test_start_sh_loops_on_75_and_is_executable():
    p = REPO_ROOT / "start.sh"
    text = p.read_text()
    assert "VJHSTUDIO_LAUNCHER=1" in text and "75" in text and 'main "$@"' in text
    if sys.platform != "win32":
        assert stat.S_IMODE(p.stat().st_mode) & stat.S_IXUSR


def test_start_bat_runs_from_temp_copy():
    text = (REPO_ROOT / "start.bat").read_text()
    assert "%TEMP%" in text and "errorlevel 75" in text.replace("ERRORLEVEL", "errorlevel") and "VJHSTUDIO_LAUNCHER=1" in text


def test_launchers_bootstrap_uv_if_missing():
    for name in ("start.sh", "start.bat"):
        text = (REPO_ROOT / name).read_text()
        assert "astral.sh/uv" in text or "astral-sh/uv" in text, name


def test_start_sh_survives_an_unset_home_under_set_u():
    text = (REPO_ROOT / "start.sh").read_text()
    assert "set -u" in text
    assert "$HOME/" not in text and '"$HOME"' not in text, "use ${HOME:-} under set -u"
    assert "${HOME:-}" in text
