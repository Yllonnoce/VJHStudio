"""Models page: sort by price (default) or by name."""

import re


def _names(html: str) -> list[str]:
    return re.findall(r'<strong class="model-name">([^<]*)</strong>', html) or re.findall(
        r"<strong>([^<]*)</strong>", html.split("<tbody>", 1)[1]
    )


async def test_models_can_be_sorted_by_name(client):
    by_price = await client.get("/hx/models?kind=video")
    by_name = await client.get("/hx/models?kind=video&sort=name")
    assert by_price.status_code == by_name.status_code == 200
    names = _names(by_name.text)
    assert len(names) >= 3
    assert names == sorted(names, key=str.lower)
    assert _names(by_price.text) != names  # price order is not alphabetical for the snapshot
    assert "sort=name" in by_price.text and 'aria-pressed="true"' in by_name.text


async def test_unknown_sort_falls_back_to_price(client):
    a = await client.get("/hx/models?kind=image&sort=bogus")
    b = await client.get("/hx/models?kind=image")
    assert _names(a.text) == _names(b.text)
