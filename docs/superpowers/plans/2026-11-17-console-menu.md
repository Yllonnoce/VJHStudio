# Console Menu (Linux) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On a Linux install, the desktop link opens a terminal menu that starts, stops and restarts VJHStudio, which runs in the background as a systemd user service.

**Architecture:** A new `vjhstudio console` subcommand (`vjhstudio/console.py`) loops over status, choices and actions; every `systemctl`/`journalctl` call goes through one injectable runner so the logic is tested without systemd. `install.sh` always writes the `vjhstudio` user service on Linux with systemd and points the desktop link at a new `console.sh`.

**Tech Stack:** Python 3 standard library only (argparse, subprocess, urllib, webbrowser), bash, systemd user units, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-console-menu-design.md`

## Global Constraints

- Linux only. `install.bat`, `uninstall.bat`, `start.bat`, the macOS branches of `install.sh`/`uninstall.sh` and `start.sh` are not changed.
- No PowerShell anywhere (`tests/test_installers.py::test_no_powershell_in_any_shipped_script`); add `console.sh` to that test's list.
- No new installer questions and no new installer flags. Installer scripts stay short, with plain-English comments.
- The service name is `vjhstudio`; every call is `systemctl --user ...`. The service file content stays exactly as it is today (`ExecStart="$DIR/start.sh" --no-browser`, `Restart=always`).
- No new dependencies.
- New shell scripts on this NTFS checkout get their executable bit with `git update-index --chmod=+x <file>`.
- No test starts, stops or queries a real service.
- Run tests with `uv run pytest`; lint with `uv run ruff check .`.

## Review Focus

- `systemctl` is missing or the user bus does not answer when the menu is opened: the menu says so in one plain line and exits with code 1, with no traceback. (Task 1)
- The service is `active` but the address does not answer yet (just started): the status line says "not answering yet", not "stopped". (Task 1)
- Unknown or empty input at the prompt (`""`, `"9"`, `"x"`, `" 3 "`): empty refreshes, surrounding spaces are ignored, anything unknown re-shows the menu and does nothing. (Task 1)
- End of input or Ctrl-C at the prompt (window closed, Ctrl-D): the menu exits with code 0 and leaves the service alone. (Task 2)
- The update check fails (offline, zip install): the Update line reads "could not check" and the menu is still usable. (Task 1)

---

### Task 1: Status and choices (pure logic)

**Files:**
- Create: `vjhstudio/console.py`
- Test: `tests/test_console.py`

**Interfaces:**
- Produces:
  - `UNIT = "vjhstudio"`
  - `Runner = Callable[[list[str]], tuple[int, str]]`: takes the arguments after `systemctl --user`, returns `(returncode, stripped stdout)`.
  - `class NoSystemd(Exception)`
  - `system_runner(args: list[str]) -> tuple[int, str]`: runs `systemctl --user *args` with a 15 second timeout; raises `NoSystemd` when `systemctl` is not found.
  - `@dataclass(frozen=True) class Status: active: bool; state: str; answering: bool; since: str; at_login: bool; update: str; url: str`
  - `gather(run: Runner, url: str, answers: Callable[[str], bool], check: Callable[[], dict]) -> Status`
  - `answers(url: str) -> bool`: one GET with a 2 second timeout; any exception is `False`.
  - `app_url(environ: Mapping[str, str]) -> str`
  - `render(status: Status, version: str) -> str`
  - `parse_choice(text: str) -> str | None`

- [ ] **Step 1: Write the failing tests** in `tests/test_console.py`

A fake runner helper: `fake(replies: dict[str, tuple[int, str]])` returns a `Runner` that looks up `" ".join(args)` and records each call in a list.

```python
OK_CHECK = lambda: {"git": True, "current": "abc", "behind": 0, "commits": [], "error": ""}

def test_gather_running_and_answering():
    run = fake({"is-active vjhstudio": (0, "active"),
                "is-enabled vjhstudio": (0, "enabled"),
                "show vjhstudio -p ActiveEnterTimestamp --value": (0, "Tue 2026-11-17 09:14:02 CET")})
    s = console.gather(run, "http://127.0.0.1:8080/", lambda u: True, OK_CHECK)
    assert (s.active, s.answering, s.at_login, s.update) == (True, True, True, "up to date")
    assert "09:14" in s.since

def test_gather_stopped_and_not_enabled():
    run = fake({"is-active vjhstudio": (3, "inactive"), "is-enabled vjhstudio": (1, "disabled")})
    s = console.gather(run, "http://127.0.0.1:8080/", lambda u: False, OK_CHECK)
    assert (s.active, s.state, s.at_login) == (False, "inactive", False)

def test_gather_update_lines():
    run = fake({})
    behind = lambda: {"git": True, "current": "abc", "behind": 3, "commits": [], "error": ""}
    failed = lambda: {"git": True, "current": "", "behind": 0, "commits": [], "error": "offline"}
    def boom(): raise OSError("no git")
    assert console.gather(run, "u", lambda u: False, behind).update == "3 updates available"
    assert console.gather(run, "u", lambda u: False, failed).update == "could not check"
    assert console.gather(run, "u", lambda u: False, boom).update == "could not check"

def test_render_started_but_not_answering_yet():
    s = console.Status(True, "active", False, "09:14", True, "up to date", "http://127.0.0.1:8080/")
    text = console.render(s, "0.5.0")
    assert "VJHStudio 0.5.0" in text
    assert "running" in text and "not answering yet" in text
    assert "8  Start at login: turn off" in text

def test_render_stopped_offers_to_turn_login_start_on():
    s = console.Status(False, "inactive", False, "", False, "up to date", "http://127.0.0.1:8080/")
    text = console.render(s, "0.5.0")
    assert "stopped" in text
    assert "8  Start at login: turn on" in text

@pytest.mark.parametrize("text,want", [
    ("1", "start"), ("2", "stop"), ("3", "restart"), ("4", "browser"), ("5", "log"),
    ("6", "update"), ("7", "backup"), ("8", "login"), ("q", "quit"), ("Q", "quit"),
    (" 3 ", "restart"), ("", "refresh"), ("9", None), ("x", None)])
def test_parse_choice(text, want):
    assert console.parse_choice(text) == want

def test_app_url_uses_the_server_port_setting():
    assert console.app_url({}) == "http://127.0.0.1:8080/"
    assert console.app_url({"VJHSTUDIO_PORT": "8090"}) == "http://127.0.0.1:8090/"

def test_system_runner_without_systemctl(monkeypatch):
    def missing(*a, **k): raise FileNotFoundError("systemctl")
    monkeypatch.setattr(console.subprocess, "run", missing)
    with pytest.raises(console.NoSystemd):
        console.system_runner(["is-active", "vjhstudio"])
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_console.py -q`
Expected: collection error, `vjhstudio.console` does not exist.

- [ ] **Step 3: Implement the interfaces above in `vjhstudio/console.py`**

Decisions the tests do not pin:
- `gather`: a `since` that cannot be read is `""`; show only `HH:MM` when the timestamp is today, otherwise the date too. `update` is `"1 update available"` for exactly one.
- `render`: layout as in the spec's sketch (two columns of choices, `q  Quit` last). Server line: `running · <url> answering · since <since>` / `running · <url> not answering yet` / `stopped` (any non-active state, with the raw state in brackets when it is not `inactive`, e.g. `stopped (failed)`).
- `app_url`: port from `config.env_int(environ, "VJHSTUDIO_PORT", config.DEFAULT_PORT)`, host `config.DEFAULT_HOST`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_console.py -q && uv run ruff check vjhstudio/console.py tests/test_console.py`
Expected: all pass, no lint findings.

- [ ] **Step 5: Commit**

```bash
git add vjhstudio/console.py tests/test_console.py
git commit -m "feat(console): status and choices for the Linux console menu"
```

---

### Task 2: Actions, the menu loop and the `console` subcommand

**Files:**
- Modify: `vjhstudio/console.py`
- Modify: `vjhstudio/main.py` (`build_parser`, new `cmd_console`)
- Test: `tests/test_console.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces:
  - `@dataclass class Deps: run: Runner; url: str; version: str; answers: Callable[[str], bool]; check: Callable[[], dict]; ask: Callable[[str], str]; out: Callable[[str], None]; in_terminal: Callable[[list[str]], int]; open_browser: Callable[[str], object]`
  - `act(choice: str, status: Status, deps: Deps) -> None`
  - `menu(deps: Deps) -> int`
  - `in_terminal(argv: list[str]) -> int`: runs the command with this terminal's stdin/stdout; Ctrl-C ends the command and returns to the caller, never raises.
  - `main.cmd_console(args) -> int`, registered as `console` with help `"start, stop and restart the app from a menu (Linux)"`.

- [ ] **Step 1: Write the failing tests**

A `deps(...)` helper builds `Deps` with a fake runner, a scripted `ask` (pops answers from a list, raises `EOFError` when empty), `out` appending to a list, and `in_terminal` recording its argv and returning 0.

```python
def test_start_stop_restart_call_systemctl():
    for choice in ("start", "stop", "restart"):
        d, calls, _ = deps()
        console.act(choice, STOPPED, d)
        assert [choice, "vjhstudio"] in calls

def test_login_toggle_enables_when_off_and_disables_when_on():
    d, calls, _ = deps()
    console.act("login", STOPPED, d)          # STOPPED.at_login is False
    assert ["enable", "vjhstudio"] in calls
    d, calls, _ = deps()
    console.act("login", RUNNING, d)          # RUNNING.at_login is True
    assert ["disable", "vjhstudio"] in calls

def test_login_toggle_never_stops_or_starts_the_server():
    d, calls, _ = deps()
    console.act("login", RUNNING, d)
    assert not any("--now" in c or c[0] in ("stop", "start") for c in calls)

def test_log_follows_the_journal_in_this_terminal():
    d, _, ran = deps()
    console.act("log", RUNNING, d)
    assert ran == [["journalctl", "--user", "-u", "vjhstudio", "-n", "60", "-f"]]

def test_update_restarts_only_a_running_server():
    d, calls, ran = deps()
    console.act("update", RUNNING, d)
    assert ran[0][-1] == "update" and ["restart", "vjhstudio"] in calls
    d, calls, ran = deps()
    console.act("update", STOPPED, d)
    assert ["restart", "vjhstudio"] not in calls

def test_failed_update_does_not_restart():
    d, calls, _ = deps(in_terminal_code=1)
    console.act("update", RUNNING, d)
    assert ["restart", "vjhstudio"] not in calls

def test_backup_runs_the_backup_command():
    d, _, ran = deps()
    console.act("backup", RUNNING, d)
    assert ran[0][-1] == "backup"

def test_browser_opens_the_app_url():
    opened = []
    d, _, _ = deps(open_browser=opened.append)
    console.act("browser", RUNNING, d)
    assert opened == [RUNNING.url]

def test_a_failing_systemctl_prints_its_message():
    d, _, _ = deps(replies={"start vjhstudio": (1, "Unit vjhstudio.service not found.")})
    console.act("start", STOPPED, d)
    assert any("not found" in line for line in d.printed)

def test_menu_quits_on_q_and_on_end_of_input_without_touching_the_service():
    for answers in (["q"], []):                # [] makes ask raise EOFError
        d, calls, _ = deps(answers=answers)
        assert console.menu(d) == 0
        assert not any(c[0] in ("start", "stop", "restart") for c in calls)

def test_menu_quits_on_ctrl_c_at_the_prompt():
    d, _, _ = deps(ask_raises=KeyboardInterrupt)
    assert console.menu(d) == 0

def test_menu_ignores_unknown_input_then_acts():
    d, calls, _ = deps(answers=["9", "x", "3", "q"])
    assert console.menu(d) == 0
    assert calls.count(["restart", "vjhstudio"]) == 1

def test_menu_without_systemd_says_so_and_returns_1():
    def no(args): raise console.NoSystemd()
    d, _, _ = deps(run=no)
    assert console.menu(d) == 1
    assert any("systemd" in line for line in d.printed)
```

In `tests/test_cli.py`:

```python
def test_console_command_is_registered():
    args = main.build_parser().parse_args(["console"])
    assert args.func is main.cmd_console
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_console.py tests/test_cli.py::test_console_command_is_registered -q`
Expected: FAIL, `act`, `menu`, `Deps` and `cmd_console` are missing.

- [ ] **Step 3: Implement**

- `act`: the update and backup commands are `[sys.executable, str(REPO_ROOT / "run.py"), "update"]` and `[..., "backup"]` (already inside the app's environment, so no second `uv run`). After start/stop/restart/enable/disable, a non-zero return prints the runner's text; `out` gets one short confirmation line otherwise.
- `menu`: loop of `gather` → `out(render(...))` → `ask("> ")` → `parse_choice` → `act`. `None` and `"refresh"` just loop. `EOFError` and `KeyboardInterrupt` from `ask` return 0. `NoSystemd` from any call prints `"This menu needs systemd, which this computer does not seem to have. Start the app with ./start.sh instead."` and returns 1.
- `system_runner` treats "Failed to connect to bus" on stderr as `NoSystemd` too.
- `cmd_console` in `main.py`: builds `Deps` with the real pieces (`console.system_runner`, `console.app_url(os.environ)`, `__version__`, `console.answers`, `update.check_updates`, `input`, `print`, `console.in_terminal`, `webbrowser.open`) and returns `console.menu(deps)`.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass, no lint findings.

- [ ] **Step 5: Commit**

```bash
git add vjhstudio/console.py vjhstudio/main.py tests/test_console.py tests/test_cli.py
git commit -m "feat(console): menu loop, actions and the console subcommand"
```

---

### Task 3: `console.sh`, the installer, the uninstaller and the README

**Files:**
- Create: `console.sh`
- Modify: `install.sh` (steps 7, 8 and 10, Linux branches only)
- Modify: `uninstall.sh` (step 2, Linux branch only)
- Modify: `README.md` (the two-questions paragraph near line 108, "use the app again" near line 224, the uninstall paragraph near line 411)
- Modify: `CHANGELOG.md` (one entry under the unreleased heading)
- Test: `tests/test_installers.py`

**Interfaces:**
- Consumes: `python run.py console` from Task 2.

- [ ] **Step 1: Write the failing tests** in `tests/test_installers.py`

Add `"console.sh"` to the list in `test_no_powershell_in_any_shipped_script` and to the parametrize lists of `test_shell_scripts_parse` and `test_shell_scripts_are_executable`. Then:

```python
def test_console_sh_opens_the_menu_and_keeps_an_error_readable():
    text = read("console.sh")
    assert "run.py console" in text
    assert "--frozen" in text
    assert "Press Enter to close" in text


def test_install_sh_always_writes_the_service_on_linux_with_systemd():
    text = read("install.sh")
    assert "systemctl --user show-environment" in text      # the "is systemd here?" check
    assert "systemctl --user enable --now vjhstudio" in text   # answered yes
    assert "systemctl --user start vjhstudio" in text          # answered no: start once
    assert text.count("vjhstudio.service") == 1                # still written in one place


def test_install_sh_desktop_link_opens_the_menu_when_systemd_is_there():
    text = read("install.sh")
    assert "console.sh" in text
    assert 'Exec="$DIR/start.sh"' in text    # the fallback without systemd is still there


def test_install_sh_still_asks_only_the_two_questions():
    assert read("install.sh").count("read -r -p") == 2


def test_uninstall_sh_removes_the_linux_service_even_without_start_at_login():
    """The service file now exists on every Linux install with systemd, so its removal
    must not depend on the "service" answer written in install.json."""
    text = read("uninstall.sh")
    # the Linux removal is guarded by the file being there, not by the install.json answer
    guard = 'if [ -f "${HOME:-}/.config/systemd/user/vjhstudio.service" ]; then'
    assert guard in text
    assert text.index(guard) < text.index("systemctl --user disable --now")
    # and the macOS removal is the only thing left behind the SERVICE answer
    block = text[text.index('if [ "$SERVICE" = "yes" ]'):text.index(guard)]
    assert "launchctl unload" in block and "systemctl" not in block
```

The existing `test_uninstall_sh_stops_the_autostart_entry_before_asking_the_app_to_quit` and `test_install_sh_quotes_the_service_exec_start` must keep passing unchanged.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_installers.py -q`
Expected: the new tests FAIL (`console.sh` missing, strings absent).

- [ ] **Step 3: Write `console.sh`**

Same shape as `start.sh` (a `main` function, `set -u`, `cd` to its own folder, find `uv` the same three ways but do not install it). Runs `"$UV" run --frozen python run.py console`; when that returns non-zero, prints an empty line, then `read -r -p "Press Enter to close" _`. Then:

```bash
git add console.sh && git update-index --chmod=+x console.sh
```

- [ ] **Step 4: Change `install.sh` (Linux branches only)**

- Before step 7, one new variable: `SYSTEMD="no"`, set to `"yes"` when `OS` is `linux` and `systemctl --user show-environment >/dev/null 2>&1` succeeds.
- Step 7, Linux: run when `SYSTEMD` is `yes` (not when `SERVICE` is `yes`). Write the same file, `daemon-reload`, then `enable --now` if `SERVICE` is `yes`, else `systemctl --user start vjhstudio` unless `START` is `no`. Keep the linger tip only for `SERVICE=yes`.
- Step 8, Linux: `Exec="$DIR/console.sh"` and `Comment=Start, stop and restart VJHStudio` when `SYSTEMD` is `yes`; today's `Exec="$DIR/start.sh"` otherwise. One heredoc with a variable for the two values, not two heredocs.
- Step 10: on Linux with `SYSTEMD=yes` the app is already running in the background, so print the address and, when the desktop link was made, the line `Use the VJHStudio link on your desktop to start, stop or restart it.`; never `exec ./start.sh` in that case. Everything else unchanged.
- The macOS branches and `data/install.json` are not touched.

- [ ] **Step 5: Change `uninstall.sh` step 2**

Split into two blocks, macOS first: the macOS removal stays behind `if [ "$SERVICE" = "yes" ]` (with the `uname` test inside it); then a separate block, `if [ -f "${HOME:-}/.config/systemd/user/vjhstudio.service" ]; then`, does `disable --now`, deletes the file and runs `daemon-reload`. Both stay before the `/api/shutdown` step.

- [ ] **Step 6: README and CHANGELOG**

- Near line 108, add after the two-questions paragraph: on Linux the desktop link opens a small menu where you start, stop and restart VJHStudio; the app keeps running when you close that window.
- Near line 224, give Linux its own bullet: double-click the VJHStudio link on your desktop, or open a terminal, `cd ~/VJHStudio`, `./console.sh`.
- Uninstall paragraph: on Linux it also removes the background service.
- CHANGELOG: one line, "Linux: the desktop link opens a console menu (start, stop, restart, log, update, backup); the app runs as a background service."

- [ ] **Step 7: Run everything**

Run: `uv run pytest -q && uv run ruff check . && bash -n console.sh install.sh uninstall.sh`
Expected: all pass, no output from `bash -n`.

- [ ] **Step 8: Try the menu for real, with no service installed**

Do not install a service on this machine. Run the menu as it is and confirm: `uv run python run.py console` shows `Server    stopped`, choice `1` prints systemd's "not found" message without a traceback, `q` exits 0. Report exactly what was and was not exercised; a full install on this machine is the owner's call because port 8080 is used by their debug run.

- [ ] **Step 9: Commit**

```bash
git add console.sh install.sh uninstall.sh README.md CHANGELOG.md tests/test_installers.py
git commit -m "feat(install): Linux desktop link opens the console menu; service always installed"
```
