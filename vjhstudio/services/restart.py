"""Process restart/shutdown. The exit happens on a timer so the HTTP reply gets out first."""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from ..config import REPO_ROOT, RESTART_EXIT_CODE

_timers: list[threading.Timer] = []


def _is_windows() -> bool:
    return os.name == "nt"


def _exit(code: int) -> None:
    os._exit(code)


def _execv(argv: list[str]) -> None:
    # Re-exec ourselves: argv[0] is sys.executable, no shell involved.
    os.execv(argv[0], argv)  # noqa: S606


def _spawn_helper(pid: int) -> None:
    helper = REPO_ROOT / "scripts" / "restart_helper.bat"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    # Fixed argv; cmd.exe is resolved from PATH because Windows always has it there.
    subprocess.Popen(  # noqa: S603
        ["cmd.exe", "/c", str(helper), str(pid)],  # noqa: S607
        cwd=REPO_ROOT,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _schedule(delay: float, fn) -> None:
    t = threading.Timer(delay, fn)
    t.daemon = True
    _timers.append(t)
    t.start()


def _pending_join() -> None:
    """Test helper: wait for scheduled exits to run."""
    for t in _timers:
        t.join(5)
    _timers.clear()


def request_restart(delay: float = 1.5) -> str:
    if os.environ.get("VJHSTUDIO_LAUNCHER") == "1":
        _schedule(delay, lambda: _exit(RESTART_EXIT_CODE))
        return "launcher"
    if _is_windows():
        pid = os.getpid()

        def _go():
            _spawn_helper(pid)
            _exit(0)

        _schedule(delay, _go)
        return "windows-helper"
    argv = [sys.executable, *sys.argv]
    if "--open" in argv:
        argv[argv.index("--open")] = "--no-browser"
    _schedule(delay, lambda: _execv(argv))
    return "execv"


def request_shutdown(delay: float = 1.0) -> None:
    _schedule(delay, lambda: _exit(0))
