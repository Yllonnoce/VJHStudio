import sys

from vjhstudio.services import restart


def test_launcher_strategy(monkeypatch):
    called = {}
    monkeypatch.setenv("VJHSTUDIO_LAUNCHER", "1")
    monkeypatch.setattr(restart, "_exit", lambda code: called.setdefault("code", code))
    assert restart.request_restart(delay=0) == "launcher"
    restart._pending_join()
    assert called["code"] == 75


def test_windows_helper_strategy(monkeypatch):
    monkeypatch.delenv("VJHSTUDIO_LAUNCHER", raising=False)
    monkeypatch.setattr(restart, "_is_windows", lambda: True)
    spawned, exits = [], []
    monkeypatch.setattr(restart, "_spawn_helper", lambda pid: spawned.append(pid))
    monkeypatch.setattr(restart, "_exit", lambda code: exits.append(code))
    assert restart.request_restart(delay=0) == "windows-helper"
    restart._pending_join()
    assert spawned and exits == [0]


def test_execv_strategy(monkeypatch):
    monkeypatch.delenv("VJHSTUDIO_LAUNCHER", raising=False)
    monkeypatch.setattr(restart, "_is_windows", lambda: False)
    argv = []
    monkeypatch.setattr(restart, "_execv", lambda a: argv.extend(a))
    assert restart.request_restart(delay=0) == "execv"
    restart._pending_join()
    assert argv[0] == sys.executable


def test_execv_reruns_the_package_as_a_module(monkeypatch):
    """`python -m vjhstudio.main` and the VS Code debugger both leave sys.argv[0] as the
    path of main.py; re-executing that path as a script breaks on the package's relative
    imports. The restart must go through `-m vjhstudio.main` whatever argv[0] was."""
    monkeypatch.delenv("VJHSTUDIO_LAUNCHER", raising=False)
    monkeypatch.setattr(restart, "_is_windows", lambda: False)
    monkeypatch.setattr(
        sys, "argv", ["/somewhere/vjhstudio/main.py", "serve", "--port", "8080", "--open"]
    )
    argv = []
    monkeypatch.setattr(restart, "_execv", lambda a: argv.extend(a))
    restart.request_restart(delay=0)
    restart._pending_join()
    assert argv == [
        sys.executable,
        "-m",
        "vjhstudio.main",
        "serve",
        "--port",
        "8080",
        "--no-browser",
    ]
