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
