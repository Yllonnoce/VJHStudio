# VJHStudio Phase 2 (Catalog & pricing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A model catalog with real prices: seeded offline from a shipped snapshot, refreshed from RunWare's free content API, searchable live through the SDK, and shown as price-sorted (most expensive first) dropdowns for image, video and text models plus a Models page.

**Architecture:** `vjhstudio/runware/catalog_api.py` talks to `https://content.runware.ai` over httpx (public, no key; transport injectable for tests). `vjhstudio/runware/pricing.py` holds pure normalization (`PriceInfo`). `vjhstudio/services/catalog.py` owns the `catalog_models` table: seed, refresh, list-by-kind sorted by price, labels, family detection, live `modelSearch` add. Web: `/models` page + `/hx/models*` partials + `/api/models`, and the Settings default-model fields become price-sorted selects.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, httpx (already a dependency), runware-sdk (`client.model_search`), Jinja2 + HTMX + Pico, pytest with `httpx.MockTransport` and the existing `tests/fakes/fake_runware.py`.

**Spec:** `docs/superpowers/specs/2026-09-20-runwarestudio-design.md` — sections "Verified facts about runware-sdk" (content API), "Data model → catalog_models", "RunWare adapter → catalog_api.py normalization rules", "Model dropdowns (image / video / polish-text)", "Model-family rules".

## Global Constraints

- Package `vjhstudio`; env prefix `VJHSTUDIO_`; port 8080; no PowerShell; commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` on every commit; `uv run pytest -q`, `uv run ruff check .` and `uv run ruff format --check .` clean before each commit.
- `vjhstudio/runware/` and `vjhstudio/services/` never import `vjhstudio/web/`. Routes are thin. No `Base.metadata.create_all`.
- Content API base URL `https://content.runware.ai`; endpoints `GET /models?category=<image|video|text>&status=live&limit=<n>&offset=<n>&paginate=true` (paginated `{"total","limit","offset","items"}`) and `GET /models/{slug_or_air}/pricing`. Unauthenticated. All network calls have a 20 s timeout and never block boot.
- Price normalization (verbatim from spec): image → `price_unit="per_image"`, `price_primary` = the `pricingMeasured` row whose configuration starts with `1024x1024` and contains no "LoRA", else the first measured row, else the minimum parsed `pricingExamples[].price` ("$0.0013" → 0.0013), else None. video → `price_unit="per_second"`, primary = `pricingRates` row with `unit == "durationSecond"` whose label contains "720p" and not "audio", else the lowest `durationSecond` rate, else None. text → `price_unit="per_1m_tokens"`, `price_in = inputToken×1e6`, `price_out = outputToken×1e6`, `price_primary = price_in + price_out` (None if either missing). `price_tiers_json` keeps `{"measured": [...], "rates": [...], "examples": [...], "typical_latency_ms": <min latencyMs of examples or null>}`.
- Dropdown order: `price_primary DESC`, NULLs last, then name ASC. Label: image `"{name} — ${primary:.4f}/img"`, video `"{name} — ${primary:.3f}/s (${primary*5:.2f}/5 s)"`, text `"{name} — ${in:.2f} in / ${out:.2f} out per 1M"`, unknown price → `"{name} — price unknown"`. Favourites get a leading `★ `.
- Model family (spec): `instruction` when `architecture` or `creator` or `air` contains any of `gemini`, `gpt`, `openai`, `nano-banana`; `video` when kind is video; otherwise `diffusion`.
- Kinds stored in `catalog_models.kind`: `image`, `video`, `text`. Content API rows whose `capabilities` contain none of `io:text-to-image`, `io:image-to-image`, `io:text-to-video`, `io:image-to-video`, `io:text-to-text`/`io:chat` are still imported (kind from the category queried) but `is_hidden` stays False; utility models (upscalers, background removal, controlnet preprocessors, age classifiers, `runware:llama-3-1-8b@prompt-enhancer`) are hidden by name rule: hide when the slug contains any of `controlnet-preprocess`, `birefnet`, `rembg`, `yolov8`, `mediapipe`, `age-`, `clip-vit`, `prompt-enhancer`, `upscal`, `esrgan`, `swinir`, `ccsr`, `clarity`, `remove-background`, `background-removal`, `eraser`, `vectoriz`, `lipsync`, `lip-sync`, `avatar`, `enhancement`, `object-remover`, `layerize`, `vto`, `try-on`.

## File Structure

```
vjhstudio/runware/pricing.py          pure: PriceInfo, normalize_price(kind, pricing_row, list_row) , parse_dollars(), kind_for_category()
vjhstudio/runware/catalog_api.py      httpx client: ContentAPI(transport=None).list_models(category) / get_pricing(slug); ContentAPIError
vjhstudio/data/curated_models.json    shipped snapshot: {"version": 1, "models": [...]} in the normalized row shape
vjhstudio/services/catalog.py         seed_curated(session, paths) ; upsert_row(session, row) ; refresh_from_content_api(session_factory, api) -> RefreshResult ;
                                      list_models(session, kind, include_hidden=False) ; label(model) ; family(model) ; is_utility_slug(slug) ;
                                      search_live(client_factory, api_key, transport, query, kind) ; add_from_search(session, record, kind) ; last_refreshed(session)
vjhstudio/boot.py                     call catalog.seed_curated after the default project
vjhstudio/web/app.py                  lifespan: schedule background price refresh if older than 7 days (asyncio task, best-effort)
vjhstudio/web/routes/catalog.py       GET /models ; GET /hx/models?kind= ; POST /hx/models/search ; POST /models/add ; POST /models/refresh-prices ; POST /models/{id}/favourite ; POST /models/{id}/hide ; GET /api/models?kind=
vjhstudio/web/templates/pages/models.html, catalog/_list.html, catalog/_row.html, catalog/_search_results.html, catalog/_refresh_status.html, catalog/_model_select.html
vjhstudio/web/templates/settings/_general_form.html   defaults.* model fields rendered with catalog/_model_select.html
vjhstudio/web/templates/_header.html  nav link "Models"
tests/fixtures/content_*.json         canned content API responses (from live probes)
tests/test_pricing.py, tests/test_catalog_api.py, tests/test_catalog_service.py, tests/test_web_models.py
```

---

### Task 1: Price normalization and the content API client

**Files:**
- Create: `vjhstudio/runware/pricing.py`, `vjhstudio/runware/catalog_api.py`, `tests/fixtures/content_list_image.json`, `tests/fixtures/content_pricing_flux.json`, `tests/fixtures/content_pricing_veo.json`, `tests/fixtures/content_pricing_gemma.json`, `tests/test_pricing.py`, `tests/test_catalog_api.py`

**Interfaces:**
- Produces:
  - `pricing.PriceInfo(unit: str | None, primary: float | None, price_in: float | None, price_out: float | None, tiers: dict)` (frozen dataclass)
  - `pricing.parse_dollars(text: str | None) -> float | None` (`"$1.60"` → 1.6, `"$0.0013"` → 0.0013, garbage → None)
  - `pricing.normalize_price(kind: str, pricing: dict | None, listing: dict | None = None) -> PriceInfo` (rules in Global Constraints; `listing` supplies `pricingExamples`/`pricingMeasured` when `pricing` is None)
  - `pricing.kind_for_category(category: str) -> str | None` (`image`/`video`/`text` pass through, others None)
  - `catalog_api.ContentAPI(base_url="https://content.runware.ai", transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0)` with `async list_models(category: str, status: str = "live", page_size: int = 100) -> list[dict]` (follows pagination until `offset+len(items) >= total`) and `async get_pricing(model_id: str) -> dict | None` (404 → None); raises `ContentAPIError(str)` on other HTTP/network errors.

- [ ] **Step 1: Write the fixtures** (real shapes captured from live probes on 2026-09-20; trim to what is shown)

`tests/fixtures/content_pricing_flux.json`:
```json
{"model":"bfl-flux-1-dev","air":"runware:101@1","name":"FLUX.1 [dev]","status":"live",
 "pricingOverview":"Each image generation costs $0.0038 at 1024x1024.",
 "pricingExamples":[{"configuration":"text-to-image · 1024×1024 · steps 28","price":"$0.0013","latencyMs":2942,"exampleId":"a"},
                    {"configuration":"image-to-image · 720p","price":"$0.0038","latencyMs":3100,"exampleId":"b"}],
 "pricingMeasured":[{"configuration":"1024x1024 · 28 steps","price":0.0038},{"configuration":"512x512 · 28 steps","price":0.0019},
                    {"configuration":"1024x1024 · 28 steps +1 LoRA","price":0.0051}],
 "category":["image"]}
```
`tests/fixtures/content_pricing_veo.json`:
```json
{"model":"google-veo-3-1","air":"google:3@2","name":"Veo 3.1","status":"live",
 "pricingOverview":"Each generation will cost $0.2/s for 720p, or $0.4/s for 720p with audio.",
 "pricingExamples":[{"configuration":"text-to-video · 1080p · 4s · generateAudio","price":"$1.60","latencyMs":89836,"exampleId":"c"}],
 "pricingRates":[{"amount":0.2,"unit":"durationSecond","label":"720p / 1080p · without audio"},
                 {"amount":0.4,"unit":"durationSecond","label":"720p / 1080p · with audio"},
                 {"amount":0.4,"unit":"durationSecond","label":"4K · without audio"},
                 {"amount":0.6,"unit":"durationSecond","label":"4K · with audio"}],
 "category":["video"]}
```
`tests/fixtures/content_pricing_gemma.json`:
```json
{"model":"google-gemma-4-31b","air":"google:gemma@4-31b","name":"Gemma 4 31B","status":"live",
 "pricingExamples":[{"configuration":"text-generation","price":"$0.000567","latencyMs":16040,"exampleId":"d"}],
 "pricingRates":[{"amount":1.02e-7,"unit":"inputToken"},{"amount":2.97e-7,"unit":"outputToken"},{"amount":1.2e-8,"unit":"cachedInputToken"}],
 "pricingBasis":"token","category":["text"]}
```
`tests/fixtures/content_list_image.json` (a paginated page):
```json
{"total":3,"limit":2,"offset":0,"items":[
 {"model":"bfl-flux-1-dev","air":"runware:101@1","name":"FLUX.1 [dev]","creator":"black-forest-labs","architecture":"flux-1-dev","capabilities":["io:text-to-image","form:checkpoint"],"hosted":true,"status":"live","pricingOverview":"Each image generation costs $0.0038 at 1024x1024.","pricingExamples":[{"configuration":"text-to-image · 1024×1024 · steps 28","price":"$0.0013","latencyMs":2942}]},
 {"model":"google-nano-banana-pro","air":"google:4@2","name":"Nano Banana Pro","creator":"google","architecture":"gemini","capabilities":["io:text-to-image","io:image-to-image"],"hosted":true,"status":"live","pricingExamples":[{"configuration":"1K","price":"$0.138","latencyMs":12000}]}
]}
```
and a second page file `tests/fixtures/content_list_image_p2.json`:
```json
{"total":3,"limit":2,"offset":2,"items":[
 {"model":"birefnet-general","air":"runware:112@5","name":"BiRefNet General","creator":"runware","architecture":"birefnet","capabilities":["io:image-to-image"],"hosted":true,"status":"live"}
]}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_pricing.py
import json
from pathlib import Path
from vjhstudio.runware import pricing

FIX = Path(__file__).parent / "fixtures"

def load(name):
    return json.loads((FIX / name).read_text())

def test_parse_dollars():
    assert pricing.parse_dollars("$1.60") == 1.6
    assert pricing.parse_dollars("$0.0013") == 0.0013
    assert pricing.parse_dollars("free") is None and pricing.parse_dollars(None) is None

def test_image_uses_1024_measured_without_lora():
    p = pricing.normalize_price("image", load("content_pricing_flux.json"))
    assert p.unit == "per_image" and p.primary == 0.0038
    assert p.tiers["typical_latency_ms"] == 2942
    assert len(p.tiers["measured"]) == 3

def test_image_falls_back_to_examples_when_no_measured():
    row = {"pricingExamples": [{"price": "$0.20"}, {"price": "$0.05"}]}
    assert pricing.normalize_price("image", row).primary == 0.05

def test_video_prefers_720p_without_audio():
    p = pricing.normalize_price("video", load("content_pricing_veo.json"))
    assert p.unit == "per_second" and p.primary == 0.2

def test_video_falls_back_to_lowest_rate():
    row = {"pricingRates": [{"amount": 0.5, "unit": "durationSecond", "label": "4K"}, {"amount": 0.3, "unit": "durationSecond", "label": "1080p"}]}
    assert pricing.normalize_price("video", row).primary == 0.3

def test_text_per_million_tokens():
    p = pricing.normalize_price("text", load("content_pricing_gemma.json"))
    assert p.unit == "per_1m_tokens"
    assert round(p.price_in, 4) == 0.102 and round(p.price_out, 4) == 0.297
    assert round(p.primary, 4) == 0.399

def test_unknown_price_is_none_not_error():
    p = pricing.normalize_price("image", None, {"name": "x"})
    assert p.primary is None and p.unit == "per_image"

def test_kind_for_category():
    assert pricing.kind_for_category("image") == "image"
    assert pricing.kind_for_category("audio") is None
```

```python
# tests/test_catalog_api.py
import json
from pathlib import Path
import httpx, pytest
from vjhstudio.runware.catalog_api import ContentAPI, ContentAPIError

FIX = Path(__file__).parent / "fixtures"

def transport_with(pages: dict[str, str | int]):
    """pages maps a path (+query) to a fixture filename or an HTTP status code."""
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path + ("?" + str(request.url.query, "utf-8") if request.url.query else "")
        for k, v in pages.items():
            if key.startswith(k):
                if isinstance(v, int):
                    return httpx.Response(v)
                return httpx.Response(200, json=json.loads((FIX / v).read_text()))
        return httpx.Response(404)
    return httpx.MockTransport(handler)

async def test_list_models_follows_pagination():
    t = transport_with({"/models?category=image&status=live&limit=2&offset=0": "content_list_image.json",
                        "/models?category=image&status=live&limit=2&offset=2": "content_list_image_p2.json"})
    api = ContentAPI(transport=t)
    items = await api.list_models("image", page_size=2)
    assert [i["model"] for i in items] == ["bfl-flux-1-dev", "google-nano-banana-pro", "birefnet-general"]

async def test_get_pricing_and_404():
    t = transport_with({"/models/bfl-flux-1-dev/pricing": "content_pricing_flux.json", "/models/nope/pricing": 404})
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
```

- [ ] **Step 3: Run to verify failure** → `uv run pytest tests/test_pricing.py tests/test_catalog_api.py -q` → import errors.

- [ ] **Step 4: Implement pricing.py**

```python
# vjhstudio/runware/pricing.py
"""Pure price normalization for RunWare content-catalog rows. No I/O."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

_DOLLARS = re.compile(r"\$?\s*([0-9]+(?:\.[0-9]+)?)")
UNIT_FOR_KIND = {"image": "per_image", "video": "per_second", "text": "per_1m_tokens"}


@dataclass(frozen=True)
class PriceInfo:
    unit: str | None
    primary: float | None
    price_in: float | None = None
    price_out: float | None = None
    tiers: dict = field(default_factory=dict)


def parse_dollars(text: str | None) -> float | None:
    if not text:
        return None
    m = _DOLLARS.search(str(text))
    return float(m.group(1)) if m else None


def kind_for_category(category: str | None) -> str | None:
    return category if category in UNIT_FOR_KIND else None


def _examples_min(examples: list[dict]) -> float | None:
    vals = [v for v in (parse_dollars(e.get("price")) for e in examples) if v is not None]
    return min(vals) if vals else None


def _latency(examples: list[dict]) -> int | None:
    vals = [e["latencyMs"] for e in examples if isinstance(e.get("latencyMs"), (int, float))]
    return int(min(vals)) if vals else None


def _image_primary(measured: list[dict], examples: list[dict]) -> float | None:
    for row in measured:
        cfg = str(row.get("configuration", ""))
        if cfg.startswith("1024x1024") and "lora" not in cfg.lower() and isinstance(row.get("price"), (int, float)):
            return float(row["price"])
    for row in measured:
        if isinstance(row.get("price"), (int, float)):
            return float(row["price"])
    return _examples_min(examples)


def _video_primary(rates: list[dict]) -> float | None:
    secs = [r for r in rates if r.get("unit") == "durationSecond" and isinstance(r.get("amount"), (int, float))]
    for r in secs:
        label = str(r.get("label", "")).lower()
        if "720p" in label and "audio" not in label.replace("without audio", ""):
            return float(r["amount"])
    return min((float(r["amount"]) for r in secs), default=None)


def _rate(rates: list[dict], unit: str) -> float | None:
    for r in rates:
        if r.get("unit") == unit and isinstance(r.get("amount"), (int, float)):
            return float(r["amount"]) * 1_000_000
    return None


def normalize_price(kind: str, pricing: dict | None, listing: dict | None = None) -> PriceInfo:
    src = pricing or listing or {}
    measured = list(src.get("pricingMeasured") or (listing or {}).get("pricingMeasured") or [])
    rates = list(src.get("pricingRates") or [])
    examples = list(src.get("pricingExamples") or (listing or {}).get("pricingExamples") or [])
    tiers = {"measured": measured, "rates": rates, "examples": examples, "typical_latency_ms": _latency(examples)}
    unit = UNIT_FOR_KIND.get(kind)
    if kind == "image":
        return PriceInfo(unit, _image_primary(measured, examples), tiers=tiers)
    if kind == "video":
        return PriceInfo(unit, _video_primary(rates), tiers=tiers)
    if kind == "text":
        p_in, p_out = _rate(rates, "inputToken"), _rate(rates, "outputToken")
        primary = (p_in + p_out) if (p_in is not None and p_out is not None) else None
        return PriceInfo(unit, primary, p_in, p_out, tiers)
    return PriceInfo(None, None, tiers=tiers)
```
Note on `_video_primary`: the "without audio" check must treat the label `"720p / 1080p · without audio"` as a match and `"720p / 1080p · with audio"` as a non-match. The expression above strips "without audio" before testing for "audio"; keep it exactly.

- [ ] **Step 5: Implement catalog_api.py**

```python
# vjhstudio/runware/catalog_api.py
"""RunWare public content catalog (https://content.runware.ai). No API key needed."""
from __future__ import annotations
import httpx

DEFAULT_BASE_URL = "https://content.runware.ai"


class ContentAPIError(Exception):
    pass


class ContentAPI:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 20.0):
        self._client_kwargs = {"base_url": base_url, "timeout": timeout, "transport": transport,
                               "headers": {"User-Agent": "VJHStudio"}}

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response | None:
        try:
            async with httpx.AsyncClient(**self._client_kwargs) as c:
                r = await c.get(path, params=params)
        except httpx.HTTPError as e:
            raise ContentAPIError(f"content API unreachable: {e}") from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise ContentAPIError(f"content API {r.status_code} for {path}")
        return r

    async def list_models(self, category: str, status: str = "live", page_size: int = 100) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            r = await self._get("/models", {"category": category, "status": status, "limit": page_size,
                                            "offset": offset, "paginate": "true"})
            if r is None:
                break
            data = r.json()
            page = data["items"] if isinstance(data, dict) else data
            items.extend(page)
            total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
            offset += len(page)
            if not page or offset >= total:
                break
        return items

    async def get_pricing(self, model_id: str) -> dict | None:
        r = await self._get(f"/models/{model_id}/pricing")
        return None if r is None else r.json()
```
Note: the `list_models` test builds the query string in the order `category, status, limit, offset`; keep the params dict in that order (httpx preserves insertion order) and note `paginate=true` is appended last, so the test's `startswith` match still works.

- [ ] **Step 6: Run tests** → `uv run pytest tests/test_pricing.py tests/test_catalog_api.py -q` → all pass. Then `uv run ruff check . && uv run ruff format .`.
- [ ] **Step 7: Commit** → `git add vjhstudio/runware/pricing.py vjhstudio/runware/catalog_api.py tests/fixtures tests/test_pricing.py tests/test_catalog_api.py && git commit -m "feat: content catalog client and price normalization" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 2: Catalog service, curated snapshot, boot seed and background refresh

**Files:**
- Create: `vjhstudio/data/__init__.py` (empty), `vjhstudio/data/curated_models.json`, `vjhstudio/services/catalog.py`, `tests/test_catalog_service.py`
- Modify: `vjhstudio/boot.py` (seed after the default project), `vjhstudio/web/app.py` (background refresh in lifespan), `pyproject.toml` (`[tool.hatch.build.targets.wheel] include` so the json ships; add `"include" = ["vjhstudio/**/*.json"]` only if hatch excludes it — check with `uv build --wheel -o /tmp/x && unzip -l /tmp/x/*.whl | grep curated`).

**Interfaces:**
- Consumes: `pricing.normalize_price`, `pricing.kind_for_category`, `catalog_api.ContentAPI`, `models.CatalogModel`, `models.utcnow`, `services.meta.get/set`, `db.session_scope`.
- Produces (all in `vjhstudio/services/catalog.py`):
  - `CURATED_PATH: Path`, `SEED_VERSION_KEY = "catalog_seed_version"`, `REFRESH_KEY = "catalog_prices_refreshed_at"`
  - `is_utility_slug(slug: str | None) -> bool`
  - `family(model: CatalogModel) -> str` → `"instruction" | "diffusion" | "video"`
  - `label(model: CatalogModel) -> str` (formats in Global Constraints)
  - `upsert_row(session, row: dict, source: str) -> CatalogModel` — `row` is the normalized shape `{air, slug, name, kind, creator, architecture, capabilities, price: {unit, primary, price_in, price_out, tiers}, provider_settings_schema?, defaults?: {width,height,steps,cfg}, hero_image_url?, raw?}`; matches on `air`; never overwrites `is_favourite`/`is_hidden` once set by the user (tracked with a `user_hidden` marker: only set `is_hidden` when the row is new); updates `last_seen_at`.
  - `seed_curated(session, force: bool = False) -> int` — reads `curated_models.json`, upserts with `source="curated"` when `meta.get(SEED_VERSION_KEY)` < file version (or force), sets the version; returns number of rows touched.
  - `@dataclass RefreshResult(models: int, priced: int, errors: list[str], finished_at: datetime)`
  - `async refresh_from_content_api(session_factory, api: ContentAPI, concurrency: int = 5) -> RefreshResult` — for each kind in (image, video, text): `list_models(category)`, then `get_pricing(slug)` for each with an `asyncio.Semaphore(concurrency)`; upsert with `source="content"`, `price_source="content_api"`; hidden by `is_utility_slug` on insert; sets `REFRESH_KEY` to now ISO; per-model failures are collected in `errors` and never abort the run.
  - `last_refreshed(session) -> datetime | None`, `needs_refresh(session, max_age_days: int = 7) -> bool`
  - `list_models(session, kind: str, include_hidden: bool = False) -> list[CatalogModel]` ordered `price_primary DESC NULLS LAST, name ASC`.
  - `get_by_air(session, air: str) -> CatalogModel | None`, `set_flag(session, model_id: int, field: Literal["is_favourite","is_hidden"], value: bool | None = None) -> CatalogModel` (None toggles).

- [ ] **Step 1: Write the curated snapshot** (`vjhstudio/data/curated_models.json`; prices verified 2026-09-20 from RunWare's catalog; unknown prices are `null`)

```json
{
  "version": 1,
  "models": [
    {"air": "runware:101@1", "slug": "bfl-flux-1-dev", "name": "FLUX.1 [dev]", "kind": "image", "creator": "black-forest-labs", "architecture": "flux-1-dev",
     "capabilities": ["io:text-to-image", "io:image-to-image"], "defaults": {"width": 1024, "height": 1024, "steps": 28, "cfg": 3.5},
     "price": {"unit": "per_image", "primary": 0.0038, "tiers": {"measured": [{"configuration": "1024x1024 · 28 steps", "price": 0.0038}, {"configuration": "512x512 · 28 steps", "price": 0.0019}], "typical_latency_ms": 2942}}},
    {"air": "runware:100@1", "slug": "bfl-flux-1-schnell", "name": "FLUX.1 [schnell]", "kind": "image", "creator": "black-forest-labs", "architecture": "flux-1-schnell",
     "capabilities": ["io:text-to-image"], "defaults": {"width": 1024, "height": 1024, "steps": 4, "cfg": 1.0}, "price": {"unit": "per_image", "primary": null, "tiers": {}}},
    {"air": "google:4@2", "slug": "google-nano-banana-pro", "name": "Nano Banana Pro", "kind": "image", "creator": "google", "architecture": "gemini",
     "capabilities": ["io:text-to-image", "io:image-to-image"], "defaults": {"width": 1024, "height": 1024}, "price": {"unit": "per_image", "primary": 0.138, "tiers": {"measured": [{"configuration": "1K", "price": 0.138}, {"configuration": "2K", "price": 0.138}, {"configuration": "4K", "price": 0.244}]}}},
    {"air": "google:4@3", "slug": "google-nano-banana-2", "name": "Nano Banana 2", "kind": "image", "creator": "google", "architecture": "gemini",
     "capabilities": ["io:text-to-image", "io:image-to-image"], "defaults": {"width": 1024, "height": 1024}, "price": {"unit": "per_image", "primary": null, "tiers": {}}},
    {"air": "openai:gpt-image@2", "slug": "openai-gpt-image-2", "name": "GPT Image 2", "kind": "image", "creator": "openai", "architecture": "gpt",
     "capabilities": ["io:text-to-image", "io:image-to-image"], "defaults": {"width": 1024, "height": 1024}, "price": {"unit": "per_image", "primary": null, "tiers": {}}},
    {"air": "google:3@2", "slug": "google-veo-3-1", "name": "Veo 3.1", "kind": "video", "creator": "google", "architecture": "veo",
     "capabilities": ["io:text-to-video", "io:image-to-video"], "price": {"unit": "per_second", "primary": 0.2, "tiers": {"rates": [{"amount": 0.2, "unit": "durationSecond", "label": "720p / 1080p · without audio"}, {"amount": 0.4, "unit": "durationSecond", "label": "720p / 1080p · with audio"}], "typical_latency_ms": 89836}},
     "provider_settings_schema": [{"key": "generateAudio", "label": "Generate audio", "type": "bool", "default": true}],
     "video": {"durations": [4, 6, 8], "resolutions": ["720p", "1080p", "4K"], "frame_roles": ["first", "last"]}},
    {"air": "klingai:kling-video@3-standard", "slug": "klingai-video-3-0-standard", "name": "Kling 3.0 Standard", "kind": "video", "creator": "klingai", "architecture": "kling",
     "capabilities": ["io:text-to-video", "io:image-to-video"], "price": {"unit": "per_second", "primary": 0.084, "tiers": {"rates": [{"amount": 0.084, "unit": "durationSecond", "label": "720p · without audio"}, {"amount": 0.126, "unit": "durationSecond", "label": "720p · with audio"}]}},
     "provider_settings_schema": [{"key": "sound", "label": "Sound", "type": "bool", "default": false}],
     "video": {"durations": [3, 5, 8, 10, 15], "resolutions": ["720p"], "frame_roles": ["first", "last"]}},
    {"air": "bytedance:seedance@2.0", "slug": "bytedance-seedance-2-0", "name": "Seedance 2.0", "kind": "video", "creator": "bytedance", "architecture": "seedance",
     "capabilities": ["io:text-to-video", "io:image-to-video"], "price": {"unit": "per_second", "primary": 0.16, "tiers": {"rates": [{"amount": 0.07, "unit": "durationSecond", "label": "480p"}, {"amount": 0.16, "unit": "durationSecond", "label": "720p"}, {"amount": 0.4, "unit": "durationSecond", "label": "1080p"}]}},
     "video": {"durations": [4, 5, 8, 10, 15], "resolutions": ["480p", "720p", "1080p", "4k"], "frame_roles": ["first", "last"]}},
    {"air": "alibaba:wan@2.7", "slug": "alibaba-wan2-7", "name": "Wan 2.7", "kind": "video", "creator": "alibaba", "architecture": "wan",
     "capabilities": ["io:text-to-video", "io:image-to-video"], "price": {"unit": "per_second", "primary": 0.1, "tiers": {"rates": [{"amount": 0.1, "unit": "durationSecond", "label": "720p"}, {"amount": 0.15, "unit": "durationSecond", "label": "1080p"}]}},
     "video": {"durations": [2, 5, 8, 10, 15], "resolutions": ["720p", "1080p"], "frame_roles": ["first", "last"]}},
    {"air": "lightricks:ltx@2.3", "slug": "lightricks-ltx-2-3", "name": "LTX-2.3", "kind": "video", "creator": "lightricks", "architecture": "ltx",
     "capabilities": ["io:text-to-video", "io:image-to-video"], "price": {"unit": "per_second", "primary": 0.04, "tiers": {"rates": [{"amount": 0.04, "unit": "durationSecond", "label": "720p"}, {"amount": 0.08, "unit": "durationSecond", "label": "1080p"}]}},
     "video": {"durations": [3, 5, 8, 10], "resolutions": ["720p", "1080p"], "fps": [24, 25, 30], "frame_roles": ["first", "last"]}},
    {"air": "anthropic:claude@sonnet-4.6", "slug": "anthropic-claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "kind": "text", "creator": "anthropic", "architecture": "claude",
     "capabilities": ["io:text-to-text"], "price": {"unit": "per_1m_tokens", "primary": 18.0, "price_in": 3.0, "price_out": 15.0, "tiers": {}}},
    {"air": "google:gemini@3.5-flash", "slug": "google-gemini-3-5-flash", "name": "Gemini 3.5 Flash", "kind": "text", "creator": "google", "architecture": "gemini",
     "capabilities": ["io:text-to-text"], "price": {"unit": "per_1m_tokens", "primary": 10.5, "price_in": 1.5, "price_out": 9.0, "tiers": {}}},
    {"air": "openai:gpt@5.5", "slug": "openai-gpt-5-5", "name": "GPT-5.5", "kind": "text", "creator": "openai", "architecture": "gpt",
     "capabilities": ["io:text-to-text"], "price": {"unit": "per_1m_tokens", "primary": 35.0, "price_in": 5.0, "price_out": 30.0, "tiers": {}}},
    {"air": "google:gemma@4-31b", "slug": "google-gemma-4-31b", "name": "Gemma 4 31B", "kind": "text", "creator": "google", "architecture": "gemma",
     "capabilities": ["io:text-to-text"], "price": {"unit": "per_1m_tokens", "primary": 0.399, "price_in": 0.102, "price_out": 0.297, "tiers": {}}}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_catalog_service.py
import json
from datetime import timedelta
from pathlib import Path
import httpx, pytest
from vjhstudio import db, models
from vjhstudio.models import utcnow
from vjhstudio.runware.catalog_api import ContentAPI
from vjhstudio.services import catalog, meta, migrate

FIX = Path(__file__).parent / "fixtures"

@pytest.fixture
def factory(tmp_path):
    p = tmp_path / "c.db"; migrate.upgrade(p)
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
        assert catalog.label(catalog.get_by_air(s, "google:3@2")) == "Veo 3.1 — $0.200/s ($1.00/5 s)"
        assert catalog.label(catalog.get_by_air(s, "anthropic:claude@sonnet-4.6")) == "Claude Sonnet 4.6 — $3.00 in / $15.00 out per 1M"
        assert catalog.label(catalog.get_by_air(s, "runware:100@1")) == "FLUX.1 [schnell] — price unknown"
        fav = catalog.set_flag(s, catalog.get_by_air(s, "runware:101@1").id, "is_favourite", True)
        assert catalog.label(fav).startswith("★ ")

def test_family(factory):
    with db.session_scope(factory) as s:
        catalog.seed_curated(s)
        assert catalog.family(catalog.get_by_air(s, "runware:101@1")) == "diffusion"
        assert catalog.family(catalog.get_by_air(s, "google:4@2")) == "instruction"
        assert catalog.family(catalog.get_by_air(s, "openai:gpt-image@2")) == "instruction"
        assert catalog.family(catalog.get_by_air(s, "google:3@2")) == "video"

def test_utility_slugs_hidden():
    assert catalog.is_utility_slug("controlnet-preprocess-canny")
    assert catalog.is_utility_slug("birefnet-general")
    assert not catalog.is_utility_slug("bfl-flux-1-dev")

def _content_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, str(request.url.query, "utf-8")
        if path == "/models":
            if "category=image" in q:
                name = "content_list_image_p2.json" if "offset=2" in q else "content_list_image.json"
                return httpx.Response(200, json=json.loads((FIX / name).read_text()))
            return httpx.Response(200, json={"total": 0, "limit": 2, "offset": 0, "items": []})
        if path == "/models/bfl-flux-1-dev/pricing":
            return httpx.Response(200, json=json.loads((FIX / "content_pricing_flux.json").read_text()))
        if path == "/models/google-nano-banana-pro/pricing":
            return httpx.Response(500)
        return httpx.Response(404)
    return httpx.MockTransport(handler)

async def test_refresh_from_content_api(factory):
    with db.session_scope(factory) as s:
        catalog.seed_curated(s)
        fav = catalog.set_flag(s, catalog.get_by_air(s, "runware:101@1").id, "is_favourite", True)
    api = ContentAPI(transport=_content_transport())
    res = await catalog.refresh_from_content_api(factory, api)
    assert res.models == 3 and res.priced == 1 and len(res.errors) == 1
    with db.session_scope(factory) as s:
        flux = catalog.get_by_air(s, "runware:101@1")
        assert flux.source == "content" and flux.price_source == "content_api" and flux.is_favourite is True
        assert flux.price_tiers_json["typical_latency_ms"] == 2942
        banana = catalog.get_by_air(s, "google:4@2")
        assert banana.price_primary == 0.138  # examples fallback from the listing when pricing failed
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
```
Note for `test_list_models_sorted_by_price_desc_nulls_last`: the curated image list has null prices for schnell, Nano Banana 2 and GPT Image 2, so `None in prices` is guaranteed.

- [ ] **Step 3: Run to verify failure** → import error.

- [ ] **Step 4: Implement catalog.py**

```python
# vjhstudio/services/catalog.py
"""Model catalog: curated seed, content-API refresh, price-sorted listing, labels, families."""
from __future__ import annotations
import asyncio, json, logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from sqlalchemy import case, select
from sqlalchemy.orm import Session, sessionmaker
from .. import db
from ..models import CatalogModel, utcnow
from ..runware.catalog_api import ContentAPI, ContentAPIError
from ..runware.pricing import kind_for_category, normalize_price
from . import meta

log = logging.getLogger(__name__)
CURATED_PATH = Path(__file__).resolve().parent.parent / "data" / "curated_models.json"
SEED_VERSION_KEY = "catalog_seed_version"
REFRESH_KEY = "catalog_prices_refreshed_at"
KINDS = ("image", "video", "text")
_UTILITY = ("controlnet-preprocess", "birefnet", "rembg", "yolov8", "mediapipe", "age-", "clip-vit",
            "prompt-enhancer", "upscal", "esrgan", "swinir", "ccsr", "clarity", "remove-background",
            "background-removal", "eraser", "vectoriz", "lipsync", "lip-sync", "avatar", "enhancement",
            "object-remover", "layerize", "vto", "try-on")
_INSTRUCTION = ("gemini", "gpt", "openai", "nano-banana")


def is_utility_slug(slug: str | None) -> bool:
    s = (slug or "").lower()
    return any(k in s for k in _UTILITY)


def family(model: CatalogModel) -> str:
    if model.kind == "video":
        return "video"
    hay = " ".join(x or "" for x in (model.architecture, model.creator, model.air, model.slug)).lower()
    return "instruction" if any(k in hay for k in _INSTRUCTION) else "diffusion"


def label(model: CatalogModel) -> str:
    star = "★ " if model.is_favourite else ""
    p = model.price_primary
    if p is None:
        return f"{star}{model.name} — price unknown"
    if model.kind == "image":
        return f"{star}{model.name} — ${p:.4f}/img"
    if model.kind == "video":
        return f"{star}{model.name} — ${p:.3f}/s (${p * 5:.2f}/5 s)"
    if model.price_in is None or model.price_out is None:
        return f"{star}{model.name} — price unknown"
    return f"{star}{model.name} — ${model.price_in:.2f} in / ${model.price_out:.2f} out per 1M"


def get_by_air(session: Session, air: str) -> CatalogModel | None:
    return session.execute(select(CatalogModel).where(CatalogModel.air == air)).scalar_one_or_none()


def upsert_row(session: Session, row: dict, source: str) -> CatalogModel:
    price = row.get("price") or {}
    defaults = row.get("defaults") or {}
    m = get_by_air(session, row["air"])
    new = m is None
    if new:
        m = CatalogModel(air=row["air"], name=row.get("name") or row["air"], kind=row["kind"], source=source)
        m.is_hidden = bool(row.get("hidden", False))
        session.add(m)
    m.slug = row.get("slug") or m.slug
    m.name = row.get("name") or m.name
    m.kind = row.get("kind") or m.kind
    m.creator = row.get("creator") or m.creator
    m.architecture = row.get("architecture") or m.architecture
    m.capabilities_json = list(row.get("capabilities") or m.capabilities_json or [])
    m.default_width = defaults.get("width", m.default_width)
    m.default_height = defaults.get("height", m.default_height)
    m.default_steps = defaults.get("steps", m.default_steps)
    m.default_cfg = defaults.get("cfg", m.default_cfg)
    m.hero_image_url = row.get("hero_image_url") or m.hero_image_url
    if row.get("provider_settings_schema") is not None:
        m.provider_settings_schema = row["provider_settings_schema"]
    if price:
        m.price_unit = price.get("unit") or m.price_unit
        m.price_primary = price.get("primary")
        m.price_in = price.get("price_in")
        m.price_out = price.get("price_out")
        tiers = dict(price.get("tiers") or {})
        if row.get("video"):
            tiers["video"] = row["video"]
        m.price_tiers_json = tiers
        m.price_source = row.get("price_source") or ("content_api" if source == "content" else "curated")
        m.price_updated_at = utcnow()
    m.source = source if new or source == "content" else m.source
    m.raw_json = row.get("raw") if row.get("raw") is not None else m.raw_json
    m.last_seen_at = utcnow()
    session.flush()
    return m


def seed_curated(session: Session, force: bool = False) -> int:
    data = json.loads(CURATED_PATH.read_text(encoding="utf-8"))
    version = str(data.get("version", 1))
    current = meta.get(session, SEED_VERSION_KEY)
    if not force and current is not None and int(current) >= int(version):
        return 0
    n = 0
    for row in data["models"]:
        upsert_row(session, row, source="curated")
        n += 1
    meta.set(session, SEED_VERSION_KEY, version)
    return n


@dataclass
class RefreshResult:
    models: int = 0
    priced: int = 0
    errors: list[str] = field(default_factory=list)
    finished_at: datetime | None = None


def _row_from_listing(item: dict, kind: str, pricing: dict | None) -> dict:
    info = normalize_price(kind, pricing, item)
    return {
        "air": item["air"], "slug": item.get("model"), "name": item.get("name") or item["air"], "kind": kind,
        "creator": item.get("creator"), "architecture": item.get("architecture"),
        "capabilities": item.get("capabilities") or [], "hero_image_url": item.get("coverImage"),
        "hidden": is_utility_slug(item.get("model")),
        "price": {"unit": info.unit, "primary": info.primary, "price_in": info.price_in,
                  "price_out": info.price_out, "tiers": info.tiers},
        "price_source": "content_api", "raw": item,
    }


async def refresh_from_content_api(session_factory: sessionmaker[Session], api: ContentAPI,
                                   concurrency: int = 5) -> RefreshResult:
    res = RefreshResult()
    sem = asyncio.Semaphore(concurrency)

    async def price_for(item: dict) -> dict | None:
        async with sem:
            try:
                return await api.get_pricing(item.get("model") or item["air"])
            except ContentAPIError as e:
                res.errors.append(f"{item.get('model')}: {e}")
                return None

    for kind in KINDS:
        try:
            items = await api.list_models(kind)
        except ContentAPIError as e:
            res.errors.append(f"list {kind}: {e}")
            continue
        items = [i for i in items if i.get("air")]
        pricings = await asyncio.gather(*(price_for(i) for i in items))
        with db.session_scope(session_factory) as s:
            for item, pricing in zip(items, pricings, strict=True):
                upsert_row(s, _row_from_listing(item, kind, pricing), source="content")
                res.models += 1
                if pricing is not None:
                    res.priced += 1
    res.finished_at = utcnow()
    with db.session_scope(session_factory) as s:
        meta.set(s, REFRESH_KEY, res.finished_at.isoformat())
    return res


def last_refreshed(session: Session) -> datetime | None:
    v = meta.get(session, REFRESH_KEY)
    return datetime.fromisoformat(v) if v else None


def needs_refresh(session: Session, max_age_days: int = 7) -> bool:
    at = last_refreshed(session)
    return at is None or (utcnow() - at) > timedelta(days=max_age_days)


def list_models(session: Session, kind: str, include_hidden: bool = False) -> list[CatalogModel]:
    q = select(CatalogModel).where(CatalogModel.kind == kind)
    if not include_hidden:
        q = q.where(CatalogModel.is_hidden.is_(False))
    q = q.order_by(case((CatalogModel.price_primary.is_(None), 1), else_=0),
                   CatalogModel.price_primary.desc(), CatalogModel.name.asc())
    return list(session.execute(q).scalars())


def set_flag(session: Session, model_id: int, field_name: Literal["is_favourite", "is_hidden"],
             value: bool | None = None) -> CatalogModel:
    m = session.get(CatalogModel, model_id)
    if m is None:
        raise LookupError(model_id)
    setattr(m, field_name, (not getattr(m, field_name)) if value is None else bool(value))
    session.flush()
    return m
```
Note on `upsert_row` price handling: when a refresh yields `primary=None` for a model that previously had a curated price, keep the old price: implement as `if price.get("primary") is not None or m.price_primary is None: m.price_primary = price.get("primary")` (same guard for `price_in`/`price_out`); the Nano Banana Pro assertion in the refresh test relies on the examples fallback (0.138 from the listing), not on this guard, but the guard is required by the spec ("observed cost refines the estimate", never blanks it).

- [ ] **Step 5: Wire boot and the background refresh**

In `vjhstudio/boot.py`, inside the existing `session_scope` block after the Default project: `from .services import catalog` … `catalog.seed_curated(s)` wrapped in `try/except Exception as e: log.warning("catalog seed skipped: %s", e)` (never blocks boot).

In `vjhstudio/web/app.py` lifespan, after boot:
```python
async def _maybe_refresh():
    try:
        with db.session_scope(app.state.boot.session_factory) as s:
            due = catalog.needs_refresh(s)
        if due and env.get("VJHSTUDIO_OFFLINE") != "1":
            await asyncio.sleep(3)
            res = await catalog.refresh_from_content_api(app.state.boot.session_factory, ContentAPI())
            log.info("catalog refreshed: %d models, %d priced, %d errors", res.models, res.priced, len(res.errors))
    except Exception as e:  # noqa: BLE001
        log.warning("catalog refresh skipped: %s", e)
task = asyncio.create_task(_maybe_refresh()) if app.state.auto_refresh else None
yield
if task: task.cancel()
```
Add `auto_refresh: bool = True` parameter to `create_app`; `tests/conftest.py` passes `auto_refresh=False` so tests never hit the network. Add `VJHSTUDIO_OFFLINE=1` to the README "For developers" line? No — keep it undocumented for now (an internal switch).

Add a boot test in `tests/test_boot.py`:
```python
def test_boot_seeds_catalog(paths):
    info = boot.boot(paths)
    with db.session_scope(info.session_factory) as s:
        assert s.query(models.CatalogModel).count() >= 10
```
- [ ] **Step 6: Run** → `uv run pytest -q` all green; ruff clean.
- [ ] **Step 7: Commit** → `git add vjhstudio/data vjhstudio/services/catalog.py vjhstudio/boot.py vjhstudio/web/app.py tests/conftest.py tests/test_catalog_service.py tests/test_boot.py pyproject.toml && git commit -m "feat: model catalog service with curated seed and content-API price refresh" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 3: Live model search through the SDK

**Files:**
- Modify: `vjhstudio/services/catalog.py` (add `search_live`, `add_from_search`), `tests/test_catalog_service.py`, `tests/fakes/fake_runware.py` (already has `model_search`)

**Interfaces:**
- Produces: `async search_live(client_factory, api_key: str, transport: str, query: str, kind: str, limit: int = 20) -> list[dict]` → each `{air, name, category, architecture, provider, capabilities, heroImage, shortDescription, raw}`; raises `SearchError(UserFacingError)` (subclass of Exception with `.error`) on SDK failure. `add_from_search(session, record: dict, kind: str) -> CatalogModel` (source `"search"`, price unknown, `raw` stored).

- [ ] **Step 1: Failing tests** (append to `tests/test_catalog_service.py`)

```python
from runware import RunwareError
from tests.fakes.fake_runware import FakeRunware, fake_factory

async def test_search_live_maps_results_and_add(factory):
    fake = FakeRunware({"model_search": [[{"results": [
        {"air": "civitai:4201@130090", "name": "Realistic Vision", "category": "checkpoint", "architecture": "sdxl",
         "provider": "civitai", "capabilities": ["io:text-to-image"], "heroImage": "https://x/y.png", "shortDescription": "photo"}
    ], "totalResults": 1}]]})
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
```

- [ ] **Step 2: Implement** (append to `catalog.py`)

```python
from ..runware.errors import UserFacingError, classify


class SearchError(Exception):
    def __init__(self, error: UserFacingError):
        super().__init__(error.message)
        self.error = error


_SEARCH_CATEGORY = {"image": "checkpoint", "video": "checkpoint", "text": "checkpoint"}


async def search_live(client_factory, api_key: str, transport: str, query: str, kind: str,
                      limit: int = 20) -> list[dict]:
    params = {"search": query, "category": _SEARCH_CATEGORY.get(kind, "checkpoint"),
              "visibility": "public", "limit": limit}
    try:
        async with client_factory(api_key, transport) as client:
            rows = await client.model_search(params)
    except Exception as e:  # noqa: BLE001
        raise SearchError(classify(e)) from e
    results: list[dict] = []
    for row in rows or []:
        for r in row.get("results") or []:
            if r.get("air"):
                results.append({"air": r["air"], "name": r.get("name") or r["air"], "category": r.get("category"),
                                "architecture": r.get("architecture"), "provider": r.get("provider"),
                                "capabilities": r.get("capabilities") or [], "heroImage": r.get("heroImage"),
                                "shortDescription": r.get("shortDescription"), "raw": r})
    return results


def add_from_search(session: Session, record: dict, kind: str) -> CatalogModel:
    return upsert_row(session, {
        "air": record["air"], "name": record.get("name"), "kind": kind, "creator": record.get("provider"),
        "architecture": record.get("architecture"), "capabilities": record.get("capabilities") or [],
        "hero_image_url": record.get("heroImage"), "price": {"unit": {"image": "per_image", "video": "per_second", "text": "per_1m_tokens"}[kind], "primary": None, "tiers": {}},
        "price_source": "search", "raw": record.get("raw"),
    }, source="search")
```
- [ ] **Step 3: Run** → green; ruff clean.
- [ ] **Step 4: Commit** → `git add vjhstudio/services/catalog.py tests/test_catalog_service.py && git commit -m "feat: live model search via runware modelSearch" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 4: Models page, price-sorted dropdowns, and Settings default-model selects

**Files:**
- Create: `vjhstudio/web/routes/catalog.py`, `vjhstudio/web/templates/pages/models.html`, `vjhstudio/web/templates/catalog/_list.html`, `vjhstudio/web/templates/catalog/_row.html`, `vjhstudio/web/templates/catalog/_search_results.html`, `vjhstudio/web/templates/catalog/_refresh_status.html`, `vjhstudio/web/templates/catalog/_model_select.html`, `tests/test_web_models.py`
- Modify: `vjhstudio/web/app.py` (include router), `vjhstudio/web/templates/_header.html` (nav link `Models` between Home and Settings), `vjhstudio/web/templates/settings/_general_form.html` (model fields → select), `vjhstudio/web/routes/settings.py` (`_general_ctx` adds `model_options`), `vjhstudio/web/static/css/app.css` (small table styles), `tests/test_web_settings.py`

**Interfaces:**
- Consumes: `catalog.list_models/label/family/set_flag/get_by_air/search_live/add_from_search/refresh_from_content_api/last_refreshed/SearchError`, `ContentAPI`, `deps.render`, `deps.Form`, `app.state.boot.session_factory`, `app.state.client_factory`, `app.state.api_key()`, `app.state.setting("runware.transport")`.
- Produces routes:
  | Method | Path | Returns |
  |---|---|---|
  | GET | `/models` | page with three tabs (image/video/text) each including `catalog/_list.html` |
  | GET | `/hx/models?kind=image&hidden=0` | `catalog/_list.html` (table of rows) |
  | POST | `/hx/models/search` form `q`, `kind` | `catalog/_search_results.html` (422 with error text when no key / SearchError) |
  | POST | `/models/add` form `kind`, `record` (JSON string of one search row) | `catalog/_list.html` for that kind + OOB toast "Added <name>" |
  | POST | `/models/refresh-prices` | `catalog/_refresh_status.html` (runs the refresh inline, up to ~60 s; message with counts; 422 with error on total failure) |
  | POST | `/models/{id}/favourite`, `/models/{id}/hide` | `catalog/_row.html` (404 JSON if unknown id) |
  | GET | `/api/models?kind=image` | JSON list `[{id, air, name, kind, label, price_primary, price_unit, price_in, price_out, family, is_favourite, is_hidden, capabilities, tiers}]` |
- `catalog/_model_select.html` macro-like partial: expects `name` (form field), `options` (list of CatalogModel), `selected` (air), `allow_empty` (bool); renders `<select name="{{ name }}">` with `<option value="{{ m.air }}">{{ label }}</option>` in the given (price-sorted) order; when `selected` is not among options, an extra first option `"{{ selected }} (not in catalog)"` is rendered selected so a saved value is never silently lost.
- `settings/_general_form.html`: for keys `defaults.image_model`, `defaults.video_model`, `defaults.polish_model` render `_model_select.html` with `options = model_options[kind]` (`image`/`video`/`text`), `allow_empty` True only for the polish model (`<option value="">(use promptEnhance)</option>`).

- [ ] **Step 1: Failing tests**

```python
# tests/test_web_models.py
import json
from runware import RunwareError

async def test_models_page_lists_three_kinds_sorted(client):
    r = await client.get("/models")
    assert r.status_code == 200 and 'id="models-image"' in r.text and 'id="models-video"' in r.text and 'id="models-text"' in r.text
    body = r.text
    assert body.index("GPT-5.5") < body.index("Claude Sonnet 4.6") < body.index("Gemini 3.5 Flash")  # dearest first
    assert "Veo 3.1 — $0.200/s ($1.00/5 s)" in body

async def test_hx_models_partial_hides_hidden_by_default(client):
    r = await client.get("/hx/models?kind=image")
    assert r.status_code == 200 and "FLUX.1 [dev]" in r.text

async def test_api_models_json(client):
    r = await client.get("/api/models?kind=video")
    rows = r.json()
    assert rows[0]["air"] == "google:3@2" and rows[0]["family"] == "video" and rows[0]["label"].startswith("Veo 3.1")
    assert rows[-1]["price_primary"] is None or rows[-1]["price_primary"] <= rows[0]["price_primary"]

async def test_favourite_and_hide_toggle(client):
    rows = (await client.get("/api/models?kind=image")).json()
    flux = next(r for r in rows if r["air"] == "runware:101@1")
    r = await client.post(f"/models/{flux['id']}/favourite")
    assert r.status_code == 200 and "★" in r.text
    r = await client.post(f"/models/{flux['id']}/hide")
    assert r.status_code == 200
    assert all(x["air"] != "runware:101@1" for x in (await client.get("/api/models?kind=image")).json())
    assert any(x["air"] == "runware:101@1" for x in (await client.get("/api/models?kind=image&hidden=1")).json())
    assert (await client.post("/models/999999/hide")).status_code == 404

async def test_search_requires_key(client):
    r = await client.post("/hx/models/search", data={"q": "x", "kind": "image"})
    assert r.status_code == 422 and "API key" in r.text

async def test_search_and_add(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["model_search"] = [[{"results": [{"air": "civitai:1@2", "name": "Dreamy", "category": "checkpoint",
                                                   "architecture": "sdxl", "provider": "civitai", "capabilities": []}]}]]
    r = await client.post("/hx/models/search", data={"q": "dream", "kind": "image"})
    assert r.status_code == 200 and "Dreamy" in r.text and 'name="record"' in r.text
    record = json.dumps({"air": "civitai:1@2", "name": "Dreamy", "architecture": "sdxl", "provider": "civitai", "capabilities": []})
    r = await client.post("/models/add", data={"kind": "image", "record": record})
    assert r.status_code == 200 and "Dreamy" in r.text and "Added" in r.text
    assert any(x["air"] == "civitai:1@2" for x in (await client.get("/api/models?kind=image")).json())

async def test_search_error_422(client, fake):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})
    fake.script["model_search"] = [RunwareError("invalidApiKey", "bad")]
    r = await client.post("/hx/models/search", data={"q": "x", "kind": "image"})
    assert r.status_code == 422 and "rejected the API key" in r.text

async def test_refresh_prices_uses_injected_api(app, client, monkeypatch):
    from vjhstudio.models import utcnow
    from vjhstudio.services import catalog
    async def fake_refresh(session_factory, api, concurrency=5):
        return catalog.RefreshResult(models=3, priced=2, errors=["x: boom"], finished_at=utcnow())
    monkeypatch.setattr(catalog, "refresh_from_content_api", fake_refresh)
    r = await client.post("/models/refresh-prices")
    assert r.status_code == 200 and "3 models" in r.text and "2 priced" in r.text and "1 error" in r.text
    r = await client.get("/models")
    assert "Last refreshed" in r.text

async def test_settings_default_models_are_selects(client):
    r = await client.get("/settings")
    assert '<select name="defaults.image_model">' in r.text and '<select name="defaults.video_model">' in r.text
    assert '<select name="defaults.polish_model">' in r.text and "(use promptEnhance)" in r.text
    assert 'value="runware:101@1" selected' in r.text
    # a saved value that is not in the catalog is preserved
    await client.post("/settings", data={"defaults.image_model": "runware:101@1"})
    r = await client.post("/settings", data={"defaults.video_model": "someone:custom@1"})
    assert r.status_code == 200 and "someone:custom@1 (not in catalog)" in r.text
```

- [ ] **Step 2: Run** → failures (routes and templates missing).

- [ ] **Step 3: Implement routes**

```python
# vjhstudio/web/routes/catalog.py
from __future__ import annotations
import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from ... import db
from ...runware.catalog_api import ContentAPI
from ...services import catalog
from .. import deps

router = APIRouter()
KINDS = ("image", "video", "text")


def _rows(request: Request, kind: str, include_hidden: bool) -> list[dict]:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        ms = catalog.list_models(s, kind, include_hidden=include_hidden)
        return [_view(m) for m in ms]


def _view(m) -> dict:
    return {"id": m.id, "air": m.air, "name": m.name, "kind": m.kind, "label": catalog.label(m),
            "price_primary": m.price_primary, "price_unit": m.price_unit, "price_in": m.price_in,
            "price_out": m.price_out, "family": catalog.family(m), "is_favourite": m.is_favourite,
            "is_hidden": m.is_hidden, "capabilities": m.capabilities_json or [], "tiers": m.price_tiers_json or {},
            "source": m.source, "creator": m.creator}


def _list_ctx(request: Request, kind: str, include_hidden: bool = False) -> dict:
    return {"kind": kind, "rows": _rows(request, kind, include_hidden), "include_hidden": include_hidden}


def _refresh_ctx(request: Request, message: str | None = None, error: str | None = None) -> dict:
    with db.session_scope(request.app.state.boot.session_factory) as s:
        at = catalog.last_refreshed(s)
    return {"last_refreshed": at, "message": message, "error": error}


@router.get("/models")
def models_page(request: Request):
    ctx = {"lists": {k: _list_ctx(request, k) for k in KINDS}, **_refresh_ctx(request)}
    return deps.render(request, "pages/models.html", ctx)


@router.get("/hx/models")
def hx_models(request: Request, kind: str = "image", hidden: int = 0):
    kind = kind if kind in KINDS else "image"
    return deps.render(request, "catalog/_list.html", _list_ctx(request, kind, bool(hidden)))


@router.get("/api/models")
def api_models(request: Request, kind: str = "image", hidden: int = 0):
    return JSONResponse(_rows(request, kind if kind in KINDS else "image", bool(hidden)))


@router.post("/hx/models/search")
async def hx_search(request: Request, form: deps.Form):
    q, kind = str(form.get("q", "")).strip(), str(form.get("kind", "image"))
    key = request.app.state.api_key()
    if not key:
        return deps.render(request, "catalog/_search_results.html", {"error": "Add your RunWare API key in Settings to search.", "results": [], "kind": kind}, 422)
    if not q:
        return deps.render(request, "catalog/_search_results.html", {"results": [], "kind": kind, "empty": True})
    try:
        results = await catalog.search_live(request.app.state.client_factory, key,
                                            request.app.state.setting("runware.transport"), q, kind)
    except catalog.SearchError as e:
        return deps.render(request, "catalog/_search_results.html", {"error": e.error.message, "results": [], "kind": kind}, 422)
    for r in results:
        r["record_json"] = json.dumps({k: v for k, v in r.items() if k != "raw"})
    return deps.render(request, "catalog/_search_results.html", {"results": results, "kind": kind})


@router.post("/models/add")
def add_model(request: Request, form: deps.Form):
    kind = str(form.get("kind", "image"))
    try:
        record = json.loads(str(form.get("record", "{}")))
        assert isinstance(record, dict) and record.get("air")
    except (ValueError, AssertionError):
        return JSONResponse({"error": "bad record"}, status_code=400)
    with db.session_scope(request.app.state.boot.session_factory) as s:
        m = catalog.add_from_search(s, record, kind)
        name = m.name
    ctx = _list_ctx(request, kind)
    ctx["toast"] = f"Added {name}"
    return deps.render(request, "catalog/_list.html", ctx)


@router.post("/models/refresh-prices")
async def refresh_prices(request: Request):
    try:
        res = await catalog.refresh_from_content_api(request.app.state.boot.session_factory, ContentAPI())
    except Exception as e:  # noqa: BLE001
        return deps.render(request, "catalog/_refresh_status.html", _refresh_ctx(request, error=f"Refresh failed: {e}"), 422)
    msg = f"Refreshed {res.models} models, {res.priced} priced, {len(res.errors)} error{'s' if len(res.errors) != 1 else ''}."
    return deps.render(request, "catalog/_refresh_status.html", _refresh_ctx(request, message=msg))


def _toggle(request: Request, model_id: int, field: str):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        try:
            m = catalog.set_flag(s, model_id, field)  # type: ignore[arg-type]
        except LookupError:
            return JSONResponse({"error": "unknown model"}, status_code=404)
        view = _view(m)
    return deps.render(request, "catalog/_row.html", {"m": view, "kind": view["kind"]})


@router.post("/models/{model_id}/favourite")
def favourite(request: Request, model_id: int):
    return _toggle(request, model_id, "is_favourite")


@router.post("/models/{model_id}/hide")
def hide(request: Request, model_id: int):
    return _toggle(request, model_id, "is_hidden")
```
Register in `create_app`: `from .routes import catalog as catalog_routes` … `app.include_router(catalog_routes.router)`.

- [ ] **Step 4: Templates**

`pages/models.html`:
```html
{% extends "base.html" %}
{% block title %}Models · VJHStudio{% endblock %}
{% block content %}
<h1>Models</h1>
<p>Sorted by price, most expensive first. Prices come from RunWare's public catalog.</p>
<section id="refresh">{% include "catalog/_refresh_status.html" %}</section>
<section id="search">
  <h2>Find more models</h2>
  <form hx-post="/hx/models/search" hx-target="#search-results" hx-swap="innerHTML">
    <div class="grid">
      <input type="search" name="q" placeholder="Search RunWare models (e.g. realistic, anime)" aria-label="Search">
      <select name="kind" aria-label="Kind"><option value="image">image</option><option value="video">video</option><option value="text">text</option></select>
      <button type="submit">Search</button>
    </div>
  </form>
  <div id="search-results"></div>
</section>
{% for kind, ctx in lists.items() %}
<section id="models-{{ kind }}">
  <h2>{{ kind|capitalize }} models</h2>
  <label><input type="checkbox" hx-get="/hx/models?kind={{ kind }}&hidden=1" hx-trigger="change[this.checked]" hx-target="#list-{{ kind }}" hx-swap="outerHTML"
         onchange="if(!this.checked){htmx.ajax('GET','/hx/models?kind={{ kind }}',{target:'#list-{{ kind }}',swap:'outerHTML'})}"> show hidden</label>
  {% with kind=ctx.kind, rows=ctx.rows, include_hidden=ctx.include_hidden %}{% include "catalog/_list.html" %}{% endwith %}
</section>
{% endfor %}
{% endblock %}
```
`catalog/_list.html`:
```html
<div id="list-{{ kind }}">
{% if toast %}{% with text=toast %}{% include "partials/_toast.html" %}{% endwith %}{% endif %}
<table class="models">
  <thead><tr><th>Model</th><th>Price</th><th>Family</th><th>Source</th><th></th></tr></thead>
  <tbody>
  {% for m in rows %}{% include "catalog/_row.html" %}{% else %}<tr><td colspan="5">No {{ kind }} models yet. Use the search above.</td></tr>{% endfor %}
  </tbody>
</table>
</div>
```
`catalog/_row.html` (expects `m` as the dict from `_view`):
```html
<tr id="model-{{ m.id }}" class="{% if m.is_hidden %}hidden-model{% endif %}">
  <td><strong>{{ m.name }}</strong><br><small><code>{{ m.air }}</code>{% if m.creator %} · {{ m.creator }}{% endif %}</small></td>
  <td>{{ m.label.split(' — ', 1)[1] if ' — ' in m.label else '' }}</td>
  <td>{{ m.family }}</td>
  <td>{{ m.source }}</td>
  <td>
    <button class="secondary outline" hx-post="/models/{{ m.id }}/favourite" hx-target="#model-{{ m.id }}" hx-swap="outerHTML" title="Favourite">{{ '★' if m.is_favourite else '☆' }}</button>
    <button class="secondary outline" hx-post="/models/{{ m.id }}/hide" hx-target="#model-{{ m.id }}" hx-swap="outerHTML" title="Hide/show">{{ 'Show' if m.is_hidden else 'Hide' }}</button>
  </td>
</tr>
```
`catalog/_search_results.html`:
```html
{% if error %}<p class="error">{{ error }}</p>{% endif %}
{% if empty %}<p><small>Type something to search.</small></p>{% endif %}
{% if results %}
<table class="models"><tbody>
{% for r in results %}
<tr>
  <td><strong>{{ r.name }}</strong><br><small><code>{{ r.air }}</code>{% if r.provider %} · {{ r.provider }}{% endif %}{% if r.shortDescription %} · {{ r.shortDescription }}{% endif %}</small></td>
  <td>
    <form hx-post="/models/add" hx-target="#list-{{ kind }}" hx-swap="outerHTML">
      <input type="hidden" name="kind" value="{{ kind }}">
      <input type="hidden" name="record" value='{{ r.record_json }}'>
      <button type="submit">Add</button>
    </form>
  </td>
</tr>
{% endfor %}
</tbody></table>
{% elif not error and not empty %}<p><small>No results.</small></p>{% endif %}
```
Note: `record_json` contains `"` characters, so the attribute uses single quotes and Jinja autoescaping (enabled by Starlette's Jinja2Templates) escapes `'` inside; verify in the browser that Add works.
`catalog/_refresh_status.html`:
```html
<form id="refresh-form" hx-post="/models/refresh-prices" hx-target="this" hx-swap="outerHTML" hx-indicator="#refresh-spinner">
  <button type="submit" class="secondary">Refresh prices</button>
  <span id="refresh-spinner" class="htmx-indicator" aria-busy="true">Refreshing…</span>
  <small>Last refreshed: {% if last_refreshed %}{{ last_refreshed.strftime('%Y-%m-%d %H:%M') }} UTC{% else %}never (using the shipped snapshot){% endif %}</small>
  {% if message %}<p class="ok">{{ message }}</p>{% endif %}
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
</form>
```
`catalog/_model_select.html` (expects `name`, `options`, `selected`, `allow_empty`):
```html
<select name="{{ name }}">
  {% if allow_empty %}<option value="" {% if not selected %}selected{% endif %}>(use promptEnhance)</option>{% endif %}
  {% set airs = options|map(attribute='air')|list %}
  {% if selected and selected not in airs %}<option value="{{ selected }}" selected>{{ selected }} (not in catalog)</option>{% endif %}
  {% for m in options %}<option value="{{ m.air }}" {% if m.air == selected %}selected{% endif %}>{{ labels[m.air] }}</option>{% endfor %}
</select>
```
In `routes/settings.py::_general_ctx`, add `model_options = {k: catalog.list_models(s, k) for k in ("image","video","text")}` (inside the same session; the objects are detached afterwards but `expire_on_commit=False` keeps their attributes) and `labels = {m.air: catalog.label(m) for k in model_options for m in model_options[k]}`; pass both. In `settings/_general_form.html` replace the `{% else %}<input …>` branch with:
```html
{% elif key in ("defaults.image_model", "defaults.video_model", "defaults.polish_model") %}
  {% set kind = {"defaults.image_model": "image", "defaults.video_model": "video", "defaults.polish_model": "text"}[key] %}
  {% with name=key, options=model_options[kind], selected=values[key], allow_empty=(key == "defaults.polish_model") %}{% include "catalog/_model_select.html" %}{% endwith %}
{% else %}
  <input name="{{ key }}" value="{{ values[key] }}" {% if sp.type.__name__ == 'int' %}type="number"{% endif %}>
```
(`labels` is available to the include through the enclosing context.)
Header: add `<li><a href="/models">Models</a></li>` after Home. CSS: `table.models td{vertical-align:top} tr.hidden-model{opacity:.55}`.

- [ ] **Step 5: Run** → `uv run pytest -q` green; ruff + format clean. Smoke: `VJHSTUDIO_DATA_DIR=$PWD/data-debug uv run vjhstudio serve --port 8098 --no-browser &`, `curl -s localhost:8098/models | grep -c "models-video"` = 1, `curl -s "localhost:8098/api/models?kind=text" | head -c 300`, then `curl -s -X POST localhost:8098/api/shutdown`.
- [ ] **Step 6: Commit** → `git add vjhstudio/web tests/test_web_models.py tests/test_web_settings.py && git commit -m "feat: Models page with price-sorted lists, live search, refresh and default-model selects" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

## Phase 2 exit criteria

- `/models` shows image, video and text tables sorted most expensive first with prices in the labels; Refresh prices pulls the live catalog (hundreds of models) and stamps "Last refreshed"; hidden utility models stay out of the dropdowns.
- Settings → General shows the three default-model dropdowns in price order.
- `/api/models?kind=video` returns JSON that Phase 3 will use for the Generate form.
- Offline: a fresh install still has the curated models (seeded at boot) and the app never blocks on the network.
