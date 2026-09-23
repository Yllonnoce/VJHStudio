"""Model dropdowns can be sorted by price or by name (client-side, persisted)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
COMPOSE = ROOT / "vjhstudio" / "web" / "static" / "js" / "compose.js"
APP_JS = (ROOT / "vjhstudio" / "web" / "static" / "js" / "app.js").read_text()


async def test_generate_selects_carry_sort_data_and_buttons(client):
    for path, select_id in (
        ("/generate/image", "model-select"),
        ("/generate/video", "video-model-select"),
    ):
        r = await client.get(path)
        assert r.status_code == 200
        assert f'id="{select_id}" name="model" data-sortable="1"' in r.text
        assert 'data-name="' in r.text and 'data-price="' in r.text
        assert f'data-sort-select="{select_id}" data-sort="name"' in r.text
        assert f'data-sort-select="{select_id}" data-sort="price" aria-pressed="true"' in r.text


def test_app_js_sorts_in_place_and_persists():
    body = APP_JS.split("function vjhSortSelect(", 1)[1]
    assert "select.appendChild(" in body and "select.value = current" in body
    assert "localStorage.setItem(VJH_SORT_KEY" in body
    assert "htmx:afterSettle" in body and "vjhApplySavedSort" in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_vjh_sort_options_orders_by_price_then_name():
    script = f"""
      const c = require({json.dumps(str(COMPOSE))});
      const items = [
        {{value: 'a', name: 'Zeta', price: '0.1'}},
        {{value: 'b', name: 'alpha', price: ''}},
        {{value: 'c', name: 'Beta', price: '0.5'}},
        {{value: 'd', name: 'gamma', price: '0.5'}},
      ];
      console.log(JSON.stringify({{
        price: c.vjhSortOptions(items, 'price').map(i => i.value),
        name: c.vjhSortOptions(items, 'name').map(i => i.value),
        untouched: items.map(i => i.value),
      }}));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got["price"] == ["c", "d", "a", "b"]  # dearest first, tie by name, unknown last
    assert got["name"] == ["b", "c", "d", "a"]  # case-insensitive A-Z
    assert got["untouched"] == ["a", "b", "c", "d"]
