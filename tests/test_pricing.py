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
    row = {
        "pricingRates": [
            {"amount": 0.5, "unit": "durationSecond", "label": "4K"},
            {"amount": 0.3, "unit": "durationSecond", "label": "1080p"},
        ]
    }
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
