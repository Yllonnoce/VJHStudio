"""Static checks for the installers and uninstallers.

The scripts are never executed here: Windows batch files cannot run in CI at all,
and the shell scripts would install software. We check their text and, for the
shell scripts, that bash can parse them (`bash -n`).
"""

import shutil
import stat
import subprocess
import sys

import pytest

from vjhstudio.config import REPO_ROOT

SCRIPTS = ("install.sh", "install.bat", "uninstall.sh", "uninstall.bat")
QUESTION_SERVICE = "Start VJHStudio automatically when you log in? [y/N]"
QUESTION_DESKTOP = "Create a desktop link? [y/N]"


def read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def test_all_four_scripts_exist():
    for name in SCRIPTS:
        assert (REPO_ROOT / name).exists(), name


def test_no_powershell_in_any_shipped_script():
    every = SCRIPTS + ("start.sh", "start.bat", "scripts/restart_helper.bat")
    for name in every:
        text = read(name).lower()
        assert "powershell" not in text, name
        assert ".ps1" not in text, name


@pytest.mark.parametrize("name", ("install.sh", "uninstall.sh"))
def test_shell_scripts_parse(name):
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    done = subprocess.run(["bash", "-n", str(REPO_ROOT / name)], capture_output=True)
    assert done.returncode == 0, done.stderr.decode(errors="replace")


@pytest.mark.parametrize("name", ("install.sh", "uninstall.sh"))
def test_shell_scripts_are_executable(name):
    if sys.platform == "win32":
        pytest.skip("no executable bit on Windows")
    mode = (REPO_ROOT / name).stat().st_mode
    assert stat.S_IMODE(mode) & stat.S_IXUSR, name


def test_install_sh_structure():
    text = read("install.sh")
    assert 'main "$@"' in text
    assert "set -u" in text
    assert "uv sync --frozen" in text
    assert "astral.sh/uv" in text
    assert "--ff-only" in text
    assert "systemctl --user" in text
    assert "launchctl" in text
    assert "install.json" in text


def test_install_sh_uses_guarded_home_under_set_u():
    text = read("install.sh")
    assert "$HOME/" not in text and '"$HOME"' not in text, "use ${HOME:-} under set -u"
    assert "${HOME:-}" in text


def test_install_sh_asks_the_two_questions_once_each():
    text = read("install.sh")
    assert text.count("automatically when you log in") == 1
    assert text.count("desktop link") == 1
    assert QUESTION_SERVICE in text
    assert QUESTION_DESKTOP in text


def test_install_sh_has_the_documented_flags():
    text = read("install.sh")
    for flag in (
        "--dir",
        "--branch",
        "--no-start",
        "--service",
        "--no-service",
        "--desktop",
        "--no-desktop",
    ):
        assert flag in text, flag


def test_install_bat_structure():
    text = read("install.bat")
    for needle in (
        "schtasks /Create /SC ONLOGON",
        "/TN VJHStudio",
        "curl.exe",
        "tar.exe",
        "cscript",
        ".vbs",
        ".url",
        "uv sync --frozen",
        "install.json",
        "@echo off",
    ):
        assert needle in text, needle


def test_install_bat_runs_from_temp_not_from_the_checkout():
    text = read("install.bat")
    assert "%TEMP%" in text
    assert "%~dp0" not in text, "install.bat must not run out of the checkout it updates"


@pytest.mark.parametrize("name", ("uninstall.sh", "uninstall.bat"))
def test_uninstall_scripts_stop_the_app_and_clean_up(name):
    text = read(name)
    assert "/api/shutdown" in text
    assert "install.json" in text
    assert ".venv" in text
    assert "DELETE" in text
    assert ("--purge" if name.endswith(".sh") else "/purge") in text


def test_uninstall_scripts_remove_the_autostart_entry():
    assert "systemctl --user disable" in read("uninstall.sh")
    assert "launchctl" in read("uninstall.sh")
    assert "schtasks /Delete" in read("uninstall.bat")


def test_uninstall_scripts_never_delete_a_whole_home_folder():
    sh = read("uninstall.sh")
    assert 'rm -rf "$HOME"' not in sh
    assert 'rm -rf "${HOME:-}"' not in sh
    bat = read("uninstall.bat").lower()
    assert "rmdir /s /q %userprofile%" not in bat


def test_install_json_shape_is_documented_in_both_installers():
    for name in ("install.sh", "install.bat"):
        text = read(name)
        for key in ('"home"', '"service"', '"desktop"', '"os"'):
            assert key in text, f"{name}: {key}"
