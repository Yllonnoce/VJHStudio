from vjhstudio.services import constraints as C

KLING = {
    "dims": {
        "mode": "list",
        "list": [[3840, 2160], [2160, 3840], [2880, 2880]],
        "labels": {"3840x2160": "4K (16:9)"},
    },
    "duration": {"min": 3, "max": 15, "step": 1, "default": 5},
}
LTX = {
    "dims": {"mode": "rule", "min": 128, "max": 2048, "step": 64},
    "duration": {"min": 1, "max": 20, "type": "float"},
}
VEO = {"dims": {"mode": "unknown"}, "duration": {"values": [4, 6, 7, 8], "default": 4}}
ALEPH = {"params": ["positivePrompt", "inputs.video"], "inputs": {"video": {"required": True}}}
PRESETS = [(1280, 720, "720p"), (1920, 1080, "1080p"), (1000, 700, "odd")]


def test_size_options_list_mode_is_exactly_the_list_with_labels():
    opts = C.size_options(KLING, "video", PRESETS)
    assert [(o["w"], o["h"]) for o in opts] == [(3840, 2160), (2160, 3840), (2880, 2880)]
    assert opts[0]["label"] == "4K (16:9) — 3840×2160"
    assert opts[2]["label"] == "2880×2880"


def test_size_options_rule_mode_keeps_only_presets_that_fit():
    opts = C.size_options(LTX, "video", PRESETS)
    assert [(o["w"], o["h"]) for o in opts] == [(1280, 704), (1920, 1088)]  # snapped to 64


def test_size_options_unknown_returns_fallback():
    assert [(o["w"], o["h"]) for o in C.size_options(VEO, "video", PRESETS)] == [
        (1280, 720),
        (1920, 1080),
        (1000, 700),
    ]
    assert C.size_options(None, "image", PRESETS)[0]["w"] == 1280


def test_nearest_size_prefers_aspect_then_area():
    assert C.nearest_size(KLING, 1280, 720) == (3840, 2160)
    assert C.nearest_size(KLING, 720, 1280) == (2160, 3840)
    assert C.nearest_size(KLING, 1000, 1000) == (2880, 2880)
    assert C.nearest_size(LTX, 1280, 720) == (1280, 704)
    assert C.nearest_size(LTX, 100, 5000) == (128, 2048)
    assert C.nearest_size(VEO, 1280, 720) == (1280, 720)


def test_duration_spec_shapes():
    assert C.duration_spec(VEO) == {"values": [4, 6, 7, 8], "default": 4}
    assert C.duration_spec(KLING) == {"min": 3, "max": 15, "step": 1, "default": 5}
    assert C.duration_spec(None) == {}


def test_input_requirements_and_capabilities():
    assert C.requires_input_video(ALEPH) is True
    assert C.requires_input_video(KLING) is False
    assert C.can_start_from_text(["io:text-to-video"]) is True
    assert C.needs_first_frame(["io:image-to-video"], None) is True
    assert C.needs_first_frame(["io:text-to-video", "io:image-to-video"], None) is False
    assert C.is_generate_capable("video", ["io:video-to-video", "op:edit"], None) is False
    assert C.is_generate_capable("video", ["io:image-to-video"], None) is True
    assert (
        C.is_generate_capable("video", ["io:text-to-video"], ALEPH) is False
    )  # required input video wins
    assert C.is_generate_capable("image", [], None) is True


def test_merge_precedence_api_over_docs_and_keys_never_invented():
    docs = {
        "params": {
            "width": {"min": 128, "max": 2048, "step": 64},
            "duration": {"min": 1, "max": 20},
        },
        "dims": [],
        "dim_labels": {},
        "inputs": {"frameImages": {"required": False, "max_items": 2}},
    }
    api = {
        "params": ["width", "height", "duration"],
        "dims": {"mode": "list", "list": [[1280, 704]]},
        "missing": [],
    }
    out = C.merge_sources(None, docs=docs, api=api, now="2026-09-22T10:00:00")
    assert out["dims"] == {"mode": "list", "list": [[1280, 704]], "labels": {}}
    assert out["params"] == ["width", "height", "duration"]
    assert out["duration"] == {"min": 1, "max": 20}
    assert out["inputs"] == {"frameImages": {"required": False, "max_items": 2}}
    assert out["sources"] == {
        "docs": "2026-09-22T10:00:00",
        "api": "2026-09-22T10:00:00",
        "observed": None,
    }
    assert "fps" not in out and "steps" not in out


def test_merge_docs_only_uses_docs_dims_and_rule():
    docs = {
        "params": {"width": {"min": 128, "max": 2048, "step": 64}},
        "dims": [],
        "dim_labels": {},
        "inputs": {},
    }
    out = C.merge_sources(None, docs=docs, api=None, now="t")
    assert out["dims"] == {"mode": "rule", "min": 128, "max": 2048, "step": 64}
    docs2 = {"params": {}, "dims": [[3840, 2160]], "dim_labels": {"3840x2160": "4K"}, "inputs": {}}
    assert C.merge_sources(None, docs=docs2, api=None, now="t")["dims"] == {
        "mode": "list",
        "list": [[3840, 2160]],
        "labels": {"3840x2160": "4K"},
    }
    assert C.merge_sources(None, docs=None, api=None, now="t")["dims"] == {"mode": "unknown"}


def test_merge_api_missing_required_marks_inputs():
    api = {"params": ["positivePrompt"], "dims": None, "missing": ["inputs.video"]}
    out = C.merge_sources(None, docs=None, api=api, now="t")
    assert out["inputs"]["video"] == {"required": True}


def test_observe_dims_overrides_and_stamps():
    out = C.observe_dims(KLING, {"mode": "list", "list": [[1280, 720]]}, now="t2")
    assert out["dims"]["list"] == [[1280, 720]] and out["sources"]["observed"] == "t2"
    assert out["duration"] == KLING["duration"]


def test_720p_preset_survives_the_rule_fit_filter():
    """1280x720 on a 64-step grid moves height by exactly step/4 (720 -> 704). That is the
    most common video preset, so the fit filter's boundary must keep it."""
    opts = C.size_options(LTX, "video", [(1280, 720, "720p")])
    assert [(o["w"], o["h"]) for o in opts] == [(1280, 704)]


# ---- the docs must never overwrite a stronger source ----------------------
DOCS_LIST = {"params": {}, "dims": [[1024, 1024]], "dim_labels": {"1024x1024": "Square"}}


def _existing(**sources) -> dict:
    return {
        "dims": {"mode": "list", "list": [[1280, 720]], "labels": {"1280x720": "720p"}},
        "sources": {"docs": "t0", "api": None, "observed": None, **sources},
    }


def test_docs_only_run_leaves_observed_dims_alone():
    """A run with no key (or a client that fell over) still refreshes the docs half; it
    must not undo a correction a real job paid for."""
    existing = _existing(observed="t1")
    out = C.merge_sources(existing, docs=DOCS_LIST, api=None, now="t2")
    assert out["dims"] == existing["dims"]
    assert out["sources"] == {"docs": "t2", "api": None, "observed": "t1"}


def test_docs_only_run_leaves_api_dims_alone():
    existing = _existing(api="t1")
    out = C.merge_sources(existing, docs=DOCS_LIST, api=None, now="t2")
    assert out["dims"] == existing["dims"]
    assert out["sources"] == {"docs": "t2", "api": "t1", "observed": None}


def test_a_fresh_api_result_still_beats_the_docs_and_the_older_api_dims():
    api = {"params": ["width"], "dims": {"mode": "list", "list": [[1920, 1080]]}, "missing": []}
    out = C.merge_sources(_existing(api="t1"), docs=DOCS_LIST, api=api, now="t2")
    assert out["dims"]["list"] == [[1920, 1080]]
    assert out["sources"] == {"docs": "t2", "api": "t2", "observed": None}


def test_docs_dims_still_fill_a_row_no_stronger_source_has_touched():
    out = C.merge_sources(
        {"sources": {"docs": "t0", "api": None, "observed": None}},
        docs=DOCS_LIST,
        api=None,
        now="t2",
    )
    assert out["dims"] == {
        "mode": "list",
        "list": [[1024, 1024]],
        "labels": {"1024x1024": "Square"},
    }


def test_observe_dims_keeps_the_labels_of_the_sizes_that_survive():
    """A size correction replaces the list; the curated tier names for the sizes still in
    it have to travel with it, or the estimate re-prices from the short-side heuristic."""
    existing = {
        "dims": {
            "mode": "list",
            "list": [[1280, 720], [832, 480]],
            "labels": {"1280x720": "720p", "832x480": "480p"},
        }
    }
    out = C.observe_dims(existing, {"mode": "list", "list": [[1280, 720]]}, now="t")
    assert out["dims"]["labels"] == {"1280x720": "720p"}
    assert out["dims"]["list"] == [[1280, 720]] and out["sources"]["observed"] == "t"


def test_docs_dims_do_not_replace_older_api_dims_when_this_probe_learned_nothing():
    existing = {
        "dims": {"mode": "list", "list": [[3840, 2160]], "labels": {}},
        "sources": {"docs": None, "api": "t1", "observed": None},
    }
    docs = {
        "params": {"width": {"min": 128, "max": 2048, "step": 64}},
        "dims": [],
        "dim_labels": {},
        "inputs": {},
    }
    api = {"params": ["width", "height"], "dims": {"mode": "unknown"}, "missing": []}
    out = C.merge_sources(existing, docs=docs, api=api, now="t2")
    assert out["dims"]["mode"] == "list" and out["dims"]["list"] == [[3840, 2160]]
    assert out["sources"]["api"] == "t2" and out["sources"]["docs"] == "t2"


SEEDANCE_INPUTS = {
    "inputs": {
        "referenceImages": {"required": False, "min_items": 1, "max_items": 9},
        "frameImages": {"required": False, "min_items": 1, "max_items": 2},
        "frameImages.image": {"required": True},
        "referenceVideos": {"required": False, "min_items": 1, "max_items": 3},
    }
}
FIRST_ONLY = {"inputs": {"frameImages": {"required": True, "min_items": 1, "max_items": 1}}}
FLUX_INPUTS = {"inputs": {"seedImage": {"required": False}, "maskImage": {"required": False}}}
GPT_INPUTS = {"inputs": {"referenceImages": {"required": False, "min_items": 1, "max_items": 16}}}
EDIT_ONLY = {"inputs": {"referenceImages": {"required": True, "min_items": 1}}}


def test_needs_first_frame_honours_the_harvested_inputs_over_the_tags():
    assert C.needs_first_frame(["io:text-to-video", "io:image-to-video"], FIRST_ONLY) is True
    # tags alone would demand a frame, but the docs name no frameImages input at all
    refs_only = {"inputs": {"referenceImages": {"required": False, "min_items": 1}}}
    assert C.needs_first_frame(["io:image-to-video"], refs_only) is False
    assert "first" not in C.accepted_roles("video", ["io:image-to-video"], refs_only)


def test_accepted_roles_video_from_harvested_inputs():
    roles = C.accepted_roles("video", ["io:text-to-video", "io:image-to-video"], SEEDANCE_INPUTS)
    assert list(roles) == ["first", "last", "reference"]
    assert roles["first"] == {"required": False, "max": 1}
    assert roles["reference"] == {"required": False, "max": 9}
    # one frame at most: no last-frame slot, and the frame is required
    roles = C.accepted_roles("video", ["io:image-to-video"], FIRST_ONLY)
    assert roles == {"first": {"required": True, "max": 1}}
    # an inputs block that names no image input at all: text only
    assert C.accepted_roles("video", ["io:text-to-video"], {"inputs": {"audio": {}}}) == {}


def test_accepted_roles_video_falls_back_to_capability_tags():
    assert C.accepted_roles("video", ["io:text-to-video"], None) == {}
    roles = C.accepted_roles("video", ["io:image-to-video"], None)
    assert list(roles) == ["first", "last", "reference"] and roles["first"]["required"] is True
    assert roles["reference"] == {"required": False, "max": None}
    # no tags at all (search-added row): every role of the mode stays open
    assert list(C.accepted_roles("video", [], None)) == ["first", "last", "reference"]


def test_accepted_roles_image_from_inputs_and_family():
    assert list(C.accepted_roles("image", ["io:image-to-image"], FLUX_INPUTS)) == ["seed"]
    roles = C.accepted_roles("image", ["io:image-to-image"], GPT_INPUTS, "instruction")
    assert roles == {"reference": {"required": False, "max": 16}}
    assert C.accepted_roles("image", ["io:image-to-image"], EDIT_ONLY)["reference"]["required"]
    assert C.accepted_roles("image", ["io:text-to-image"], None) == {}
    assert list(C.accepted_roles("image", ["io:image-to-image"], None, "diffusion")) == [
        "seed",
        "reference",
    ]
    assert list(C.accepted_roles("image", ["io:image-to-image"], None, "instruction")) == [
        "reference"
    ]
    assert list(C.accepted_roles("image", [], None)) == ["seed", "reference"]


def test_accepts_summary_wording():
    assert C.accepts_summary({}) == "text only"
    roles = C.accepted_roles("video", ["io:text-to-video", "io:image-to-video"], SEEDANCE_INPUTS)
    assert C.accepts_summary(roles) == "first frame, last frame, up to 9 reference images"
    assert C.accepts_summary(C.accepted_roles("video", ["io:image-to-video"], FIRST_ONLY)) == (
        "first frame (required)"
    )
    assert C.accepts_summary(C.accepted_roles("image", [], EDIT_ONLY)) == (
        "reference images (required)"
    )


# ---- inputs the app cannot supply -----------------------------------------------------
FABRIC = {"inputs": {"image": {"required": True}, "audio": {"required": True}}}
ANIMATE = {"inputs": {"referenceImages": {"required": True}, "referenceVideos": {"required": True}}}


def test_an_input_the_app_cannot_supply_makes_a_model_unusable():
    assert C.unsuppliable_input(FABRIC) == "audio"
    assert C.unsuppliable_input(ANIMATE) == "referenceVideos"
    assert C.unsuppliable_input(ALEPH) == "video"
    assert C.unsuppliable_input(KLING) is None and C.unsuppliable_input(None) is None
    assert (
        C.is_generate_capable("video", ["io:image-to-video", "io:audio-to-video"], FABRIC) is False
    )
    assert C.is_generate_capable("video", ["io:text-to-video"], ANIMATE) is False
    assert C.is_generate_capable("video", ["io:text-to-video"], KLING) is True
    assert C.UNSUPPLIABLE_LABELS["audio"] == "an audio track"
    assert C.UNSUPPLIABLE_LABELS["referenceVideos"] == "a reference video"
    assert C.UNSUPPLIABLE_LABELS["video"] == "an input video"


def test_docs_rules_seed_how_frames_and_pixels_go_together():
    """The docs page says "When inputs.frameImages is provided, width/height cannot be
    used": with a ``resolution`` parameter on the page the preset replaces the pair,
    without one nothing does. The preset's own values are kept too."""
    docs = {
        "params": {"resolution": {"type": "string", "values": ["480p", "768p"]}},
        "inputs": {},
        "dims": [],
        "rules": {"frames_forbid_size": True},
    }
    out = C.merge_sources(None, docs=docs, api=None, now="t")
    assert out["size_with_inputs"] == "resolution"
    assert out["resolution"] == {"type": "string", "values": ["480p", "768p"]}
    docs2 = {"params": {}, "inputs": {}, "dims": [], "rules": {"frames_forbid_size": True}}
    assert C.merge_sources(None, docs=docs2, api=None, now="t")["size_with_inputs"] == "none"
    docs3 = {"params": {}, "inputs": {}, "dims": []}
    assert "size_with_inputs" not in C.merge_sources(None, docs=docs3, api=None, now="t")
    # a job's own observation outranks a later docs pass
    seen = {
        "size_with_inputs": "resolution",
        "sources": {"docs": None, "api": None, "observed": "t0"},
    }
    assert C.merge_sources(seen, docs=docs2, api=None, now="t")["size_with_inputs"] == "resolution"


def test_observe_merges_a_patch_and_stamps_the_source():
    out = C.observe(KLING, {"duration": {"values": [6, 10]}}, "t1")
    assert out["duration"] == {"min": 3, "max": 15, "step": 1, "default": 5, "values": [6, 10]}
    assert out["dims"] == KLING["dims"] and out["sources"]["observed"] == "t1"
    out2 = C.observe(None, {"inputs": {"frameImages": {"required": True}}}, "t2")
    assert out2["inputs"] == {"frameImages": {"required": True}}
    nested = C.observe(
        {"inputs": {"frameImages": {"required": False, "max_items": 2}}},
        {"inputs": {"frameImages": {"max_items": 1}}},
        "t3",
    )
    assert nested["inputs"]["frameImages"] == {"required": False, "max_items": 1}


def test_a_rejection_can_teach_the_row_which_inputs_are_required():
    missing = "Missing required parameter: 'inputs.frameImages'."
    assert C.rejection_patch(missing, None) == {"inputs": {"frameImages": {"required": True}}}
    # Wan 2.6 Flash: "inputs must be an object" with none sent, and the docs offer frames
    shape = "Invalid type for 'inputs'. 'inputs' must be an object."
    wan = {"inputs": {"frameImages": {"required": False}}}
    assert C.rejection_patch(shape, wan) == {"inputs": {"frameImages": {"required": True}}}
    assert C.rejection_patch(shape, None) is None  # nothing offerable to require
    kling = (
        "Unsupported parameter width/height. Use Kling 2.6 Pro for T2V or I2V requests. Kling "
        "2.6 Standard only works in Motion Control mode (with inputs.referenceImages and "
        "inputs.referenceVideos)."
    )
    assert C.rejection_patch(kling, None) == {
        "inputs": {"referenceImages": {"required": True}, "referenceVideos": {"required": True}}
    }
    assert C.rejection_patch("Unsupported use of 'steps' parameter.", None) is None


def test_observations_from_a_finished_job():
    """What each runner record teaches the catalog row."""
    assert C.observation_patch(
        {"field": "width/height", "action": "converted_to_resolution", "to": "720p"}
    ) == {"size_with_inputs": "resolution"}
    assert C.observation_patch({"field": "width/height", "action": "dropped"}) == {
        "size_with_inputs": "none"
    }
    assert C.observation_patch(
        {"field": "duration", "action": "corrected", "from": 5, "to": 6, "values": [6, 10]}
    ) == {"duration": {"values": [6, 10]}}
    assert C.observation_patch({"field": "inputs.frameImages", "action": "trimmed", "max": 1}) == {
        "inputs": {"frameImages": {"max_items": 1}}
    }
    assert C.observation_patch({"field": "steps", "action": "dropped"}) is None
    assert C.observation_patch({"field": "width/height", "action": "corrected", "dims": {}}) is None


def test_job_observations_read_the_whole_retry_chain():
    """Kling 2.6 Standard, live: width/height became a preset, then ``duration`` and the
    preset itself were dropped as unsupported, and a bare prompt was accepted. That
    teaches "no preset either", not "send a preset" -- and only for a task that carried
    an input image at all, since the key describes what an image changes."""
    swapped = {"field": "width/height", "action": "converted_to_resolution", "to": "720p"}
    with_frames = {"inputs": {"frameImages": [{"image": "u", "frame": "first"}]}}
    assert C.job_observations([swapped], with_frames) == {"size_with_inputs": "resolution"}
    chain = [
        swapped,
        {"field": "duration", "action": "dropped"},
        {"field": "resolution", "action": "dropped"},
    ]
    assert C.job_observations(chain, with_frames) == {"size_with_inputs": "none"}
    assert C.job_observations(chain, {"positivePrompt": "p"}) == {}  # no image: says nothing
    both = [
        swapped,
        {"field": "duration", "action": "corrected", "from": 5, "to": 6, "values": [6, 10]},
    ]
    assert C.job_observations(both, with_frames) == {
        "size_with_inputs": "resolution",
        "duration": {"values": [6, 10]},
    }
    assert (
        C.job_observations([{"field": "width/height", "action": "corrected", "dims": {}}], {}) == {}
    )
