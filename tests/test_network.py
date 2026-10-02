"""Opening the app to the home network: the --network switch, the addresses it reports,
the launch scripts, and what the Settings page says."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import uvicorn

from vjhstudio import main, netinfo

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_network_urls_name_real_addresses_never_the_wildcard(monkeypatch):
    monkeypatch.setattr(netinfo, "lan_addresses", lambda: ["192.168.1.24", "10.0.0.7"])
    assert netinfo.network_urls("0.0.0.0", 8080) == [
        "http://192.168.1.24:8080/",
        "http://10.0.0.7:8080/",
    ]
    assert netinfo.network_urls("192.168.1.24", 8081) == ["http://192.168.1.24:8081/"]
    # only this computer: nothing to tell anyone
    assert netinfo.network_urls("127.0.0.1", 8080) == [] and netinfo.network_urls("", 8080) == []
    assert netinfo.network_urls("localhost", 8080) == []


def test_lan_addresses_leave_out_loopback(monkeypatch):
    class FakeSocket:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, addr):
            pass

        def getsockname(self):
            return ("192.168.1.24", 5555)

    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(
        socket,
        "gethostbyname_ex",
        lambda name: (name, [], ["127.0.1.1", "172.17.0.1", "192.168.1.24"]),
    )
    # the routed address alone: a Docker bridge or VPN the hostname also resolves to is
    # not somewhere a phone can reach (seen live: 172.17.0.1 was listed beside the real one)
    assert netinfo.lan_addresses() == ["192.168.1.24"]

    def boom(*a):
        raise OSError("no network")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "gethostbyname_ex", boom)
    assert netinfo.lan_addresses() == []  # offline: nothing, and no exception


def _serve(monkeypatch, tmp_path, capsys, argv, env=None):
    """Run ``serve`` up to the point uvicorn would start; return what it was given."""
    monkeypatch.setenv("VJHSTUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VJHSTUDIO_HOST", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(netinfo, "lan_addresses", lambda: ["192.168.1.24"])
    monkeypatch.setattr(main, "pick_port", lambda host, port: (port, False))
    seen = {}
    monkeypatch.setattr(
        uvicorn, "run", lambda app, host, port, **kw: seen.update(host=host, port=port, app=app)
    )
    assert main.main(["serve", "--port", "8123", "--no-browser", *argv]) == 0
    return seen, capsys.readouterr().out


def test_serve_network_listens_on_every_interface_and_says_where(monkeypatch, tmp_path, capsys):
    seen, out = _serve(monkeypatch, tmp_path, capsys, ["--network"])
    assert seen["host"] == "0.0.0.0" and seen["port"] == 8123
    assert "http://127.0.0.1:8123/" in out  # this computer
    assert "http://192.168.1.24:8123/" in out  # everyone else
    assert "0.0.0.0" not in out  # never shown as something to open
    assert "anyone on your network" in out.lower()
    assert seen["app"].state.host == "0.0.0.0"


def test_serve_without_network_stays_on_this_computer(monkeypatch, tmp_path, capsys):
    seen, out = _serve(monkeypatch, tmp_path, capsys, [])
    assert seen["host"] == "127.0.0.1" and "192.168.1.24" not in out
    # the environment variable keeps working, and an explicit --host still wins
    seen, out = _serve(monkeypatch, tmp_path, capsys, [], {"VJHSTUDIO_HOST": "0.0.0.0"})
    assert seen["host"] == "0.0.0.0" and "http://192.168.1.24:8123/" in out
    seen, _ = _serve(monkeypatch, tmp_path, capsys, ["--network", "--host", "192.168.1.24"])
    assert seen["host"] == "192.168.1.24"


def test_the_already_running_check_dials_loopback_not_the_wildcard(monkeypatch):
    """ "http://0.0.0.0:8080" is not dialable on Windows; the probe must use loopback."""
    asked = []

    def fake_get(url, timeout):
        asked.append(url)
        raise OSError

    monkeypatch.setattr(main.httpx, "get", fake_get)
    assert main.is_ours("0.0.0.0", 8080) is False
    assert asked == ["http://127.0.0.1:8080/api/health"]


def test_start_scripts_take_a_network_switch():
    sh = (REPO_ROOT / "start.sh").read_text()
    assert "--network" in sh and "VJHSTUDIO_HOST=0.0.0.0" in sh
    bat = (REPO_ROOT / "start.bat").read_text()
    assert "--network" in bat and 'set "VJHSTUDIO_HOST=0.0.0.0"' in bat
    assert "powershell" not in bat.lower()


def test_vscode_debug_runs_on_the_network_and_has_a_local_only_option():
    """The owner runs the app from VS Code and wants it reachable on the home network, so
    the option F5 starts listens on 0.0.0.0; a second one keeps it to this computer."""
    launch = json.loads((REPO_ROOT / ".vscode" / "launch.json").read_text())
    names = {c["name"]: c for c in launch["configurations"]}
    main_cfg = names["VJHStudio: serve (debug)"]
    assert main_cfg["args"][:3] == ["serve", "--host", "0.0.0.0"] and "8080" in main_cfg["args"]
    assert main_cfg["env"]["VJHSTUDIO_DATA_DIR"].endswith("data-debug")
    local = names["VJHStudio: serve (debug, this computer only)"]
    assert "127.0.0.1" in local["args"] and "0.0.0.0" not in local["args"]


async def test_settings_says_how_other_devices_reach_it(client, app, monkeypatch):
    page = (await client.get("/settings")).text
    assert "Only this computer can open VJHStudio" in page
    monkeypatch.setattr(netinfo, "lan_addresses", lambda: ["192.168.1.24"])
    app.state.host = "0.0.0.0"
    page = (await client.get("/settings")).text
    assert "http://192.168.1.24:" in page and "Anyone on your network" in page
