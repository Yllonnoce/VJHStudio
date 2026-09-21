"""Model catalog: curated seed, content-API refresh, price-sorted listing, labels, families."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import case, select
from sqlalchemy.orm import Session, sessionmaker

from .. import db
from ..models import CatalogModel, utcnow
from ..runware.catalog_api import ContentAPI, ContentAPIError
from ..runware.errors import UserFacingError, classify
from ..runware.pricing import normalize_price
from . import meta

log = logging.getLogger(__name__)
CURATED_PATH = Path(__file__).resolve().parent.parent / "data" / "curated_models.json"
SEED_VERSION_KEY = "catalog_seed_version"
REFRESH_KEY = "catalog_prices_refreshed_at"
KINDS = ("image", "video", "text")
_UTILITY = (
    "controlnet-preprocess",
    "birefnet",
    "rembg",
    "yolov8",
    "mediapipe",
    "age-",
    "clip-vit",
    "prompt-enhancer",
    "upscal",
    "esrgan",
    "swinir",
    "ccsr",
    "clarity",
    "remove-background",
    "background-removal",
    "eraser",
    "vectoriz",
    "lipsync",
    "lip-sync",
    "avatar",
    "enhancement",
    "object-remover",
    "layerize",
    "vto",
    "try-on",
)
_INSTRUCTION = ("gemini", "gpt", "openai", "nano-banana")


def is_utility_slug(slug: str | None) -> bool:
    s = (slug or "").lower()
    return any(k in s for k in _UTILITY)


def family(model: CatalogModel) -> str:
    if model.kind == "video":
        return "video"
    if model.kind == "text":
        return "text"
    hay = " ".join(
        x or "" for x in (model.architecture, model.creator, model.air, model.slug)
    ).lower()
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


def view(m: CatalogModel) -> dict:
    """The plain-dict form of a catalog row, as ``/api/models`` serves it. Also what the
    task builders read (``tiers.video``, ``provider_settings_schema``, ``capabilities``),
    so the runner never hands an ORM object to ``runware/``."""
    return {
        "id": m.id,
        "air": m.air,
        "name": m.name,
        "kind": m.kind,
        "label": label(m),
        "price_primary": m.price_primary,
        "price_unit": m.price_unit,
        "price_in": m.price_in,
        "price_out": m.price_out,
        "family": family(m),
        "is_favourite": m.is_favourite,
        "is_hidden": m.is_hidden,
        "capabilities": m.capabilities_json or [],
        "tiers": m.price_tiers_json or {},
        "source": m.source,
        "creator": m.creator,
        "provider_settings_schema": m.provider_settings_schema or [],
        "default_width": m.default_width,
        "default_height": m.default_height,
        "default_steps": m.default_steps,
        "default_cfg": m.default_cfg,
        "slug": m.slug,
        "architecture": m.architecture,
    }


def get_by_air(session: Session, air: str) -> CatalogModel | None:
    return session.execute(select(CatalogModel).where(CatalogModel.air == air)).scalar_one_or_none()


def upsert_row(session: Session, row: dict, source: str) -> CatalogModel:
    price = row.get("price") or {}
    defaults = row.get("defaults") or {}
    m = get_by_air(session, row["air"])
    new = m is None
    if new:
        m = CatalogModel(
            air=row["air"], name=row.get("name") or row["air"], kind=row["kind"], source=source
        )
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
        # Guard: an observed price refines the estimate, it never blanks a price we already
        # had (e.g. a curated price surviving a content-API row that couldn't be priced).
        if price.get("primary") is not None or m.price_primary is None:
            m.price_primary = price.get("primary")
        if price.get("price_in") is not None or m.price_in is None:
            m.price_in = price.get("price_in")
        if price.get("price_out") is not None or m.price_out is None:
            m.price_out = price.get("price_out")
        new_tiers = dict(price.get("tiers") or {})
        old_tiers = dict(m.price_tiers_json or {})
        for key in ("video", "observed"):
            # Curated video presets and observed rolling costs the content API never
            # supplies: a refresh must not silently delete them.
            if key in old_tiers and key not in new_tiers:
                new_tiers[key] = old_tiers[key]
        if row.get("video"):
            new_tiers["video"] = row["video"]
        m.price_tiers_json = new_tiers
        m.price_source = row.get("price_source") or (
            "content_api" if source == "content" else "curated"
        )
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
        "air": item["air"],
        "slug": item.get("model"),
        "name": item.get("name") or item["air"],
        "kind": kind,
        "creator": item.get("creator"),
        "architecture": item.get("architecture"),
        "capabilities": item.get("capabilities") or [],
        "hero_image_url": item.get("coverImage"),
        "hidden": is_utility_slug(item.get("model")),
        "price": {
            "unit": info.unit,
            "primary": info.primary,
            "price_in": info.price_in,
            "price_out": info.price_out,
            "tiers": info.tiers,
        },
        "price_source": "content_api",
        "raw": item,
    }


async def refresh_from_content_api(
    session_factory: sessionmaker[Session], api: ContentAPI, concurrency: int = 5
) -> RefreshResult:
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
    if res.models > 0:
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
    q = q.order_by(
        case((CatalogModel.price_primary.is_(None), 1), else_=0),
        CatalogModel.price_primary.desc(),
        CatalogModel.name.asc(),
    )
    return list(session.execute(q).scalars())


def set_flag(
    session: Session,
    model_id: int,
    field_name: Literal["is_favourite", "is_hidden"],
    value: bool | None = None,
) -> CatalogModel:
    m = session.get(CatalogModel, model_id)
    if m is None:
        raise LookupError(model_id)
    setattr(m, field_name, (not getattr(m, field_name)) if value is None else bool(value))
    session.flush()
    return m


class SearchError(Exception):
    def __init__(self, error: UserFacingError):
        super().__init__(error.message)
        self.error = error


_SEARCH_CATEGORY = {"image": "checkpoint", "video": "checkpoint", "text": "checkpoint"}


async def search_live(
    client_factory, api_key: str, transport: str, query: str, kind: str, limit: int = 20
) -> list[dict]:
    params = {
        "search": query,
        "category": _SEARCH_CATEGORY.get(kind, "checkpoint"),
        "visibility": "public",
        "limit": limit,
    }
    try:
        async with client_factory(api_key, transport) as client:
            rows = await client.model_search(params)
    except Exception as e:  # noqa: BLE001
        raise SearchError(classify(e)) from e
    results: list[dict] = []
    for row in rows or []:
        for r in row.get("results") or []:
            if r.get("air"):
                results.append(
                    {
                        "air": r["air"],
                        "name": r.get("name") or r["air"],
                        "category": r.get("category"),
                        "architecture": r.get("architecture"),
                        "provider": r.get("provider"),
                        "capabilities": r.get("capabilities") or [],
                        "heroImage": r.get("heroImage"),
                        "shortDescription": r.get("shortDescription"),
                        "raw": r,
                    }
                )
    return results


def add_from_search(session: Session, record: dict, kind: str) -> CatalogModel:
    existing = get_by_air(session, record["air"])
    if existing is not None:
        # A search "add" must never rewrite an already-known row's kind, price or tiers
        # (curated or content-priced rows in particular) — just bump last_seen_at.
        existing.last_seen_at = utcnow()
        session.flush()
        return existing
    return upsert_row(
        session,
        {
            "air": record["air"],
            "name": record.get("name"),
            "kind": kind,
            "creator": record.get("provider"),
            "architecture": record.get("architecture"),
            "capabilities": record.get("capabilities") or [],
            "hero_image_url": record.get("heroImage"),
            "price": {
                "unit": {"image": "per_image", "video": "per_second", "text": "per_1m_tokens"}[
                    kind
                ],
                "primary": None,
                "tiers": {},
            },
            "price_source": "search",
            "raw": record.get("raw"),
        },
        source="search",
    )
