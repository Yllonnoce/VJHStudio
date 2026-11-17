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
    every = SCRIPTS + ("start.sh", "start.bat", "console.sh", "scripts/restart_helper.bat")
    for name in every:
        text = read(name).lower()
        assert "powershell" not in text, name
        assert ".ps1" not in text, name


@pytest.mark.parametrize("name", ("install.sh", "uninstall.sh", "console.sh"))
def test_shell_scripts_parse(name):
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    done = subprocess.run(["bash", "-n", str(REPO_ROOT / name)], capture_output=True)
    assert done.returncode == 0, done.stderr.decode(errors="replace")


@pytest.mark.parametrize("name", ("install.sh", "uninstall.sh", "console.sh"))
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


def test_install_sh_asks_the_questions_even_when_piped_into_bash():
    """The documented command is `curl … | bash`, so stdin is the download, not the
    keyboard. Both questions must therefore read from the terminal itself."""
    text = read("install.sh")
    assert "[ -r /dev/tty ]" in text
    assert text.count("ANSWER </dev/tty") == 2


def test_install_sh_quotes_the_service_exec_start():
    """An install folder with a space in it would otherwise make systemd run the
    wrong command."""
    assert 'ExecStart="$DIR/start.sh" --no-browser' in read("install.sh")


def test_uninstall_sh_stops_the_autostart_entry_before_asking_the_app_to_quit():
    """systemd's Restart=always and launchd's KeepAlive=true relaunch the app the
    moment it exits, and the relaunched process would then lose its .venv."""
    text = read("uninstall.sh")
    shutdown = text.index("/api/shutdown")
    assert text.index("systemctl --user disable --now") < shutdown
    assert text.index("launchctl unload") < shutdown


def test_uninstall_sh_purge_honours_the_data_dir_env_var():
    text = read("uninstall.sh")
    assert "${VJHSTUDIO_DATA_DIR:-$DIR/data}" in text
    assert 'rm -rf "$DIR/data"' not in text


# --- the Linux console menu: console.sh, the background service, the desktop link ---


def test_console_sh_opens_the_menu_and_keeps_an_error_readable():
    text = read("console.sh")
    assert 'main "$@"' in text
    assert "set -u" in text
    assert "run.py console" in text
    assert "--frozen" in text
    assert "Press Enter to close" in text


def test_install_sh_always_writes_the_service_on_linux_with_systemd():
    text = read("install.sh")
    assert "systemctl --user show-environment" in text  # the "is systemd here?" check
    assert "systemctl --user enable --now vjhstudio" in text  # answered yes
    assert "systemctl --user start vjhstudio" in text  # answered no: start it once
    assert text.count("vjhstudio.service") == 1  # still written in one place
    # the Linux service is written because systemd is there, not because of the answer
    assert 'if [ "$SYSTEMD" = "yes" ]; then' in text
    assert 'if [ "$SERVICE" = "yes" ] && [ "$OS" = "linux" ]' not in text


def test_install_sh_desktop_link_opens_the_menu_when_systemd_is_there():
    text = read("install.sh")
    assert 'LINK_EXEC="$DIR/console.sh"' in text
    assert 'LINK_EXEC="$DIR/start.sh"' in text  # without systemd: as before
    assert 'Exec="$LINK_EXEC"' in text
    assert text.count("[Desktop Entry]") == 1  # one link text, not two


def test_install_sh_still_asks_only_the_two_questions():
    assert read("install.sh").count("read -r -p") == 2


def test_install_sh_never_runs_a_second_copy_next_to_the_service():
    """With the service running in the background, ending the installer with
    start.sh would start a second server."""
    text = read("install.sh")
    tail = text[text.index("# --- Step 10") :]
    assert '[ "$SYSTEMD" = "yes" ]' in tail
    assert tail.index('[ "$SYSTEMD" = "yes" ]') < tail.index("exec ./start.sh")


def test_uninstall_sh_removes_the_linux_service_even_without_start_at_login():
    """The service file now exists on every Linux install with systemd, so its removal
    must not depend on the "service" answer written in install.json."""
    text = read("uninstall.sh")
    guard = 'if [ -f "${HOME:-}/.config/systemd/user/vjhstudio.service" ]; then'
    assert guard in text
    assert text.index(guard) < text.index("systemctl --user disable --now")
    # and the macOS removal is the only thing left behind the SERVICE answer
    block = text[text.index('if [ "$SERVICE" = "yes" ]') : text.index(guard)]
    assert "launchctl unload" in block and "systemctl" not in block


def test_install_sh_says_so_when_start_at_login_cannot_be_set_up():
    """Linux without systemd: start-at-login was answered yes but cannot be done. The
    installer must say so and start the app the ordinary way, not point at a page
    nothing serves."""
    text = read("install.sh")
    assert "Start at login needs systemd" in text
    block = text[text.index("Start at login needs systemd") :][:200]
    assert 'SERVICE="no"' in block


def test_install_sh_service_waits_before_starting_again():
    """start.sh exits at once when another copy already has the port; without a pause
    systemd would start it again many times a second."""
    assert "RestartSec=5" in read("install.sh")
