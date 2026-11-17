"""`vjhstudio console`: the Linux menu behind the desktop link.

The app runs in the background as the systemd user service `vjhstudio` (written by
install.sh); this menu shows whether it is running and starts, stops and restarts it.
It looks and behaves like the RoadTales home server menu: a status table, a
two-column list of choices, a `> ` prompt, the table again after every action.

Everything that talks to systemd goes through a `Runner`, so the tests never touch a
real service.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from . import config

UNIT = "vjhstudio"

# Takes the arguments after `systemctl --user`; returns (return code, what it said).
Runner = Callable[[list[str]], tuple[int, str]]


class NoSystemd(Exception):
    """systemctl is missing, or there is no user session for it to talk to."""


def system_runner(args: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603
            ["systemctl", "--user", *args],  # noqa: S607 — found on PATH, as the installer did
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as e:
        raise NoSystemd() from e
    except subprocess.TimeoutExpired:
        return 1, "systemctl did not answer"
    if "Failed to connect to bus" in (done.stderr or ""):
        raise NoSystemd()
    return done.returncode, (done.stdout or done.stderr or "").strip()


def answers(url: str) -> bool:
    """Does the app answer at this address? Two seconds at most."""
    try:
        return httpx.get(f"{url}api/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def app_url(environ: Mapping[str, str]) -> str:
    port = config.env_int(environ, "VJHSTUDIO_PORT", config.DEFAULT_PORT)
    return f"http://{config.DEFAULT_HOST}:{port}/"


@dataclass(frozen=True)
class Status:
    active: bool
    state: str  # systemd's word: active, inactive, failed, activating ...
    answering: bool
    since: str  # "09:14", or "2026-11-16 09:14" when not today; "" when unknown
    at_login: bool
    update: str
    url: str


def _since(stamp: str) -> str:
    """'Tue 2026-11-17 09:14:02 CET' -> '09:14' today, '2026-11-17 09:14' otherwise."""
    parts = stamp.split()
    if len(parts) < 3:
        return ""
    try:
        day = dt.date.fromisoformat(parts[1])
        clock = dt.time.fromisoformat(parts[2])
    except ValueError:
        return ""
    hhmm = clock.strftime("%H:%M")
    today = dt.date.today()  # noqa: DTZ011 — systemd prints local time
    return hhmm if day == today else f"{day.isoformat()} {hhmm}"


def _update_line(check: Callable[[], dict]) -> str:
    try:
        info = check()
    except Exception:
        return "could not check"
    if info.get("error"):
        return "could not check"
    behind = int(info.get("behind") or 0)
    if not behind:
        return "up to date"
    return f"{behind} update{'' if behind == 1 else 's'} available"


def gather(
    run: Runner, url: str, answers: Callable[[str], bool], check: Callable[[], dict]
) -> Status:
    state = run(["is-active", UNIT])[1] or "unknown"
    active = state == "active"
    since = ""
    if active:
        since = _since(run(["show", UNIT, "-p", "ActiveEnterTimestamp", "--value"])[1])
    return Status(
        active=active,
        state=state,
        # only our own service counts: something else may be using the port
        answering=active and answers(url),
        since=since,
        at_login=run(["is-enabled", UNIT])[1] == "enabled",
        update=_update_line(check),
        url=url,
    )


# (key, choice, label); shown five on the left, five on the right
def _menu(status: Status) -> list[tuple[str, str, str]]:
    return [
        ("1", "start", "Start"),
        ("2", "stop", "Stop"),
        ("3", "restart", "Restart"),
        ("4", "refresh", "Status (refresh)"),
        ("5", "log", "Logs (follow; Ctrl-C comes back here)"),
        ("6", "browser", "Open in browser"),
        ("7", "update", "Update"),
        ("8", "backup", "Backup now"),
        ("9", "login", f"Start at login: turn {'off' if status.at_login else 'on'}"),
        ("0", "quit", "Quit"),
    ]


_KEYS = {key: choice for key, choice, _ in _menu(Status(False, "", False, "", False, "", ""))}


def parse_choice(text: str) -> str | None:
    """A key from the menu; Enter alone refreshes; None for anything else."""
    key = text.strip().lower()
    if key == "":
        return "refresh"
    if key == "q":
        return "quit"
    return _KEYS.get(key)


def render(status: Status, version: str) -> str:
    if status.active:
        word = "running"
        detail = (
            f"{status.url} answering" if status.answering else f"{status.url} not answering yet"
        )
        if status.since:
            detail += f" · since {status.since}"
    else:
        word, detail = "DOWN", status.state
    rows = [
        ("Server", word, detail),
        ("Start at login", "", "on" if status.at_login else "off"),
        ("Update", "", status.update),
    ]
    width = max(len(name) for name, _, _ in rows)
    lines = [f"VJHStudio {version}", ""]
    lines += [f"  {name.ljust(width)}  {word.ljust(8)} {detail}" for name, word, detail in rows]
    lines.append("")
    menu = _menu(status)
    for (lk, _, ll), (rk, _, rl) in zip(menu[:5], menu[5:], strict=True):
        lines.append(f"  {lk}  {ll.ljust(40)}{rk}  {rl}")
    return "\n".join(lines)
