import json
from pathlib import Path

import httpx
import pytest

from vjhstudio.runware.catalog_api import ContentAPI, ContentAPIError

FIX = Path(__file__).parent / "fixtures"


def transport_with(pages: dict[str, str | int]):
    """pages maps a path (+query) to a fixture filename or an HTTP status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path + (
            "?" + str(request.url.query, "utf-8") if request.url.query else ""
        )
        for k, v in pages.items():
            if key.startswith(k):
                if isinstance(v, int):
                    return httpx.Response(v)
                return httpx.Response(200, json=json.loads((FIX / v).read_text()))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_list_models_follows_pagination():
    t = transport_with(
        {
            "/models?category=image&status=live&limit=2&offset=0": "content_list_image.json",
            "/models?category=image&status=live&limit=2&offset=2": "content_list_image_p2.json",
        }
    )
    api = ContentAPI(transport=t)
    items = await api.list_models("image", page_size=2)
    assert [i["model"] for i in items] == [
        "bfl-flux-1-dev",
        "google-nano-banana-pro",
        "birefnet-general",
    ]


async def test_get_pricing_and_404():
    t = transport_with(
        {"/models/bfl-flux-1-dev/pricing": "content_pricing_flux.json", "/models/nope/pricing": 404}
    )
    api = ContentAPI(transport=t)
    assert (await api.get_pricing("bfl-flux-1-dev"))["air"] == "runware:101@1"
    assert await api.get_pricing("nope") is None


async def test_server_error_raises():
    api = ContentAPI(transport=transport_with({"/models": 500}))
    with pytest.raises(ContentAPIError):
        await api.list_models("video")


async def test_network_error_raises():
    def boom(request):
        raise httpx.ConnectError("down")

    api = ContentAPI(transport=httpx.MockTransport(boom))
    with pytest.raises(ContentAPIError):
        await api.get_pricing("x")
