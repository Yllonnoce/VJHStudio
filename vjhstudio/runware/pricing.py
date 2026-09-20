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
        if (
            cfg.startswith("1024x1024")
            and "lora" not in cfg.lower()
            and isinstance(row.get("price"), (int, float))
        ):
            return float(row["price"])
    for row in measured:
        if isinstance(row.get("price"), (int, float)):
            return float(row["price"])
    return _examples_min(examples)


def _video_primary(rates: list[dict]) -> float | None:
    secs = [
        r
        for r in rates
        if r.get("unit") == "durationSecond" and isinstance(r.get("amount"), (int, float))
    ]
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
    tiers = {
        "measured": measured,
        "rates": rates,
        "examples": examples,
        "typical_latency_ms": _latency(examples),
    }
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
