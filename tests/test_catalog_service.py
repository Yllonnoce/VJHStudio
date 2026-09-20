import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from runware import RunwareError

from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio import db
from vjhstudio.models import utcnow
from vjhstudio.runware.catalog_api import ContentAPI
from vjhstudio.services import catalog, meta, migrate

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def factory(tmp_path):
    p = tmp_path / "c.db"
    migrate.upgrade(p)
    return db.make_session_factory(db.make_engine(p))


def test_seed_curated_is_idempotent_and_versioned(factory):
    with db.session_scope(factory) as s:
        n1 = catalog.seed_curated(s)
        n2 = catalog.seed_curated(s)
        assert n1 >= 10 and n2 == 0
        assert meta.get(s, catalog.SEED_VERSION_KEY) == "1"
        flux = catalog.get_by_air(s, "runware:101@1")
        assert flux.kind == "image" and flux.price_primary == 0.0038 and flux.source == "curated"


def test_list_models_sorted_by_price_desc_nulls_last(factory):
    with db.session_scope(factory) as s:
        catalog.seed_curated(s)
        images = catalog.list_models(s, "image")
        prices = [m.price_primary for m in images]
        known = [p for p in prices if p is not None]
        assert known == sorted(known, reverse=True)
        assert prices.index(None) > len(known) - 1  # unknown prices at the end
        texts = catalog.list_models(s, "text")
        assert texts[0].air == "openai:gpt@5.5"  # 35.0/1M is the dearest


def test_labels(factory):
    with db.session_scope(factory) as s:
        catalog.seed_curated(s)
        assert catalog.label(catalog.get_by_air(s, "runware:101@1")) == "FLUX.1 [dev] — $0.0038/img"
        assert (
            catalog.label(catalog.get_by_air(s, "google:3@2")) == "Veo 3.1 — $0.200/s ($1.00/5 s)"
        )
        assert (
            catalog.label(catalog.get_by_air(s, "anthropic:claude@sonnet-4.6"))
            == "Claude Sonnet 4.6 — $3.00 in / $15.00 out per 1M"
        )
        assert (
            catalog.label(catalog.get_by_air(s, "runware:100@1"))
            == "FLUX.1 [schnell] — price unknown"
        )
        fav = catalog.set_flag(s, catalog.get_by_air(s, "runware:101@1").id, "is_favourite", True)
        assert catalog.label(fav).startswith("★ ")


def test_family(factory):
    with db.session_scope(factory) as s:
        catalog.seed_curated(s)
        assert catalog.family(catalog.get_by_air(s, "runware:101@1")) == "diffusion"
        assert catalog.family(catalog.get_by_air(s, "google:4@2")) == "instruction"
        assert catalog.family(catalog.get_by_air(s, "openai:gpt-image@2")) == "instruction"
        assert catalog.family(catalog.get_by_air(s, "google:3@2")) == "video"
        assert catalog.family(catalog.get_by_air(s, "anthropic:claude@sonnet-4.6")) == "text"


def test_utility_slugs_hidden():
    assert catalog.is_utility_slug("controlnet-preprocess-canny")
    assert catalog.is_utility_slug("birefnet-general")
    assert not catalog.is_utility_slug("bfl-flux-1-dev")


def _content_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, str(request.url.query, "utf-8")
        if path == "/models":
            if "category=image" in q:
                name = (
                    "content_list_image_p2.json" if "offset=2" in q else "content_list_image.json"
                )
                return httpx.Response(200, json=json.loads((FIX / name).read_text()))
            return httpx.Response(200, json={"total": 0, "limit": 2, "offset": 0, "items": []})
        if path == "/models/bfl-flux-1-dev/pricing":
            return httpx.Response(
                200, json=json.loads((FIX / "content_pricing_flux.json").read_text())
            )
        if path == "/models/google-nano-banana-pro/pricing":
            return httpx.Response(500)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_refresh_from_content_api(factory):
    with db.session_scope(factory) as s:
        catalog.seed_curated(s)
        catalog.set_flag(s, catalog.get_by_air(s, "runware:101@1").id, "is_favourite", True)
    api = ContentAPI(transport=_content_transport())
    res = await catalog.refresh_from_content_api(factory, api)
    assert res.models == 3 and res.priced == 1 and len(res.errors) == 1
    with db.session_scope(factory) as s:
        flux = catalog.get_by_air(s, "runware:101@1")
        assert (
            flux.source == "content"
            and flux.price_source == "content_api"
            and flux.is_favourite is True
        )
        assert flux.price_tiers_json["typical_latency_ms"] == 2942
        banana = catalog.get_by_air(s, "google:4@2")
        assert (
            banana.price_primary == 0.138
        )  # examples fallback from the listing when pricing failed
        util = catalog.get_by_air(s, "runware:112@5")
        assert util.is_hidden is True and util.kind == "image"
        assert catalog.last_refreshed(s) is not None and not catalog.needs_refresh(s)


def test_needs_refresh_when_old(factory):
    with db.session_scope(factory) as s:
        assert catalog.needs_refresh(s)
        meta.set(s, catalog.REFRESH_KEY, (utcnow() - timedelta(days=8)).isoformat())
        assert catalog.needs_refresh(s)
        meta.set(s, catalog.REFRESH_KEY, utcnow().isoformat())
        assert not catalog.needs_refresh(s)


async def test_search_live_maps_results_and_add(factory):
    fake = FakeRunware(
        {
            "model_search": [
                [
                    {
                        "results": [
                            {
                                "air": "civitai:4201@130090",
                                "name": "Realistic Vision",
                                "category": "checkpoint",
                                "architecture": "sdxl",
                                "provider": "civitai",
                                "capabilities": ["io:text-to-image"],
                                "heroImage": "https://x/y.png",
                                "shortDescription": "photo",
                            }
                        ],
                        "totalResults": 1,
                    }
                ]
            ]
        }
    )
    rows = await catalog.search_live(fake_factory(fake), "key", "rest", "realistic", "image")
    assert rows[0]["air"] == "civitai:4201@130090" and fake.calls[-1][1]["search"] == "realistic"
    with db.session_scope(factory) as s:
        m = catalog.add_from_search(s, rows[0], "image")
        assert m.source == "search" and m.price_primary is None and m.kind == "image"
        assert catalog.label(m).endswith("price unknown")


async def test_search_live_error(factory):
    fake = FakeRunware({"model_search": [RunwareError("invalidApiKey", "bad")]})
    with pytest.raises(catalog.SearchError) as ei:
        await catalog.search_live(fake_factory(fake), "key", "rest", "x", "image")
    assert ei.value.error.code == "auth"
