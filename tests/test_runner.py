import asyncio

import pytest
from runware import RunwareError

from tests.fakes.fake_runware import FakeRunware
from vjhstudio.runware import runner

TASK = {
    "taskType": "imageInference",
    "taskUUID": "u1",
    "model": "m",
    "positivePrompt": "p",
    "inputs": {"seedImage": "img"},
    "strength": 0.8,
    "steps": 28,
}


def test_rejected_field_from_parameter_and_message():
    e = RunwareError("unsupportedParameter", "Unsupported use of 'steps' parameter.")
    assert runner.rejected_field(e, TASK) == "steps"
    e2 = RunwareError("unsupportedParameter", "Unsupported parameter for this model.")
    e2.parameter = "inputs.seedImage"
    assert runner.rejected_field(e2, TASK) == "inputs.seedImage"
    e3 = RunwareError("unsupportedParameter", "Unsupported use of 'model' parameter.")
    assert runner.rejected_field(e3, TASK) is None


def test_rejected_field_ignores_value_validation_errors():
    """An out-of-range *value* names a parameter too, but dropping it would delete a
    required field and hide RunWare's own message behind a retry cascade."""
    e = RunwareError(
        "invalidValue",
        "Invalid value for 'height' parameter. Video height must be between 128 and 2160 "
        "and in multiples of 64.",
    )
    e.parameter = "height"
    assert runner.rejected_field(e, {**TASK, "height": 720, "width": 1280}) is None


def test_apply_fallback_rules():
    t, rec = runner.apply_fallback(TASK, "inputs.seedImage")
    assert (
        t["inputs"]["referenceImages"] == ["img"]
        and "seedImage" not in t["inputs"]
        and "strength" not in t
    )
    assert rec["action"] == "converted_to_referenceImages"
    t2, rec2 = runner.apply_fallback(TASK, "strength")
    assert (
        "strength" not in t2
        and t2["inputs"]["seedImage"] == "img"
        and rec2["action"] == "dropped_strength"
    )
    t3, rec3 = runner.apply_fallback(TASK, "steps")
    assert "steps" not in t3 and rec3["action"] == "dropped"


async def _no_sleep(_s):
    return None


async def test_fallback_then_success_records_drops():
    fake = FakeRunware(
        {
            "run": [
                RunwareError("unsupportedParameter", "Unsupported use of 'steps' parameter."),
                [{"imageURL": "http://x/1.png", "seed": 3, "cost": 0.01}],
            ]
        }
    )
    attempts = []
    res = await runner.run_with_policy(
        fake,
        TASK,
        timeout_s=5,
        cancel_event=None,
        on_progress=None,
        on_attempt=lambda t, d: attempts.append((dict(t), list(d))),
        sleep=_no_sleep,
    )
    assert res.items[0].url == "http://x/1.png" and res.items[0].seed == 3 and res.attempts == 2
    assert res.dropped == [{"field": "steps", "action": "dropped"}]
    assert "steps" not in fake.calls[-1][1] and fake.calls[-1][1]["taskUUID"] != "u1"


async def test_value_validation_error_fails_the_job_on_the_first_attempt():
    """No field is dropped and the user sees RunWare's own message."""
    err = RunwareError(
        "invalidValue",
        "Invalid value for 'height' parameter. Video height must be in multiples of 64.",
    )
    err.parameter = "height"
    fake = FakeRunware({"run": [err, [{"videoURL": "http://x/v.mp4"}]]})
    task = {
        "taskType": "videoInference",
        "taskUUID": "u1",
        "model": "m",
        "positivePrompt": "p",
        "width": 1280,
        "height": 720,
    }
    with pytest.raises(RunwareError) as excinfo:
        await runner.run_with_policy(
            fake, task, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep
        )
    assert "multiples of 64" in excinfo.value.message
    assert len(fake.calls) == 1
    assert fake.calls[0][1]["height"] == 720 and fake.calls[0][1]["width"] == 1280


async def test_rate_limit_backoff_then_success():
    fake = FakeRunware({"run": [RunwareError("rateLimitExceeded", "slow"), [{"imageURL": "u"}]]})
    slept = []

    async def sleep(s):
        slept.append(s)

    res = await runner.run_with_policy(
        fake, TASK, timeout_s=5, cancel_event=None, on_progress=None, sleep=sleep
    )
    assert slept == [2] and res.attempts == 2


async def test_non_retryable_raises_immediately():
    fake = FakeRunware({"run": [RunwareError("invalidApiKey", "bad"), [{"imageURL": "u"}]]})
    with pytest.raises(RunwareError):
        await runner.run_with_policy(
            fake, TASK, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep
        )
    assert len(fake.calls) == 1


async def test_exhausts_after_five_attempts():
    errs = [
        RunwareError("unsupportedParameter", f"Unsupported use of 'k{i}' parameter.")
        for i in range(5)
    ]
    task = dict(TASK, k0=1, k1=1, k2=1, k3=1, k4=1)
    fake = FakeRunware({"run": errs + [[{"imageURL": "u"}]]})
    with pytest.raises(RunwareError):
        await runner.run_with_policy(
            fake, task, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep
        )
    assert len(fake.calls) == 5


async def test_progress_callback_and_cancel():
    fake = FakeRunware({"run": [("progress", [10, 55], [{"imageURL": "u"}])]})
    seen = []
    res = await runner.run_with_policy(
        fake, TASK, timeout_s=5, cancel_event=None, on_progress=seen.append, sleep=_no_sleep
    )
    assert seen == [10, 55] and res.items
    ev = asyncio.Event()
    ev.set()
    fake2 = FakeRunware({"run": [[{"imageURL": "u"}]]})
    with pytest.raises(RunwareError) as ei:
        await runner.run_with_policy(
            fake2, TASK, timeout_s=5, cancel_event=ev, on_progress=None, sleep=_no_sleep
        )
    assert ei.value.code == "aborted"


async def test_duration_ms_excludes_retry_backoff():
    """The rolling latency average must see the successful attempt, not the waiting."""
    slept: list[float] = []

    async def slow_sleep(seconds):
        slept.append(seconds)
        await asyncio.sleep(0.25)  # a real pause, so a whole-call timer would notice

    fake = FakeRunware(
        {
            "run": [
                RunwareError("rateLimitExceeded", "slow down"),
                [{"imageURL": "http://x/1.png", "cost": 0.01}],
            ]
        }
    )
    res = await runner.run_with_policy(
        fake, dict(TASK), timeout_s=5, cancel_event=None, on_progress=None, sleep=slow_sleep
    )
    assert res.attempts == 2 and slept == [2]
    assert res.duration_ms is not None and res.duration_ms < 100


KLING_MSG = (
    "Unsupported use of width/height parameters. The specified dimensions are not supported "
    "for the kling video 3.0 4k model. Supported values are: '3840x2160', '2160x3840', "
    "'2880x2880'."
)
LTX_MSG = (
    "Invalid value for 'width' parameter. Video width must be an integer value between 128 "
    "and 2048, in multiples of 64."
)


def test_width_height_are_never_dropped():
    e = RunwareError(
        "unsupportedParameter",
        "Unsupported use of width/height parameters. Supported values are: '3840x2160'.",
    )
    e.parameter = "width"
    assert runner.rejected_field(e, {"width": 1280, "height": 720}) is None


def test_size_correction_picks_nearest_listed_size():
    e = RunwareError("unsupportedParameter", KLING_MSG)
    e.parameter = "width"
    new, rec = runner.size_correction(e, {"width": 1280, "height": 720, "taskUUID": "t"})
    assert (new["width"], new["height"]) == (3840, 2160)
    assert rec == {
        "field": "width/height",
        "action": "corrected",
        "from": [1280, 720],
        "to": [3840, 2160],
        "dims": {"mode": "list", "list": [[3840, 2160], [2160, 3840], [2880, 2880]]},
    }


def test_size_correction_snaps_to_rule():
    e = RunwareError("invalidValue", LTX_MSG)
    e.parameter = "width"
    new, rec = runner.size_correction(e, {"width": 1280, "height": 720})
    assert (new["width"], new["height"]) == (1280, 704) and rec["dims"]["mode"] == "rule"


def test_size_correction_returns_none_without_a_rule_or_list():
    e = RunwareError(
        "unsupportedParameter", "Unsupported width/height combination for this model architecture."
    )
    e.parameter = "width"
    assert runner.size_correction(e, {"width": 1, "height": 1}) is None


async def test_run_with_policy_corrects_size_once_then_succeeds():
    e = RunwareError("unsupportedParameter", KLING_MSG)
    e.parameter = "width"
    fake = FakeRunware({"run": [e, [{"taskType": "videoInference", "videoURL": "http://x/v.mp4"}]]})
    res = await runner.run_with_policy(
        fake,
        {
            "taskType": "videoInference",
            "taskUUID": "a",
            "model": "k",
            "positivePrompt": "p",
            "width": 1280,
            "height": 720,
        },
        timeout_s=5,
        cancel_event=None,
        on_progress=None,
        sleep=_no_sleep,
    )
    sent = [p for n, p in fake.calls if n == "run"]
    assert (sent[1]["width"], sent[1]["height"]) == (3840, 2160) and sent[1]["taskUUID"] != "a"
    assert res.dropped[0]["action"] == "corrected"


async def test_a_second_size_rejection_fails_the_job():
    e = RunwareError("unsupportedParameter", KLING_MSG)
    e.parameter = "width"
    e2 = RunwareError("unsupportedParameter", KLING_MSG)
    e2.parameter = "width"
    fake = FakeRunware({"run": [e, e2]})
    with pytest.raises(RunwareError):
        await runner.run_with_policy(
            fake,
            {
                "taskType": "videoInference",
                "taskUUID": "a",
                "model": "k",
                "positivePrompt": "p",
                "width": 1,
                "height": 1,
            },
            timeout_s=5,
            cancel_event=None,
            on_progress=None,
            sleep=_no_sleep,
        )
    assert len([1 for n, _ in fake.calls if n == "run"]) == 2


def test_size_correction_ignores_a_nested_field_that_merely_contains_width():
    e = RunwareError(
        "unsupportedParameter",
        "Unsupported use of 'frameImages.0.width'. Supported values are: '64x64'.",
    )
    e.parameter = "inputs.frameImages.0.width"
    assert runner.size_correction(e, {"width": 1280, "height": 720}) is None


def test_a_nested_width_is_still_dropped():
    """Only the task's own width/height are corrected instead of dropped; a nested one
    (a frame image's size, say) is an ordinary unsupported field."""
    e = RunwareError(
        "unsupportedParameter", "Unsupported use of 'inputs.frameImages.width' parameter."
    )
    e.parameter = "inputs.frameImages.width"
    task = {"width": 1280, "height": 720, "inputs": {"frameImages": {"width": 512}}}
    assert runner.rejected_field(e, task) == "inputs.frameImages.width"


NOT_RECOGNIZED = (
    "Invalid parameter detected. The parameter \"'negativePrompt'\" is not recognized "
    "or supported by this model."
)


def test_not_recognized_wording_is_an_unsupported_parameter():
    """RunWare's second wording for an unknown key (seen live from bfl:2@2). The job
    must drop the field and retry for free, not surface a failure."""
    task = {"model": "bfl:2@2", "positivePrompt": "x", "negativePrompt": "blurry"}
    e = RunwareError("unsupportedParameter", NOT_RECOGNIZED)
    e.parameter = "negativePrompt"
    assert runner.rejected_field(e, task) == "negativePrompt"
    e2 = RunwareError("unsupportedParameter", NOT_RECOGNIZED)  # no .parameter: parse it
    assert runner.rejected_field(e2, task) == "negativePrompt"
    # an invalid *value* still surfaces as-is
    e3 = RunwareError("invalidValue", "Invalid value for 'height' parameter: must be even.")
    e3.parameter = "height"
    assert runner.rejected_field(e3, {"height": 3, "positivePrompt": "x"}) is None


# ---- frame images switch a model from pixels to a resolution preset ---------------
KLING3_I2V_MSG = (
    "Unsupported use of 'width' parameter. This parameter is not supported for the selected "
    "model. Allowed values are: 'includeCost', 'taskUUID', 'taskType', 'model', 'outputType', "
    "'outputFormat', 'numberResults', 'positivePrompt', 'negativePrompt', 'deliveryMethod', "
    "'duration', 'providerSettings', 'fps', 'uploadEndpoint', 'outputQuality', 'webhookURL', "
    "'ttl', 'inputs', 'CFGScale', 'resolution'."
)
H3_FAST_I2V_MSG = (
    "Unsupported use of 'width' parameter. This parameter is not supported for the selected "
    "model. Allowed values are: 'includeCost', 'taskUUID', 'taskType', 'model', 'outputType', "
    "'outputFormat', 'numberResults', 'positivePrompt', 'deliveryMethod', 'duration', "
    "'frameImages', 'uploadEndpoint', 'outputQuality', 'webhookURL', 'ttl', 'inputs', 'seed', "
    "'safety'."
)
SKYREELS_MSG = "Parameter 'width' and Parameter 'inputs.frameImages' cannot be used together"
I2V_TASK = {
    "taskType": "videoInference",
    "taskUUID": "a",
    "model": "k",
    "positivePrompt": "p",
    "duration": 5,
    "width": 1280,
    "height": 720,
    "inputs": {"frameImages": [{"image": "u", "frame": "first"}]},
}


def _err(message: str, parameter: str | None = None) -> RunwareError:
    e = RunwareError("unsupportedParameter", message)
    if parameter:
        e.parameter = parameter
    return e


def test_unsupported_width_becomes_a_resolution_preset_when_the_model_lists_one():
    """Seen live from Kling 3 Standard with a first frame: width/height are refused and
    the allowed list names ``resolution`` instead. The pair is swapped for the preset the
    request was built from, not dropped (the frame fixes the aspect; the preset the tier)."""
    new, rec = runner.size_to_resolution(_err(KLING3_I2V_MSG, "width"), I2V_TASK, "720p")
    assert "width" not in new and "height" not in new and new["resolution"] == "720p"
    assert rec == {"field": "width/height", "action": "converted_to_resolution", "to": "720p"}


def test_unsupported_width_is_dropped_when_the_model_has_no_resolution_parameter():
    """MiniMax H3 Fast: no ``resolution`` in the allowed list, so the frame alone sizes
    the clip and the pair simply goes."""
    new, rec = runner.size_to_resolution(_err(H3_FAST_I2V_MSG, "width"), I2V_TASK, "480p")
    assert "width" not in new and "height" not in new and "resolution" not in new
    assert rec == {"field": "width/height", "action": "dropped"}


def test_a_width_frame_conflict_is_the_same_swap():
    """SkyReels words the same rule as a conflict, without an allowed list: with a hint
    the preset is sent, and RunWare's next reply settles whether it takes one."""
    new, rec = runner.size_to_resolution(_err(SKYREELS_MSG), I2V_TASK, "480p")
    assert "width" not in new and new["resolution"] == "480p"
    assert rec["action"] == "converted_to_resolution"
    # no hint at all: nothing to swap in, the pair is dropped
    new2, rec2 = runner.size_to_resolution(_err(SKYREELS_MSG), I2V_TASK, None)
    assert "width" not in new2 and "resolution" not in new2 and rec2["action"] == "dropped"


def test_size_to_resolution_leaves_other_rejections_alone():
    assert runner.size_to_resolution(_err(KLING_MSG, "width"), I2V_TASK, "720p") is None
    assert (
        runner.size_to_resolution(_err("Unsupported use of 'steps' parameter."), I2V_TASK, "720p")
        is None
    )
    value = RunwareError("invalidValue", "Invalid value for 'width' parameter. Must be even.")
    value.parameter = "width"
    assert runner.size_to_resolution(value, I2V_TASK, "720p") is None
    # a task with no width/height has nothing to swap
    bare = {k: v for k, v in I2V_TASK.items() if k not in ("width", "height")}
    assert runner.size_to_resolution(_err(KLING3_I2V_MSG, "width"), bare, "720p") is None


async def test_run_with_policy_swaps_pixels_for_the_preset_and_records_it():
    fake = FakeRunware(
        {"run": [_err(KLING3_I2V_MSG, "width"), [{"videoURL": "http://x/v.mp4", "cost": 0.4}]]}
    )
    res = await runner.run_with_policy(
        fake,
        I2V_TASK,
        timeout_s=5,
        cancel_event=None,
        on_progress=None,
        sleep=_no_sleep,
        resolution="720p",
    )
    sent = [p for n, p in fake.calls if n == "run"]
    assert sent[1]["resolution"] == "720p" and "width" not in sent[1] and "height" not in sent[1]
    assert sent[1]["inputs"] == I2V_TASK["inputs"] and sent[1]["taskUUID"] != "a"
    assert res.dropped == [
        {"field": "width/height", "action": "converted_to_resolution", "to": "720p"}
    ]


# ---- two inputs the model will not take together ------------------------------------
GROK_CONFLICT = (
    "Parameter 'inputs.frameImages' and Parameter 'inputs.referenceImages' cannot be used together"
)


def test_conflict_drops_the_second_named_input():
    task = {
        **I2V_TASK,
        "inputs": {"frameImages": [{"image": "u", "frame": "first"}], "referenceImages": ["u"]},
    }
    assert runner.conflict_field(_err(GROK_CONFLICT), task) == "inputs.referenceImages"
    # the second one is not in the task (already gone): the first is what conflicts
    only_frames = {**I2V_TASK}
    assert runner.conflict_field(_err(GROK_CONFLICT), only_frames) == "inputs.frameImages"
    assert runner.conflict_field(_err("Unsupported use of 'steps' parameter."), task) is None
    # a width conflict belongs to size_to_resolution, never to the plain drop
    assert runner.conflict_field(_err(SKYREELS_MSG), task) is None


async def test_run_with_policy_drops_the_conflicting_reference_images():
    task = {
        **I2V_TASK,
        "inputs": {"frameImages": [{"image": "u", "frame": "first"}], "referenceImages": ["u"]},
    }
    fake = FakeRunware({"run": [_err(GROK_CONFLICT), [{"videoURL": "http://x/v.mp4"}]]})
    res = await runner.run_with_policy(
        fake, task, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep
    )
    sent = [p for n, p in fake.calls if n == "run"]
    assert "referenceImages" not in sent[1]["inputs"] and sent[1]["inputs"]["frameImages"]
    assert res.dropped == [
        {"field": "inputs.referenceImages", "action": "dropped_reference_images"}
    ]


# ---- a value the model enumerates -------------------------------------------------------
MINIMAX_DURATION = (
    "Invalid value for 'duration' parameter. The Duration requested for MiniMax Hailuo 02 "
    "must be a float. Supported values are: '6', '10'"
)


def test_value_correction_picks_the_nearest_listed_value():
    e = RunwareError("invalidValue", MINIMAX_DURATION)
    e.parameter = "duration"
    new, rec = runner.value_correction(e, {**I2V_TASK, "duration": 5})
    assert new["duration"] == 6
    assert rec == {
        "field": "duration",
        "action": "corrected",
        "from": 5,
        "to": 6,
        "values": [6, 10],
    }
    new, _ = runner.value_correction(e, {**I2V_TASK, "duration": 9})
    assert new["duration"] == 10
    # a tie goes to the shorter (cheaper) clip
    new, _ = runner.value_correction(e, {**I2V_TASK, "duration": 8})
    assert new["duration"] == 6


def test_value_correction_handles_named_presets_by_their_number():
    e = RunwareError(
        "invalidValue",
        "Invalid value for 'resolution' parameter. Supported values are: '480p', '768p'",
    )
    e.parameter = "resolution"
    task = {**I2V_TASK, "resolution": "720p"}
    task.pop("width"), task.pop("height")
    new, rec = runner.value_correction(e, task)
    assert new["resolution"] == "768p" and rec["values"] == ["480p", "768p"]


def test_value_correction_is_only_for_listed_scalar_values():
    e = RunwareError("invalidValue", "Invalid value for 'duration' parameter. Must be > 0.")
    e.parameter = "duration"
    assert runner.value_correction(e, I2V_TASK) is None  # no list to choose from
    e2 = RunwareError("invalidValue", MINIMAX_DURATION)
    e2.parameter = "duration"
    assert runner.value_correction(e2, {**I2V_TASK, "duration": 6}) is None  # already listed
    assert (
        runner.value_correction(e2, {k: v for k, v in I2V_TASK.items() if k != "duration"}) is None
    )
    sized = RunwareError(
        "invalidValue", "Invalid value for 'width' parameter. Supported values are: '1280x720'"
    )
    sized.parameter = "width"
    assert runner.value_correction(sized, I2V_TASK) is None  # the pair is size_correction's


async def test_run_with_policy_corrects_a_listed_duration_then_succeeds():
    e = RunwareError("invalidValue", MINIMAX_DURATION)
    e.parameter = "duration"
    fake = FakeRunware({"run": [e, [{"videoURL": "http://x/v.mp4"}]]})
    res = await runner.run_with_policy(
        fake, I2V_TASK, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep
    )
    sent = [p for n, p in fake.calls if n == "run"]
    assert sent[1]["duration"] == 6 and res.dropped[0]["action"] == "corrected"


async def test_the_same_value_is_corrected_once_only():
    e = RunwareError("invalidValue", MINIMAX_DURATION)
    e.parameter = "duration"
    fake = FakeRunware({"run": [e, e]})
    with pytest.raises(RunwareError):
        await runner.run_with_policy(
            fake, I2V_TASK, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep
        )
    assert len([1 for n, _ in fake.calls if n == "run"]) == 2


# ---- more frame images than the model takes ------------------------------------------
GROK_FRAMES = (
    "Invalid number of elements for 'inputs.frameImages' parameter. Frame images must "
    "contain between 0 and 1 images."
)


def test_trim_correction_keeps_the_first_frames():
    task = {
        **I2V_TASK,
        "inputs": {
            "frameImages": [{"image": "a", "frame": "first"}, {"image": "b", "frame": "last"}]
        },
    }
    e = RunwareError("invalidValue", GROK_FRAMES)
    e.parameter = "inputs.frameImages"
    new, rec = runner.trim_correction(e, task)
    assert new["inputs"]["frameImages"] == [{"image": "a", "frame": "first"}]
    assert rec == {"field": "inputs.frameImages", "action": "trimmed", "max": 1}
    assert runner.trim_correction(e, I2V_TASK) is None  # already within the cap
    assert runner.trim_correction(_err("Unsupported use of 'steps' parameter."), task) is None


async def test_run_with_policy_trims_the_last_frame():
    task = {
        **I2V_TASK,
        "inputs": {
            "frameImages": [{"image": "a", "frame": "first"}, {"image": "b", "frame": "last"}]
        },
    }
    e = RunwareError("invalidValue", GROK_FRAMES)
    e.parameter = "inputs.frameImages"
    fake = FakeRunware({"run": [e, [{"videoURL": "http://x/v.mp4"}]]})
    res = await runner.run_with_policy(
        fake, task, timeout_s=5, cancel_event=None, on_progress=None, sleep=_no_sleep
    )
    sent = [p for n, p in fake.calls if n == "run"]
    assert len(sent[1]["inputs"]["frameImages"]) == 1 and res.dropped[0]["action"] == "trimmed"


def test_value_correction_reads_the_other_wording_of_the_list():
    """MiniMax H3 Max, live: "The resolution must be a string value with one of the
    following supported values: '480p', '768p'." -- no "Supported values are:"."""
    e = RunwareError(
        "invalidValue",
        "Invalid value for 'resolution' parameter. The resolution must be a string value "
        "with one of the following supported values: '480p', '768p'.",
    )
    e.parameter = "resolution"
    task = {k: v for k, v in I2V_TASK.items() if k not in ("width", "height")}
    new, rec = runner.value_correction(e, {**task, "resolution": "1080p"})
    assert new["resolution"] == "768p" and rec["values"] == ["480p", "768p"]
