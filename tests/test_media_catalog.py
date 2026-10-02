"""Music & SFX, speech and 3D in the catalog: price units, the audio/speech split, the
refresh over RunWare's `audio` and `3d` categories, capability rules and harvested fields.
Listing rows are trimmed copies of the public content API (2026-10-01)."""

from __future__ import annotations

import pytest

from vjhstudio import db
from vjhstudio.runware import pricing
from vjhstudio.services import catalog
from vjhstudio.services import constraints as C

MUSIC = {
    "air": "minimax:music@2.6",
    "model": "minimax-music-2-6",
    "name": "MiniMax Music 2.6",
    "capabilities": ["io:text-to-audio", "form:checkpoint"],
    "pricingOverview": " $0.15 per song generation or per music cover",
    "pricingRates": [{"amount": 0.15, "unit": "output"}],
}
COVER = {
    "air": "minimax:music@cover",
    "model": "minimax-music-cover",
    "name": "MiniMax Music Cover",
    "capabilities": ["io:audio-to-audio", "form:checkpoint"],
    "pricingRates": [{"amount": 0.15, "unit": "output"}],
}
SFX = {
    "air": "mirelo:1@1",
    "model": "mirelo-sfx-1-5",
    "name": "Mirelo SFX 1.5",
    "capabilities": ["io:text-to-audio", "io:video-to-audio", "form:checkpoint"],
    "pricingRates": [{"amount": 0.00025, "unit": "durationSecond"}],
}
ACE = {
    "air": "runware:ace-step@v1.5-turbo",
    "model": "ace-step-v1-5-turbo",
    "name": "ACE-Step v1.5 Turbo",
    "capabilities": ["io:text-to-audio", "io:audio-to-audio", "form:checkpoint"],
    "pricingOverview": "$0.0001 per second of audio",
    "pricingRates": None,
}
XAI = {
    "air": "xai:tts@0",
    "model": "xai-tts",
    "name": "xAI Text-to-Speech",
    "capabilities": ["io:text-to-audio", "form:checkpoint"],
    "pricingRates": [{"amount": 1.5e-05, "unit": "character"}],
}
FISH = {
    "air": "fishaudio:s2.1@pro",
    "model": "fish-audio-s2-1-pro",
    "name": "Fish Audio S2.1 Pro",
    "capabilities": ["io:text-to-audio", "io:audio-to-audio", "form:checkpoint"],
    "pricingRates": [{"amount": 1.5e-05, "unit": "utf8Byte"}],
}
GEMINI = {
    "air": "google:gemini@3.1-flash-tts",
    "model": "google-gemini-3-1-flash-tts",
    "name": "Gemini 3.1 Flash TTS",
    "capabilities": ["io:text-to-audio", "form:checkpoint"],
    "pricingRates": [
        {"amount": 1e-06, "unit": "inputToken"},
        {"amount": 2e-05, "unit": "outputToken"},
    ],
}
DIA = {
    "air": "runware:dia2@2b",
    "model": "dia2-2b",
    "name": "Dia2 2B",
    "capabilities": ["io:text-to-audio", "io:audio-to-audio", "form:checkpoint"],
    "pricingRates": None,
}
SEED_AUDIO = {
    "air": "bytedance:seed-audio@1.0",
    "model": "bytedance-seed-audio-1-0",
    "name": "Seed Audio 1.0",
    "capabilities": ["io:text-to-audio", "io:audio-to-audio", "form:checkpoint"],
    "pricingRates": [{"amount": 0.00263333, "unit": "durationSecond"}],
}
QWEN = {
    "air": "alibaba:qwen@3-tts-1.7b-voicedesign",
    "model": "alibaba-qwen3-tts-1-7b-voicedesign",
    "name": "Qwen3-TTS 1.7B VoiceDesign",
    "capabilities": ["io:text-to-audio", "form:checkpoint"],
    "pricingRates": [{"amount": 1.5e-05, "unit": "character"}],
}
TRIPO = {
    "air": "tripo:v3.1@0",
    "model": "tripo-v3-1",
    "name": "Tripo 3D v3.1",
    "capabilities": ["io:text-to-3d", "io:image-to-3d", "form:checkpoint"],
    "pricingRates": [
        {"amount": 0.3, "unit": "output", "label": "Text-to-3D"},
        {"amount": 0.4, "unit": "output", "label": "Image-to-3D"},
    ],
}
TRELLIS = {
    "air": "microsoft:trellis-2@4b",
    "model": "microsoft-trellis-2",
    "name": "TRELLIS.2",
    "capabilities": ["io:image-to-3d", "form:checkpoint"],
    "pricingOverview": "Pricing is based on processing time.",
    "pricingRates": None,
}


# ---- prices ---------------------------------------------------------------------------
def test_a_song_and_a_3d_object_are_priced_per_output():
    p = pricing.normalize_price("audio", None, MUSIC)
    assert (p.unit, p.primary) == ("per_output", 0.15)
    p = pricing.normalize_price("3d", None, TRIPO)
    assert (p.unit, p.primary) == ("per_output", 0.3)  # the cheapest listed mode
    assert p.tiers["rates"][1]["label"] == "Image-to-3D"  # kept for the estimate


def test_audio_by_the_second_from_a_rate_or_from_the_overview_text():
    p = pricing.normalize_price("audio", None, SFX)
    assert (p.unit, p.primary) == ("per_second", 0.00025)
    # ACE-Step ships no rate rows at all, only "$0.0001 per second of audio"
    p = pricing.normalize_price("audio", None, ACE)
    assert (p.unit, p.primary) == ("per_second", 0.0001)


def test_speech_is_priced_per_thousand_characters():
    p = pricing.normalize_price("speech", None, XAI)
    assert p.unit == "per_1k_chars" and p.primary == pytest.approx(0.015)
    p = pricing.normalize_price("speech", None, FISH)  # UTF-8 bytes: close enough to chars
    assert p.unit == "per_1k_chars" and p.primary == pytest.approx(0.015)
    # token-priced speech cannot be estimated from the text alone
    p = pricing.normalize_price("speech", None, GEMINI)
    assert p.primary is None
    assert pricing.normalize_price("3d", None, TRELLIS).primary is None


def test_the_pricing_endpoint_wins_over_the_listing_but_the_overview_still_counts():
    p = pricing.normalize_price("audio", {"pricingRates": []}, ACE)
    assert (p.unit, p.primary) == ("per_second", 0.0001)


# ---- audio or speech ----------------------------------------------------------------------
def test_runware_s_audio_category_is_split_into_music_and_speech():
    assert [catalog.audio_kind(i) for i in (MUSIC, COVER, SFX, ACE, SEED_AUDIO)] == ["audio"] * 5
    assert [catalog.audio_kind(i) for i in (XAI, FISH, GEMINI, DIA, QWEN)] == ["speech"] * 5
    # once the docs page has been read, its fields are the authority either way
    assert catalog.audio_kind(DIA, {"fields": {"positivePrompt": {}}}) == "audio"
    assert catalog.audio_kind(MUSIC, {"fields": {"speech.text": {"required": True}}}) == "speech"
    # Seed Audio lists speech.volume/speed/pitch but takes a prompt, not a text to read
    assert catalog.audio_kind(SEED_AUDIO, {"fields": {"speech.volume": {}}}) == "audio"


class _API:
    """The slice of ContentAPI the refresh uses."""

    def __init__(self, by_category: dict[str, list[dict]]):
        self.by_category = by_category
        self.asked: list[str] = []

    async def list_models(self, category: str, status: str = "live", page_size: int = 100):
        self.asked.append(category)
        return list(self.by_category.get(category, []))

    async def get_pricing(self, model_id: str):
        return None


async def test_refresh_files_audio_speech_and_3d_under_their_own_kinds(client, app):
    api = _API({"audio": [MUSIC, XAI, COVER], "3d": [TRIPO]})
    f = app.state.boot.session_factory
    res = await catalog.refresh_from_content_api(f, api)
    assert api.asked == ["image", "video", "text", "audio", "3d"]
    assert res.models == 4
    with db.session_scope(f) as s:
        music = catalog.get_by_air(s, "minimax:music@2.6")
        assert (music.kind, music.price_unit, music.price_primary) == ("audio", "per_output", 0.15)
        assert catalog.get_by_air(s, "xai:tts@0").kind == "speech"
        assert catalog.get_by_air(s, "tripo:v3.1@0").kind == "3d"
        assert catalog.label(music) == "MiniMax Music 2.6 — $0.150 each"
        assert catalog.label(catalog.get_by_air(s, "xai:tts@0")).endswith("$0.015 per 1,000 chars")
        airs = [m.air for m in catalog.list_generate_models(s, "audio")]
        assert "minimax:music@2.6" in airs and "minimax:music@cover" not in airs
        assert catalog.badge(catalog.get_by_air(s, "minimax:music@cover")) == (
            "needs an audio track — not supported yet"
        )


# ---- capability rules ---------------------------------------------------------------------
def test_generate_capability_for_the_new_kinds():
    assert C.is_generate_capable("audio", ["io:text-to-audio"], None) is True
    assert C.is_generate_capable("audio", ["io:audio-to-audio"], None) is False
    assert C.is_generate_capable("speech", ["io:text-to-audio"], None) is True
    assert C.is_generate_capable("speech", ["io:audio-to-audio"], None) is False
    assert C.is_generate_capable("3d", ["io:image-to-3d"], None) is True
    assert C.is_generate_capable("3d", ["io:text-to-3d"], None) is True
    assert C.is_generate_capable("3d", [], None) is True  # search-added: let the runner find out
    # an input image is something the app CAN supply (from Assets)
    trellis = {"inputs": {"image": {"required": True}, "meshFile": {"required": False}}}
    assert C.is_generate_capable("3d", ["io:image-to-3d"], trellis) is True
    assert C.unsupported_reason("audio", ["io:audio-to-audio"], None) == "audio"
    assert C.unsupported_reason("audio", ["io:text-to-audio"], None) is None
    needs_clip = {"inputs": {"audio": {"required": True}}}
    assert C.is_generate_capable("audio", ["io:text-to-audio"], needs_clip) is False


# ---- harvested fields -------------------------------------------------------------------------
DOCS = {
    "params": {
        "taskType": {"type": "string", "required": True},
        "taskUUID": {"type": "string", "required": True},
        "outputType": {"type": "string", "values": ["URL"]},
        "includeCost": {"type": "boolean"},
        "model": {"type": "string", "required": True},
        "audioSettings.bitrate": {"type": "integer", "min": 8},
        "outputFormat": {"type": "string", "default": "MP3", "values": ["MP3", "WAV"]},
        "positivePrompt": {"type": "string", "required": True, "min": 2, "max": 3000},
        "duration": {"type": "float", "min": 30, "max": 300, "default": 60},
        "settings.lyrics": {"type": "string", "min": 10, "max": 3000},
        "settings.bpm": {"type": "integer", "min": 30, "max": 300},
    },
    "inputs": {"audio": {"required": False}},
    "dims": [],
}


def test_the_docs_harvest_keeps_every_field_the_form_can_use():
    out = C.merge_sources(None, docs=DOCS, api=None, now="t")
    assert set(out["fields"]) == {
        "outputFormat",
        "positivePrompt",
        "duration",
        "settings.lyrics",
        "settings.bpm",
    }
    assert out["fields"]["duration"] == {"type": "float", "min": 30, "max": 300, "default": 60}
    assert C.media_fields(out)["settings.lyrics"]["max"] == 3000
    assert C.media_fields(None) == {} and C.media_fields({"fields": "junk"}) == {}
    # a later docs pass replaces the block wholesale (a parameter RunWare removed is gone)
    again = C.merge_sources(
        out, docs={"params": {"positivePrompt": {}}, "inputs": {}}, api=None, now="t2"
    )
    assert set(again["fields"]) == {"positivePrompt"}


# ---- the snapshot a fresh install starts with ----------------------------------------
async def test_a_fresh_install_can_make_music_speech_and_3d(client, app):
    """No refresh, no harvest: the curated snapshot alone carries the models, their
    fields and their prices, and the tabs open on a model that works."""
    from vjhstudio.services import media_forms

    with db.session_scope(app.state.boot.session_factory) as s:
        for kind, expect in (("audio", 8), ("speech", 10), ("3d", 6)):
            capable = catalog.list_generate_models(s, kind)
            assert len(capable) >= expect, (kind, len(capable))
            air = media_forms.default_air(kind, capable)
            assert air == media_forms.PREFERRED[kind]
            assert C.media_fields(catalog.get_by_air(s, air).constraints_json)
        hidden = {
            "minimax:music@cover": "needs an audio track — not supported yet",
            "alibaba:qwen@3-tts-1.7b-base": "needs an audio track — not supported yet",
            "meta:sam@3d": "needs a mask image — not supported yet",
        }
        for air, badge in hidden.items():
            m = catalog.get_by_air(s, air)
            assert not C.is_generate_capable(m.kind, m.capabilities_json, m.constraints_json), air
            assert catalog.badge(m) == badge
        # TRELLIS is image-only and stays usable: an image is something Assets can supply
        trellis = catalog.get_by_air(s, "microsoft:trellis-2@4b")
        assert C.is_generate_capable("3d", trellis.capabilities_json, trellis.constraints_json)
        voices = C.media_fields(catalog.get_by_air(s, "inworld:tts@2").constraints_json)
        assert len(voices["speech.voice"]["values"]) > 100
