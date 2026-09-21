"""Settings -> Updates, the header badge and the restarting page.

Nothing here touches the network: `check_and_store`, `start_update` and the
module-level `STATE` are monkeypatched, and the background check is off in the
`app` fixture (`auto_refresh=False`).
"""

from __future__ import annotations

import asyncio
import inspect
import threading

import httpx
import pytest

from tests.fakes.fake_runware import fake_factory
from vjhstudio import __version__, db
from vjhstudio.services import gitinfo, meta
from vjhstudio.services import update as update_svc
from vjhstudio.web import app as app_module
from vjhstudio.web.app import create_app
from vjhstudio.web.routes import system as system_routes

HX = {"HX-Request": "true"}


@pytest.fixture
def git_install(monkeypatch):
    """The panel hides its button on a ZIP install. These tests are about the *other*
    reasons it shows or hides, so they must not depend on this checkout having a .git."""
    monkeypatch.setattr(gitinfo, "is_git_install", lambda: True)


def _state(steps, running=True, ok=None, message=""):
    st = update_svc.UpdateState()
    st.begin()
    for title, detail, step_ok in steps:
        st.add(title, detail, ok=step_ok)
    st.message = message
    if not running:
        st.finish(bool(ok))
    return st


def _set_meta(app, key: str, value: str) -> None:
    with db.session_scope(app.state.boot.session_factory) as s:
        meta.set(s, key, value)


# --- the Settings section --------------------------------------------------


async def test_settings_page_has_an_updates_section(client, app):
    r = await client.get("/settings")
    assert r.status_code == 200
    assert r.text.count('id="updates"') == 1
    assert "Updates" in r.text
    assert __version__ in r.text
    commit = app.state.boot.commit
    if commit:
        assert commit.short in r.text
    assert "Check for updates" in r.text


async def test_updates_partial_shows_the_cached_notice(client, app, git_install):
    _set_meta(app, "update.behind", "2")
    _set_meta(app, "update.commits", '["first subject", "second subject"]')
    r = await client.get("/hx/system/updates")
    assert r.status_code == 200
    assert "2 commits behind" in r.text
    assert "first subject" in r.text and "second subject" in r.text
    assert "Update now" in r.text


async def test_check_lists_the_new_commits(client, monkeypatch, git_install):
    def fake_check(session_factory, repo=None):
        return {
            "git": True,
            "current": "abc1234 old subject",
            "behind": 3,
            "commits": ["add the gallery", "fix the runner", "bump pico"],
            "error": "",
        }

    monkeypatch.setattr(update_svc, "check_and_store", fake_check)
    r = await client.post("/system/update/check")
    assert r.status_code == 200
    assert "3 commits behind" in r.text
    for subject in ("add the gallery", "fix the runner", "bump pico"):
        assert subject in r.text
    assert "Update now" in r.text


async def test_check_with_no_new_commits_offers_no_update(client, monkeypatch, git_install):
    monkeypatch.setattr(
        update_svc,
        "check_and_store",
        lambda sf, repo=None: {
            "git": True,
            "current": "abc1234 head",
            "behind": 0,
            "commits": [],
            "error": "",
        },
    )
    r = await client.post("/system/update/check")
    assert r.status_code == 200
    assert "up to date" in r.text.lower()
    assert "Update now" not in r.text


async def test_check_renders_the_login_help_and_hides_the_update_button(
    client, monkeypatch, git_install
):
    monkeypatch.setattr(
        update_svc,
        "check_and_store",
        lambda sf, repo=None: {
            "git": True,
            "current": "",
            "behind": 0,
            "commits": [],
            "error": update_svc.GIT_LOGIN_HELP,
        },
    )
    r = await client.post("/system/update/check")
    assert r.status_code == 200
    assert "Update server login required" in r.text
    assert "Update now" not in r.text


# --- starting a run --------------------------------------------------------


async def test_update_now_starts_the_run_and_returns_the_polling_log(client, monkeypatch):
    started = []
    st = _state([("Safety backup", "vjh-x.db", True)])
    monkeypatch.setattr(update_svc, "STATE", st)
    monkeypatch.setattr(
        update_svc, "start_update", lambda paths, *a, **kw: bool(started.append(paths) or True)
    )
    r = await client.post("/system/update")
    assert r.status_code == 200
    assert len(started) == 1
    assert 'hx-trigger="every 1s"' in r.text
    assert "/hx/system/update-log" in r.text
    assert "Safety backup" in r.text


async def test_a_second_update_is_refused_with_409(client, monkeypatch):
    st = _state([("Safety backup", "", True)])
    monkeypatch.setattr(update_svc, "STATE", st)
    monkeypatch.setattr(update_svc, "start_update", lambda paths, *a, **kw: False)
    r = await client.post("/system/update")
    assert r.status_code == 409
    assert "already running" in r.text
    # The refusal still shows the live log, so the page keeps polling.
    assert "/hx/system/update-log" in r.text


# --- the live log ----------------------------------------------------------


async def test_log_mid_run_lists_the_steps_and_does_not_redirect(client, monkeypatch):
    st = _state([("Safety backup", "", True), ("Downloading update", "3 files", True)])
    monkeypatch.setattr(update_svc, "STATE", st)
    fired = []
    monkeypatch.setattr(
        system_routes.restart_svc, "request_restart", lambda: fired.append("restart")
    )
    r = await client.get("/hx/system/update-log", headers=HX)
    assert r.status_code == 200
    assert "Safety backup" in r.text and "Downloading update" in r.text
    assert "HX-Redirect" not in r.headers
    assert 'hx-trigger="every 1s"' in r.text
    assert fired == []


async def test_a_finished_run_redirects_and_restarts_exactly_once(client, monkeypatch):
    st = _state(
        [("Safety backup", "", True), ("Downloading update", "", True)],
        running=False,
        ok=True,
        message="Update complete — restarting VJHStudio.",
    )
    monkeypatch.setattr(update_svc, "STATE", st)
    fired = []
    monkeypatch.setattr(
        system_routes.restart_svc, "request_restart", lambda: fired.append("restart")
    )
    r1 = await client.get("/hx/system/update-log", headers=HX)
    r2 = await client.get("/hx/system/update-log", headers=HX)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.headers["HX-Redirect"] == "/restarting"
    assert r2.headers["HX-Redirect"] == "/restarting"
    assert fired == ["restart"]
    assert 'hx-trigger="every 1s"' not in r1.text


async def test_a_failed_run_shows_the_failing_step_and_never_restarts(client, monkeypatch):
    st = _state(
        [
            ("Safety backup", "", True),
            ("Downloading update", "fatal: could not read", False),
            ("Rolled back to the previous version", "", False),
        ],
        running=False,
        ok=False,
        message="Update aborted — the app was not changed.",
    )
    monkeypatch.setattr(update_svc, "STATE", st)
    fired = []
    monkeypatch.setattr(
        system_routes.restart_svc, "request_restart", lambda: fired.append("restart")
    )
    r = await client.get("/hx/system/update-log", headers=HX)
    assert r.status_code == 200
    assert "Rolled back to the previous version" in r.text
    assert "Update aborted" in r.text
    assert "HX-Redirect" not in r.headers
    assert fired == []


async def test_concurrent_polls_of_a_finished_run_restart_once(client, monkeypatch):
    """Every poll is a sync handler in the threadpool, so several can be inside the
    claim at the same time; re-execing the app twice would be a very bad race."""
    st = _state([("Downloading update", "", True)], running=False, ok=True, message="done")
    monkeypatch.setattr(update_svc, "STATE", st)
    fired = []
    lock = threading.Lock()

    def slow_restart():
        with lock:
            fired.append("restart")

    monkeypatch.setattr(system_routes.restart_svc, "request_restart", slow_restart)
    results = await asyncio.gather(
        *(client.get("/hx/system/update-log", headers=HX) for _ in range(8))
    )
    assert [r.status_code for r in results] == [200] * 8
    assert all(r.headers["HX-Redirect"] == "/restarting" for r in results)
    assert fired == ["restart"]


async def test_a_plain_get_of_a_finished_log_never_restarts(client, monkeypatch):
    """GET is reachable from an <img src> on any page the user has open, and the CSRF
    middleware only guards the unsafe methods, so only htmx's own poll may finish a run."""
    st = _state([("Downloading update", "", True)], running=False, ok=True, message="done")
    monkeypatch.setattr(update_svc, "STATE", st)
    fired = []
    monkeypatch.setattr(
        system_routes.restart_svc, "request_restart", lambda: fired.append("restart")
    )
    r = await client.get("/hx/system/update-log")
    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers
    assert fired == []
    # …and the real poll right after it still works.
    hx = await client.get("/hx/system/update-log", headers=HX)
    assert hx.headers["HX-Redirect"] == "/restarting"
    assert fired == ["restart"]


async def test_dismiss_clears_a_finished_log(client, monkeypatch, git_install):
    st = _state([("Downloading update", "", False)], running=False, ok=False, message="broke")
    monkeypatch.setattr(update_svc, "STATE", st)
    r = await client.post("/system/update/dismiss")
    assert r.status_code == 200
    assert "Downloading update" not in r.text and "broke" not in r.text
    assert st.snapshot() == {"running": False, "ok": None, "message": "", "steps": []}
    # The panel comes back whole, not just the log.
    assert "Check for updates" in r.text


async def test_dismiss_is_refused_while_a_run_is_in_flight(client, monkeypatch, git_install):
    st = _state([("Downloading update", "", True)])
    monkeypatch.setattr(update_svc, "STATE", st)
    r = await client.post("/system/update/dismiss")
    assert r.status_code == 409
    assert "Downloading update" in r.text
    assert st.snapshot()["steps"]


# --- the header badge ------------------------------------------------------


async def test_header_shows_the_update_badge(client, app):
    _set_meta(app, "update.behind", "2")
    r = await client.get("/")
    assert r.status_code == 200
    assert "Update available (2)" in r.text
    assert "/settings#updates" in r.text


async def test_header_hides_the_badge_when_up_to_date(client, app):
    _set_meta(app, "update.behind", "0")
    r = await client.get("/")
    assert "Update available" not in r.text


async def test_header_survives_a_junk_behind_value(client, app):
    """app_meta is text; a half-written value must not 500 every page."""
    _set_meta(app, "update.behind", "soon")
    r = await client.get("/")
    assert r.status_code == 200
    assert "Update available" not in r.text


# --- the restarting page ---------------------------------------------------


async def test_restarting_page_carries_the_current_boot_id(client, app):
    health = await client.get("/api/health")
    boot_id = health.json()["boot_id"]
    assert boot_id
    r = await client.get("/restarting")
    assert r.status_code == 200
    assert boot_id in r.text
    assert "restartWatcher(" in r.text
    assert "/settings#updates" in r.text


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/x",
        "//evil.example/x",
        # Browsers normalise the backslash to "/", so this is "//evil.example" too.
        "/\\evil.example/x",
        "\\\\evil.example/x",
        "  //evil.example",
        # Browsers strip a tab out of a URL, so this is "//evil.example" as well.
        "/\t/evil.example",
    ],
)
async def test_restarting_page_only_accepts_a_local_return_path(client, hostile):
    r = await client.get("/restarting", params={"return": hostile})
    assert r.status_code == 200
    assert "evil.example" not in r.text
    assert "/settings#updates" in r.text


async def test_restarting_page_keeps_a_real_path(client):
    ok = await client.get("/restarting", params={"return": "/gallery"})
    assert "/gallery" in ok.text


async def test_restart_watcher_is_shipped_in_app_js():
    js = (app_module.STATIC_DIR / "js" / "app.js").read_text()
    assert "window.restartWatcher" in js
    assert "/api/health" in js


# --- the background check --------------------------------------------------


async def test_no_background_update_check_without_auto_refresh(app):
    async with app.router.lifespan_context(app):
        assert app.state.update_task is None


async def test_background_update_check_is_skipped_when_offline(paths, fake, download_transport):
    offline = create_app(
        paths,
        client_factory=fake_factory(fake),
        env={"VJHSTUDIO_OFFLINE": "1"},
        auto_refresh=True,
        download_transport=download_transport,
    )
    async with offline.router.lifespan_context(offline):
        assert offline.state.update_task is None


async def test_background_update_check_runs_for_a_git_install(
    paths, fake, download_transport, monkeypatch
):
    monkeypatch.setattr(gitinfo, "is_git_install", lambda: True)
    monkeypatch.setattr(app_module.catalog, "needs_refresh", lambda s: False)
    monkeypatch.setattr(update_svc, "check_and_store", lambda sf, repo=None: {"behind": 0})
    online = create_app(
        paths,
        client_factory=fake_factory(fake),
        env={},
        auto_refresh=True,
        download_transport=download_transport,
    )
    async with online.router.lifespan_context(online):
        task = online.state.update_task
        assert task is not None and not task.done()
    for _ in range(5):
        await asyncio.sleep(0)
    assert task.done()


def test_update_handlers_are_sync_except_the_check():
    """Only the handler that shells out to git is async; the rest are DB-only."""
    for fn in (
        system_routes.updates_panel,
        system_routes.update_now,
        system_routes.update_log,
        system_routes.restarting,
    ):
        assert not inspect.iscoroutinefunction(fn), fn.__name__
    assert inspect.iscoroutinefunction(system_routes.update_check)


@pytest.mark.parametrize("code", ["409", "422"])
async def test_htmx_config_swaps_the_partial_error_codes(client, code):
    r = await client.get("/")
    assert f'{{"code":"{code}","swap":true,"error":false}}' in r.text


async def test_non_local_peer_cannot_start_an_update(app, monkeypatch):
    monkeypatch.setattr(update_svc, "start_update", lambda paths, *a, **kw: True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("10.0.0.5", 5555))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/system/update")
    assert r.status_code == 403
