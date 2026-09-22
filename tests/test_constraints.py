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
