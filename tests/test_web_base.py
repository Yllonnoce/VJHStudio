"""Invariants of the base layout that the rest of the UI depends on."""

import json
import re


async def test_htmx_config_swaps_422_partials(client):
    """422 re-renders a partial with inline errors; htmx 2.x drops 4xx bodies unless told not to."""
    r = await client.get("/")
    assert "htmx-config" in r.text
    assert '"422"' in r.text
    content = re.search(r'<meta name="htmx-config" content=\'(.*?)\'>', r.text).group(1)
    handling = {h["code"]: h for h in json.loads(content)["responseHandling"]}
    assert handling["422"] == {"code": "422", "swap": True, "error": False}
    assert handling["[45].."]["swap"] is False


def test_db_only_handlers_are_sync_so_starlette_threadpools_them():
    """Blocking SQLAlchemy inside `async def` stalls the event loop for every
    other request; only the handler that awaits the network stays async."""
    import inspect

    from vjhstudio.web.routes import pages
    from vjhstudio.web.routes import settings as settings_routes

    for fn in (
        pages.index,
        settings_routes.settings_page,
        settings_routes.save_settings,
        settings_routes.save_api_key,
        settings_routes.clear_api_key,
        settings_routes.clear_database,
    ):
        assert not inspect.iscoroutinefunction(fn), fn.__name__
    assert inspect.iscoroutinefunction(settings_routes.test_api_key)
