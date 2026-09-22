import pytest
from runware import RunwareError

from tests.fakes.fake_runware import FakeRunware
from vjhstudio.runware import probe as P

ALLOWED = (
    "Unsupported use of 'vjhProbe' parameter. This parameter is not supported for the selected model. "
    "Allowed values are: 'includeCost', 'taskUUID', 'taskType', 'model', 'height', 'width', 'outputType', "
    "'positivePrompt', 'duration', 'providerSettings', 'fps', 'inputs.frameImages'."
)
KLING = (
    "Unsupported use of width/height parameters. The specified dimensions are not supported for the kling "
    "video 3.0 4k model. Supported values are: '3840x2160', '2160x3840', '2880x2880'."
)
WAN = "Unsupported use of width/height parameters. … Supported values are: '1280*720', '720*1280', '960*960'."
LTX = "Invalid value for 'width' parameter. Video width must be an integer value between 128 and 2048, in multiples of 64."
VEO = "Unsupported width/height combination for this model architecture."
ALEPH_ALLOWED = (
    "Unsupported use of 'vjhProbe' parameter. This parameter is not supported for the selected model. "
    "Allowed values are: 'includeCost', 'taskUUID', 'taskType', 'model', 'positivePrompt', 'inputs.video'."
)


def test_parse_allowed_params():
    assert P.parse_allowed_params(ALLOWED)[:6] == [
        "includeCost",
        "taskUUID",
        "taskType",
        "model",
        "height",
        "width",
    ]
    assert P.parse_allowed_params("no list here") == []


def test_parse_supported_dims_shapes():
    assert P.parse_supported_dims(KLING) == {
        "mode": "list",
        "list": [[3840, 2160], [2160, 3840], [2880, 2880]],
    }
    assert P.parse_supported_dims(WAN) == {
        "mode": "list",
        "list": [[1280, 720], [720, 1280], [960, 960]],
    }
    assert P.parse_supported_dims(LTX) == {"mode": "rule", "min": 128, "max": 2048, "step": 64}
    assert P.parse_supported_dims(VEO) == {"mode": "unknown"}
    assert P.parse_supported_dims("Missing required parameter: 'width/height'.") == {
        "mode": "unknown"
    }


def test_parse_missing_required():
    assert P.parse_missing_required("Missing required parameter: 'inputs.video'.") == "inputs.video"
    assert P.parse_missing_required(KLING) is None


def _err(msg, param=None, code="unsupportedParameter"):
    # NB: the real runware-sdk (1.6.10) derives `.code` from this raw string via
    # `derive_code()`; it does not pass "validation" straight through. Raw codes that
    # start with "unsupported"/"invalid"/"missing" all derive to "validation" (matching
    # the convention already used in tests/test_runner.py), which is what probe.py
    # branches on.
    e = RunwareError(code, msg)
    e.parameter = param
    return e


@pytest.mark.asyncio
async def test_probe_model_sends_two_guaranteed_rejected_requests():
    fake = FakeRunware({"run": [_err(ALLOWED, "vjhProbe"), _err(KLING, "width")]})
    res = await P.probe_model(fake, "klingai:kling-video@3-4k", "video")
    first, second = [p for name, p in fake.calls if name == "run"]
    assert (
        first[P.PROBE_KEY] == 1
        and first["taskType"] == "videoInference"
        and first["model"] == "klingai:kling-video@3-4k"
    )
    assert len(first["positivePrompt"]) >= 3 and "inputs" not in first
    assert second == {
        **{k: second[k] for k in ("taskType", "taskUUID", "model")},
        "positivePrompt": P.PROBE_PROMPT,
        "width": 1,
        "height": 1,
    }
    assert second["taskUUID"] != first["taskUUID"]
    assert res.params[:4] == ["includeCost", "taskUUID", "taskType", "model"]
    assert res.dims == {"mode": "list", "list": [[3840, 2160], [2160, 3840], [2880, 2880]]}
    assert res.missing == [] and res.errors == []


@pytest.mark.asyncio
async def test_probe_skips_size_probe_when_model_has_no_width_height():
    fake = FakeRunware({"run": [_err(ALEPH_ALLOWED, "vjhProbe")]})
    res = await P.probe_model(fake, "runway:aleph@2.0", "video")
    assert len([1 for n, _ in fake.calls if n == "run"]) == 1
    assert res.dims is None and "inputs.video" in res.params


@pytest.mark.asyncio
async def test_probe_records_missing_required_from_size_probe():
    fake = FakeRunware(
        {
            "run": [
                _err(ALLOWED, "vjhProbe"),
                _err(
                    "Missing required parameter: 'inputs.video'.",
                    "inputs.video",
                    code="missingParameter",
                ),
            ]
        }
    )
    res = await P.probe_model(fake, "x:y@1", "video")
    assert res.missing == ["inputs.video"] and res.dims == {"mode": "unknown"}


@pytest.mark.asyncio
async def test_probe_treats_an_accepted_request_as_billing_and_raises():
    fake = FakeRunware({"run": [[{"taskType": "videoInference", "videoURL": "http://x"}]]})
    with pytest.raises(P.ProbeBilledError):
        await P.probe_model(fake, "x:y@1", "video")


@pytest.mark.asyncio
async def test_probe_collects_non_validation_errors_instead_of_raising():
    fake = FakeRunware({"run": [RunwareError("connectionFailed", "offline")]})
    res = await P.probe_model(fake, "x:y@1", "image")
    assert res.params == [] and res.dims is None and res.errors == ["connection: offline"]
