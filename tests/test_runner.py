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
