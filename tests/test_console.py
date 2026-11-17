"""The Linux console menu (`vjhstudio console`). No test touches a real service:
every `systemctl --user ...` call goes through a fake runner."""

import pytest

from vjhstudio import console

URL = "http://127.0.0.1:8080/"
RUNNING = console.Status(True, "active", True, "09:14", True, "up to date", URL)
STOPPED = console.Status(False, "inactive", False, "", False, "up to date", URL)


def ok_check() -> dict:
    return {"git": True, "current": "abc", "behind": 0, "commits": [], "error": ""}


def fake(replies: dict | None = None):
    """A runner that answers from `replies` (keyed by the joined arguments) and
    remembers every call."""
    calls: list[list[str]] = []

    def run(args: list[str]) -> tuple[int, str]:
        calls.append(list(args))
        return (replies or {}).get(" ".join(args), (0, ""))

    return run, calls


# --- status ---------------------------------------------------------------------


def test_gather_running_and_answering():
    run, _ = fake(
        {
            "is-active vjhstudio": (0, "active"),
            "is-enabled vjhstudio": (0, "enabled"),
            "show vjhstudio -p ActiveEnterTimestamp --value": (0, "Tue 2026-11-17 09:14:02 CET"),
        }
    )
    s = console.gather(run, URL, lambda u: True, ok_check)
    assert (s.active, s.answering, s.at_login, s.update) == (True, True, True, "up to date")
    assert "09:14" in s.since
    assert s.url == URL


def test_gather_stopped_and_not_enabled():
    run, _ = fake({"is-active vjhstudio": (3, "inactive"), "is-enabled vjhstudio": (1, "disabled")})
    s = console.gather(run, URL, lambda u: False, ok_check)
    assert (s.active, s.state, s.at_login) == (False, "inactive", False)


def test_gather_does_not_ask_the_address_when_the_service_is_stopped():
    """Something else may be on the port (a debug run); that is not our service."""
    run, _ = fake({"is-active vjhstudio": (3, "inactive")})
    asked = []
    s = console.gather(run, URL, lambda u: asked.append(u) or True, ok_check)
    assert asked == [] and s.answering is False


def test_gather_update_lines():
    run, _ = fake()

    def behind(n):
        return lambda: {"git": True, "current": "abc", "behind": n, "commits": [], "error": ""}

    def failed():
        return {"git": True, "current": "", "behind": 0, "commits": [], "error": "offline"}

    def boom():
        raise OSError("no git")

    assert console.gather(run, URL, lambda u: False, behind(3)).update == "3 updates available"
    assert console.gather(run, URL, lambda u: False, behind(1)).update == "1 update available"
    assert console.gather(run, URL, lambda u: False, failed).update == "could not check"
    assert console.gather(run, URL, lambda u: False, boom).update == "could not check"


def test_gather_survives_an_unreadable_start_time():
    run, _ = fake(
        {
            "is-active vjhstudio": (0, "active"),
            "show vjhstudio -p ActiveEnterTimestamp --value": (0, "n/a"),
        }
    )
    assert console.gather(run, URL, lambda u: True, ok_check).since == ""


# --- the screen (same shape as the RoadTales home server menu) -------------------


def test_render_running():
    lines = console.render(RUNNING, "0.5.0").splitlines()
    assert lines[0] == "VJHStudio 0.5.0"
    server = next(line for line in lines if line.startswith("  Server"))
    assert "running" in server and f"{URL} answering" in server and "since 09:14" in server


def test_render_started_but_not_answering_yet():
    s = console.Status(True, "active", False, "09:14", True, "up to date", URL)
    server = next(x for x in console.render(s, "0.5.0").splitlines() if x.startswith("  Server"))
    assert "running" in server and "not answering yet" in server
    assert "DOWN" not in server


def test_render_stopped_says_down_with_the_state():
    server = next(
        x for x in console.render(STOPPED, "0.5.0").splitlines() if x.startswith("  Server")
    )
    assert "DOWN" in server and "inactive" in server


def test_render_status_table_lines_up():
    rows = [
        x
        for x in console.render(RUNNING, "0.5.0").splitlines()
        if x.startswith(("  Server", "  Start at login", "  Update"))
    ]
    assert len(rows) == 3
    # the detail column starts at the same place on every row
    assert rows[0].index("http") == rows[1].index("on") == rows[2].index("up to date")


def test_render_menu_is_two_columns_one_to_nine_then_zero():
    text = console.render(RUNNING, "0.5.0")
    for left, right in (
        ("1  Start", "6  Open in browser"),
        ("2  Stop", "7  Update"),
        ("3  Restart", "8  Backup now"),
        ("4  Status (refresh)", "9  Start at login: turn off"),
        ("5  Logs (follow; Ctrl-C comes back here)", "0  Quit"),
    ):
        line = next(x for x in text.splitlines() if x.startswith(f"  {left}"))
        assert line.endswith(right)
        assert line.index(right) == 45  # two spaces, the key, two spaces, a 40-wide label


def test_render_offers_to_turn_login_start_on_when_it_is_off():
    assert "9  Start at login: turn on" in console.render(STOPPED, "0.5.0")


@pytest.mark.parametrize(
    "text,want",
    [
        ("1", "start"),
        ("2", "stop"),
        ("3", "restart"),
        ("4", "refresh"),
        ("5", "log"),
        ("6", "browser"),
        ("7", "update"),
        ("8", "backup"),
        ("9", "login"),
        ("0", "quit"),
        ("q", "quit"),
        ("Q", "quit"),
        (" 3 ", "restart"),
        ("", "refresh"),
        ("10", None),
        ("x", None),
    ],
)
def test_parse_choice(text, want):
    assert console.parse_choice(text) == want


def test_app_url_uses_the_server_port_setting():
    assert console.app_url({}) == "http://127.0.0.1:8080/"
    assert console.app_url({"VJHSTUDIO_PORT": "8090"}) == "http://127.0.0.1:8090/"


# --- talking to systemd ----------------------------------------------------------


def test_system_runner_without_systemctl(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(console.subprocess, "run", missing)
    with pytest.raises(console.NoSystemd):
        console.system_runner(["is-active", "vjhstudio"])


def test_system_runner_without_a_user_bus(monkeypatch):
    class Done:
        returncode = 1
        stdout = ""
        stderr = "Failed to connect to bus: No medium found"

    monkeypatch.setattr(console.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(console.NoSystemd):
        console.system_runner(["is-active", "vjhstudio"])


def test_system_runner_returns_code_and_words(monkeypatch):
    seen = {}

    class Done:
        returncode = 3
        stdout = "inactive\n"
        stderr = ""

    def run(argv, **k):
        seen["argv"] = argv
        return Done()

    monkeypatch.setattr(console.subprocess, "run", run)
    assert console.system_runner(["is-active", "vjhstudio"]) == (3, "inactive")
    assert seen["argv"] == ["systemctl", "--user", "is-active", "vjhstudio"]


def test_answers_is_false_when_nothing_listens():
    assert console.answers("http://127.0.0.1:9/") is False


# --- actions and the menu loop ----------------------------------------------------


class Kit:
    """Deps with everything faked, plus what was called, run and printed."""

    def __init__(
        self,
        replies=None,
        answers=None,
        ask_raises=None,
        in_terminal_code=0,
        run=None,
    ):
        fake_run, self.calls = fake(replies)
        self.ran: list[list[str]] = []
        self.printed: list[str] = []
        self.opened: list[str] = []
        pending = list(answers or [])

        def ask(prompt: str) -> str:
            if ask_raises is not None:
                raise ask_raises()
            if not pending:
                raise EOFError
            return pending.pop(0)

        def in_terminal(argv: list[str]) -> int:
            self.ran.append(list(argv))
            return in_terminal_code

        self.deps = console.Deps(
            run=run or fake_run,
            url=URL,
            version="0.5.0",
            answers=lambda u: False,
            check=ok_check,
            ask=ask,
            out=self.printed.append,
            in_terminal=in_terminal,
            open_browser=self.opened.append,
        )


@pytest.mark.parametrize("choice", ["start", "stop", "restart"])
def test_start_stop_restart_call_systemctl(choice):
    k = Kit(replies={"is-active vjhstudio": (0, "active")})
    console.act(choice, STOPPED, k.deps)
    assert [choice, "vjhstudio"] in k.calls
    # the RoadTales result line: two spaces, the verb, the unit, what came of it
    assert f"  {choice} vjhstudio: active" in k.printed


def test_login_toggle_enables_when_off_and_disables_when_on():
    k = Kit()
    console.act("login", STOPPED, k.deps)
    assert ["enable", "vjhstudio"] in k.calls
    k = Kit()
    console.act("login", RUNNING, k.deps)
    assert ["disable", "vjhstudio"] in k.calls


def test_login_toggle_never_stops_or_starts_the_server():
    k = Kit()
    console.act("login", RUNNING, k.deps)
    assert not any("--now" in c or c[0] in ("stop", "start", "restart") for c in k.calls)


def test_log_follows_the_journal_in_this_terminal():
    k = Kit()
    console.act("log", RUNNING, k.deps)
    assert k.ran == [["journalctl", "--user", "-u", "vjhstudio", "-n", "60", "-f"]]


def test_update_restarts_only_a_running_server():
    k = Kit()
    console.act("update", RUNNING, k.deps)
    assert k.ran[0][-1] == "update" and ["restart", "vjhstudio"] in k.calls
    k = Kit()
    console.act("update", STOPPED, k.deps)
    assert k.ran[0][-1] == "update" and ["restart", "vjhstudio"] not in k.calls


def test_failed_update_does_not_restart():
    k = Kit(in_terminal_code=1)
    console.act("update", RUNNING, k.deps)
    assert ["restart", "vjhstudio"] not in k.calls


def test_backup_runs_the_backup_command():
    k = Kit()
    console.act("backup", RUNNING, k.deps)
    assert k.ran[0][-1] == "backup"
    assert k.ran[0][-2].endswith("run.py")


def test_browser_opens_the_app_url():
    k = Kit()
    console.act("browser", RUNNING, k.deps)
    assert k.opened == [URL]


def test_a_failing_systemctl_prints_its_message():
    k = Kit(replies={"start vjhstudio": (5, "Failed to start vjhstudio.service: Unit not found.")})
    console.act("start", STOPPED, k.deps)
    assert any("Unit not found" in line for line in k.printed)


@pytest.mark.parametrize("answers", [["q"], ["0"], []])  # [] is end of input
def test_menu_quits_without_touching_the_service(answers):
    k = Kit(answers=answers)
    assert console.menu(k.deps) == 0
    assert not any(c[0] in ("start", "stop", "restart", "enable", "disable") for c in k.calls)


def test_menu_quits_on_ctrl_c_at_the_prompt():
    k = Kit(ask_raises=KeyboardInterrupt)
    assert console.menu(k.deps) == 0


def test_menu_ignores_unknown_input_then_acts():
    k = Kit(answers=["10", "x", "3", "q"])
    assert console.menu(k.deps) == 0
    assert k.calls.count(["restart", "vjhstudio"]) == 1


def test_menu_shows_the_table_again_after_every_answer():
    k = Kit(answers=["", "4", "q"])
    console.menu(k.deps)
    assert sum(1 for line in k.printed if line.startswith("VJHStudio 0.5.0")) == 3


def test_menu_without_systemd_says_so_and_returns_1():
    def no(args):
        raise console.NoSystemd()

    k = Kit(run=no)
    assert console.menu(k.deps) == 1
    assert any("systemd" in line for line in k.printed)


def test_menu_checks_for_updates_once_not_on_every_refresh():
    """The check talks to the network; the table must not wait for it each time."""
    k = Kit(answers=["", "4", "3", "q"])
    seen = []
    k.deps.check = lambda: seen.append(1) or ok_check()
    console.menu(k.deps)
    assert len(seen) == 1


def test_menu_checks_for_updates_again_after_an_update():
    k = Kit(answers=["7", "q"])
    seen = []
    k.deps.check = lambda: seen.append(1) or ok_check()
    console.menu(k.deps)
    assert len(seen) == 2
