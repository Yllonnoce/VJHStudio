import pytest
from pydantic import ValidationError

from vjhstudio.runware import tasks
from vjhstudio.schemas.image import PromptForm
from vjhstudio.schemas.video import VideoRequest

VEO = {
    "air": "google:3@2",
    "kind": "video",
    "capabilities": ["io:text-to-video", "io:image-to-video"],
    "tiers": {"video": {"durations": [4, 6, 8], "resolutions": ["720p", "1080p"]}},
    "provider_settings_schema": [{"key": "generateAudio", "type": "bool", "default": True}],
}
LTX = {
    "air": "lightricks:ltx@2.3",
    "kind": "video",
    "capabilities": ["io:text-to-video"],
    "tiers": {
        "video": {
            "durations": [3, 5, 8, 10],
            "resolutions": ["720p", "1080p"],
            "dims": {"720p": [1280, 704], "1080p": [1920, 1088]},
            "fps": [24, 25, 30],
        }
    },
    "provider_settings_schema": [],
}
BARE = {"air": "acme:vid@1", "kind": "video", "tiers": {}, "provider_settings_schema": []}


def req(**kw):
    base = dict(project_id=1, model="google:3@2", form=PromptForm(subject="a fox"))
    base.update(kw)
    return VideoRequest(**base)


def test_video_form_defaults_to_no_text_false():
    assert req().form.no_text is False
    assert VideoRequest(project_id=1, model="m", form={"subject": "x"}).form.no_text is False
    assert VideoRequest(project_id=1, model="m").form.no_text is False
    explicit = VideoRequest(project_id=1, model="m", form={"subject": "x", "no_text": True})
    assert explicit.form.no_text is True
    # a PromptForm instance: the default still moves, an assigned value never does
    assert VideoRequest(project_id=1, model="m", form=PromptForm(subject="x")).form.no_text is False
    kept = VideoRequest(project_id=1, model="m", form=PromptForm(subject="x", no_text=True))
    assert kept.form.no_text is True


def test_text_to_video_task_shape():
    t = tasks.build_video_task(req(seed=7), "uuid-1", {}, VEO)
    assert t["taskType"] == "videoInference" and t["taskUUID"] == "uuid-1"
    assert t["model"] == "google:3@2" and t["positivePrompt"] == "a fox"
    assert t["outputType"] == "URL" and t["outputFormat"] == "MP4" and t["includeCost"] is True
    assert t["width"] == 1280 and t["height"] == 720 and "resolution" not in t
    assert t["seed"] == 7
    assert "negativePrompt" not in t  # video models take no negative prompt
    assert "inputs" not in t and "providerSettings" not in t and "fps" not in t


def test_duration_snaps_to_the_model_list():
    assert tasks.build_video_task(req(duration=5), "u", {}, VEO)["duration"] == 4
    assert tasks.build_video_task(req(duration=7), "u", {}, VEO)["duration"] == 6
    assert tasks.build_video_task(req(duration=30), "u", {}, VEO)["duration"] == 8
    # no list in the catalog row: the request's own duration is sent as asked
    assert tasks.build_video_task(req(duration=12), "u", {}, BARE)["duration"] == 12
    assert tasks.build_video_task(req(duration=7.5), "u", {}, BARE)["duration"] == 7.5


def test_resolution_mapping_is_case_insensitive_and_defaults():
    def wh(resolution):
        t = tasks.build_video_task(req(resolution=resolution), "u", {}, BARE)
        return t["width"], t["height"]

    assert wh("480p") == (854, 480)
    assert wh("720p") == (1280, 720)
    assert wh("1080p") == (1920, 1080)
    assert wh("4K") == (3840, 2160) and wh("4k") == (3840, 2160)
    assert wh("nonsense") == (1280, 720)
    assert tasks.RESOLUTIONS["1080p"] == (1920, 1080)


def test_curated_dims_override_the_resolution_map():
    """LTX-2.3 rejects any dimension that is not a multiple of 64, so its curated
    ``video.dims`` wins; a model without a dims block keeps the generic preset."""

    def wh(row, resolution):
        t = tasks.build_video_task(req(resolution=resolution, model=row["air"]), "u", {}, row)
        return t["width"], t["height"]

    assert wh(LTX, "720p") == (1280, 704)
    assert wh(LTX, "1080p") == (1920, 1088)
    assert wh(LTX, "720P") == (1280, 704)  # the lookup stays case-insensitive
    assert wh(VEO, "720p") == (1280, 720) and wh(VEO, "1080p") == (1920, 1080)
    for resolution in ("720p", "1080p"):
        w, h = wh(LTX, resolution)
        assert w % 64 == 0 and h % 64 == 0, (resolution, w, h)


def test_resolution_wh_takes_the_dims_block_directly():
    video = {"dims": {"720p": [1280, 704]}}
    assert tasks.resolution_wh("720p", video) == (1280, 704)
    assert tasks.resolution_wh("1080p", video) == (1920, 1080)  # not listed -> generic
    assert tasks.resolution_wh("720p", {"dims": {"720p": [1280]}}) == (1280, 720)  # malformed
    assert tasks.resolution_wh("720p", None) == (1280, 720)


def test_fps_only_when_the_model_lists_it():
    assert "fps" not in tasks.build_video_task(req(fps=24), "u", {}, VEO)
    assert tasks.build_video_task(req(fps=24, model=LTX["air"]), "u", {}, LTX)["fps"] == 24
    assert "fps" not in tasks.build_video_task(req(model=LTX["air"]), "u", {}, LTX)


def test_image_to_video_frame_images():
    t = tasks.build_video_task(
        req(first_frame_asset_id=5, last_frame_asset_id=6),
        "u",
        {5: "uuid-5", 6: "uuid-6"},
        VEO,
    )
    assert t["inputs"]["frameImages"] == [
        {"image": "uuid-5", "frame": "first"},
        {"image": "uuid-6", "frame": "last"},
    ]


def test_unresolved_frame_assets_are_skipped():
    t = tasks.build_video_task(
        req(first_frame_asset_id=5, last_frame_asset_id=6), "u", {6: "uuid-6"}, VEO
    )
    assert t["inputs"]["frameImages"] == [{"image": "uuid-6", "frame": "last"}]
    assert "inputs" not in tasks.build_video_task(req(first_frame_asset_id=5), "u", {}, VEO)


def test_reference_images_pass_through():
    t = tasks.build_video_task(req(reference_asset_ids=[1, 2, 9]), "u", {1: "a", 2: "b"}, VEO)
    assert t["inputs"]["referenceImages"] == ["a", "b"]


def test_provider_settings_are_nested_under_the_provider_key():
    t = tasks.build_video_task(req(provider_settings={"generateAudio": True}), "u", {}, VEO)
    assert t["providerSettings"] == {"google": {"generateAudio": True}}
    assert tasks.provider_key("klingai:kling-video@3-standard") == "klingai"
    assert tasks.provider_key("bare") == "bare"
    assert "providerSettings" not in tasks.build_video_task(req(), "u", {}, VEO)


def test_extra_json_merges_last_but_cannot_override_protected_keys():
    t = tasks.build_video_task(
        req(extra_json={"model": "hack", "taskType": "hack", "numberResults": 2, "width": 640}),
        "u",
        {},
        VEO,
    )
    assert t["model"] == "google:3@2" and t["taskType"] == "videoInference"
    assert t["numberResults"] == 2 and t["width"] == 640


def test_final_prompt_wins_and_no_text_suffix_is_opt_in():
    t = tasks.build_video_task(req(final_prompt="a dancing fox"), "u", {}, VEO)
    assert t["positivePrompt"] == "a dancing fox"
    r = req(form=PromptForm(subject="a fox", no_text=True))
    assert tasks.build_video_task(r, "u", {}, VEO)["positivePrompt"].startswith("a fox Pure")


def test_duration_validator_range():
    for bad in (0, 0.5, 31):
        with pytest.raises(ValidationError):
            req(duration=bad)
    assert req(duration=1).duration == 1 and req(duration=30).duration == 30
