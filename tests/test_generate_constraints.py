"""The Generate page read through ``services/constraints``: size selects, duration
inputs, dropdown filtering, the first-frame badge and the two pre-flight refusals.

Rows are seeded straight through the session factory so each test owns the exact
``constraints_json`` it needs; the curated catalog ships none until Task 7.
"""

import pytest

from vjhstudio import db, models
from vjhstudio.runware.tasks import build_video_task
from vjhstudio.schemas.video import VideoRequest

KLING_4K = "klingai:kling-video@3-4k"
VEO = "vjh:veo-values@1"
ALEPH = "runwayml:aleph@2"
FIRST_ONLY = "vjh:i2v-only@1"
NANO = "vjh:nano-banana@1"

KLING_C = {
    "dims": {
        "mode": "list",
        "list": [[3840, 2160], [2160, 3840], [2880, 2880]],
        "labels": {"3840x2160": "4K (16:9)"},
    },
    "duration": {"min": 3, "max": 15, "step": 1, "default": 5},
}
VEO_C = {"dims": {"mode": "unknown"}, "duration": {"values": [4, 6, 7, 8], "default": 4}}
ALEPH_C = {
    "params": ["positivePrompt", "inputs.video"],
    "inputs": {"video": {"required": True}},
    "dims": {"mode": "unknown"},
}
NANO_C = {
    "dims": {
        "mode": "list",
        "list": [[1024, 1024], [1344, 768]],
        "labels": {"1024x1024": "Square", "1344x768": "Wide 16:9"},
    }
}

V2V_ONLY = ["io:video-to-video"]
T2V = ["io:text-to-video", "io:image-to-video"]
I2V_ONLY = ["io:image-to-video"]

FORM = {
    "project_id": "1",
    "model": KLING_4K,
    "subject": "a fox running",
    "duration": "5",
    "output_format": "MP4",
}


def _add(app, air, kind, name, caps, constraints_json, price=0.1):
    with db.session_scope(app.state.boot.session_factory) as s:
        row = models.CatalogModel(
            air=air,
            name=name,
            kind=kind,
            source="curated",
            capabilities_json=list(caps),
            constraints_json=constraints_json,
            price_primary=price,
            price_unit="per_second" if kind == "video" else "per_image",
        )
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture
def seeded(app):
    _add(app, KLING_4K, "video", "Kling 4K", T2V, KLING_C)
    _add(app, VEO, "video", "Veo Values", T2V, VEO_C)
    _add(app, ALEPH, "video", "Aleph 2.0", V2V_ONLY, ALEPH_C)
    _add(app, FIRST_ONLY, "video", "FrameStart", I2V_ONLY, None)
    _add(app, NANO, "image", "Nano Banana Pro", ["io:text-to-image"], NANO_C)
    return app


async def _key(client):
    await client.post("/settings/api-key", data={"api_key": "abcdefgh1234"})


# ---- parameter panel -----------------------------------------------------
async def test_video_list_mode_renders_a_size_select_and_hidden_pixels(client, seeded):
    r = await client.get(f"/hx/model-options?mode=video&air={KLING_4K}")
    assert r.status_code == 200
    assert "3840×2160" in r.text and "4K (16:9)" in r.text
    assert '<option value="3840x2160"' in r.text
    assert "1280x720" not in r.text and "720p" not in r.text
    assert '<select name="resolution"' not in r.text  # the tier rides hidden instead
    assert '<input type="hidden" name="width" value="3840">' in r.text
    assert '<input type="hidden" name="height" value="2160">' in r.text
    assert '<input type="hidden" name="resolution" value="4K">' in r.text
    assert 'data-res="4K"' in r.text


async def test_video_rule_duration_renders_a_number_input(client, seeded):
    r = await client.get(f"/hx/model-options?mode=video&air={KLING_4K}")
    assert '<input type="number" name="duration" min="3" max="15" step="1" value="5"' in r.text
    assert r.text.count('name="duration"') == 1


async def test_video_duration_values_render_a_select(client, seeded):
    r = await client.get(f"/hx/model-options?mode=video&air={VEO}")
    assert '<select name="duration"' in r.text
    opts = [line for line in r.text.split("<option") if "s</option>" in line]
    assert [o.split('value="')[1].split('"')[0] for o in opts] == ["4", "6", "7", "8"]


async def test_unknown_constraints_keep_the_resolution_select(client):
    """A curated row carries no constraints yet: today's behaviour, unchanged."""
    r = await client.get("/hx/model-options?mode=video&air=lightricks:ltx@2.3")
    assert r.status_code == 200
    assert '<select name="resolution">' in r.text
    assert 'name="size"' not in r.text
    assert '<select name="duration"' in r.text


async def test_image_list_mode_presets_and_readonly_pixels(client, seeded):
    r = await client.get(f"/hx/model-options?mode=image&air={NANO}")
    assert r.status_code == 200
    assert '<option value="1024x1024"' in r.text and '<option value="1344x768"' in r.text
    assert "Wide 16:9 — 1344×768" in r.text
    assert "Portrait 3:4" not in r.text  # the generic presets are gone in list mode
    assert r.text.count("readonly") == 2


async def test_image_rule_mode_carries_min_max_step(client, app):
    _add(
        app,
        "vjh:ruled@1",
        "image",
        "Ruled",
        ["io:text-to-image"],
        {"dims": {"mode": "rule", "min": 256, "max": 1536, "step": 32}},
    )
    r = await client.get("/hx/model-options?mode=image&air=vjh:ruled@1")
    assert 'step="32" min="256" max="1536"' in r.text
    assert "readonly" not in r.text


async def test_first_frame_warning_in_the_parameters_panel(client, seeded):
    r = await client.get(f"/hx/model-options?mode=video&air={FIRST_ONLY}")
    assert '<p class="warn">needs a first frame</p>' in r.text
    r = await client.get(f"/hx/model-options?mode=video&air={VEO}")
    assert "needs a first frame" not in r.text


# ---- dropdown ------------------------------------------------------------
async def test_dropdown_drops_video_only_editors_and_flags_i2v_rows(client, seeded):
    r = await client.get("/generate/video")
    assert r.status_code == 200
    assert "Aleph 2.0" not in r.text
    assert "FrameStart — $0.100/s ($0.50/5 s) · needs a first frame" in r.text
    assert "Kling 4K" in r.text


async def test_badges(client, seeded, app):
    from vjhstudio.services import catalog

    with db.session_scope(app.state.boot.session_factory) as s:
        assert (
            catalog.badge(catalog.get_by_air(s, ALEPH)) == "video-to-video only — not supported yet"
        )
        assert catalog.badge(catalog.get_by_air(s, FIRST_ONLY)) == "needs a first frame"
        assert catalog.badge(catalog.get_by_air(s, VEO)) == ""
        assert catalog.label(catalog.get_by_air(s, FIRST_ONLY)).endswith(" · needs a first frame")


async def test_list_generate_models_filters(client, seeded, app):
    from vjhstudio.services import catalog

    with db.session_scope(app.state.boot.session_factory) as s:
        airs = [m.air for m in catalog.list_generate_models(s, "video")]
        assert ALEPH not in airs and KLING_4K in airs and FIRST_ONLY in airs
        assert ALEPH in [m.air for m in catalog.list_models(s, "video")]


# ---- pre-flight ----------------------------------------------------------
async def test_video_to_video_model_is_refused(client, seeded):
    await _key(client)
    r = await client.post("/generate/video", data={**FORM, "model": ALEPH})
    assert r.status_code == 422
    assert "This model edits an existing video. VJHStudio cannot supply one yet." in r.text


async def test_i2v_only_model_needs_a_first_frame(client, seeded, app):
    await _key(client)
    r = await client.post("/generate/video", data={**FORM, "model": FIRST_ONLY})
    assert r.status_code == 422
    assert "This model needs a first-frame image. Add one under References." in r.text

    with db.session_scope(app.state.boot.session_factory) as s:
        asset = models.Asset(
            filename="a.png",
            original_name="a.png",
            kind="image",
            mime="image/png",
            size_bytes=10,
            sha256="d" * 64,
        )
        s.add(asset)
        s.flush()
        asset_id = asset.id
    r = await client.post(
        "/generate/video",
        data={**FORM, "model": FIRST_ONLY, "first_frame_asset_id": str(asset_id)},
    )
    assert r.status_code == 200
    with db.session_scope(app.state.boot.session_factory) as s:
        assert s.query(models.Job).filter_by(model_air=FIRST_ONLY).count() == 1


# ---- posted pixels reach the task ----------------------------------------
async def test_posted_width_height_ride_into_the_job(client, seeded, app):
    await _key(client)
    r = await client.post("/generate/video", data={**FORM, "width": "3840", "height": "2160"})
    assert r.status_code == 200
    with db.session_scope(app.state.boot.session_factory) as s:
        job = s.query(models.Job).filter_by(model_air=KLING_4K).one()
        assert job.request_json["width"] == 3840 and job.request_json["height"] == 2160


def test_build_video_task_prefers_posted_pixels():
    req = VideoRequest(project_id=1, model=KLING_4K, width=3840, height=2160, resolution="720p")
    task = build_video_task(req, "u", {}, {})
    assert (task["width"], task["height"]) == (3840, 2160)
    plain = VideoRequest(project_id=1, model=KLING_4K, resolution="720p")
    task = build_video_task(plain, "u", {}, {})
    assert (task["width"], task["height"]) == (1280, 720)


def test_video_request_rejects_tiny_pixels():
    with pytest.raises(ValueError):
        VideoRequest(project_id=1, model=KLING_4K, width=32, height=2160)


async def test_video_rule_mode_keeps_names_but_posts_snapped_pixels(client, app):
    """The bug this phase exists to stop: "720p" on a multiple-of-64 model was sent as
    1280x720 and rejected. The name still rides (the estimate prices by tier), the
    pixels are snapped to the grid."""
    _add(
        app,
        "vjh:ruled-video@1",
        "video",
        "Ruled Video",
        T2V,
        {"dims": {"mode": "rule", "min": 128, "max": 2048, "step": 64}},
    )
    r = await client.get("/hx/model-options?mode=video&air=vjh:ruled-video@1")
    assert '<select name="resolution"' in r.text
    assert 'value="720p" data-w="1280" data-h="704"' in r.text
    assert 'value="1080p" data-w="1920" data-h="1088"' in r.text
    assert '<input type="hidden" name="width" value="1280">' in r.text
    assert '<input type="hidden" name="height" value="704">' in r.text


# ---- the estimate still prices a list-mode size by its tier ----------------
TIERED = "vjh:tiered@1"
TIERED_C = {
    "dims": {
        "mode": "list",
        "list": [[1280, 720], [1920, 1080]],
        "labels": {"1280x720": "HD (720p)", "1920x1080": "Full HD (1080p)"},
    }
}
TIERED_RATES = {
    "video": {"durations": [5], "resolutions": ["720p", "1080p"]},
    "rates": [
        {"label": "720p", "amount": 0.10},
        {"label": "1080p", "amount": 0.40},
    ],
}


def _add_tiered(app):
    with db.session_scope(app.state.boot.session_factory) as s:
        s.add(
            models.CatalogModel(
                air=TIERED,
                name="Tiered",
                kind="video",
                source="curated",
                capabilities_json=list(T2V),
                constraints_json=TIERED_C,
                price_tiers_json=TIERED_RATES,
                price_primary=0.10,
                price_unit="per_second",
            )
        )


async def test_list_mode_sizes_carry_their_tier_name(client, app):
    _add_tiered(app)
    r = await client.get(f"/hx/model-options?mode=video&air={TIERED}")
    assert 'data-res="720p"' in r.text and 'data-res="1080p"' in r.text
    # the panel opens on the first listed size, so the hidden tier is that one's
    assert '<input type="hidden" name="resolution" value="720p">' in r.text


async def test_estimate_prices_the_chosen_list_size_by_its_tier(client, app):
    _add_tiered(app)
    base = f"/hx/generate/estimate?mode=video&air={TIERED}&duration=5"
    assert "≈$0.50" in (await client.get(f"{base}&resolution=720p")).text
    assert "≈$2.00" in (await client.get(f"{base}&resolution=1080p")).text


def test_tier_name_falls_back_to_the_shorter_side():
    from vjhstudio.web.routes.generate import _tier_name

    assert _tier_name(3840, 2160, "4K (16:9)") == "4K"
    assert _tier_name(2160, 3840, "") == "4K"  # portrait: shorter side is 2160
    assert _tier_name(1920, 1080, "") == "1080p"
    assert _tier_name(1280, 720, "") == "720p"
    assert _tier_name(854, 480, "") == "480p"
    assert _tier_name(1920, 1080, "Full HD (1080p)") == "1080p"


def test_build_video_task_snaps_an_off_list_size():
    """A remix or a hand-edited post can carry a size the model never offered; the
    constraints snap it before the wire instead of spending a rejected round trip."""
    row = {"constraints": {"dims": {"mode": "list", "list": [[3840, 2160], [2880, 2880]]}}}
    req = VideoRequest(project_id=1, model=KLING_4K, width=1280, height=720)
    task = build_video_task(req, "u", {}, row)
    assert (task["width"], task["height"]) == (3840, 2160)
    ruled = {"constraints": {"dims": {"mode": "rule", "min": 128, "max": 2048, "step": 64}}}
    req = VideoRequest(project_id=1, model=KLING_4K, width=1280, height=720)
    task = build_video_task(req, "u", {}, ruled)
    assert (task["width"], task["height"]) == (1280, 704)
