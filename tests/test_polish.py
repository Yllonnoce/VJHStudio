import asyncio
from contextlib import asynccontextmanager

import pytest
from runware import RunwareError

from tests.fakes.fake_runware import FakeRunware, fake_factory
from vjhstudio.runware import tasks
from vjhstudio.services import polish


# ---- runware/tasks.py builders --------------------------------------------
def test_build_prompt_enhance_shape_and_truncation():
    t = tasks.build_prompt_enhance("x" * 400, "uuid-1", versions=2, max_length=200)
    assert t["taskType"] == "promptEnhance" and t["taskUUID"] == "uuid-1"
    assert "model" not in t  # live API rejects a `model` key for promptEnhance
    assert len(t["prompt"]) == 300 and t["prompt"] == "x" * 300
    assert t["promptMaxLength"] == 200 and t["promptVersions"] == 2 and t["includeCost"] is True


def test_build_prompt_enhance_clamps_versions_and_max_length():
    t = tasks.build_prompt_enhance("a fox", "u", versions=0, max_length=1)
    assert t["promptVersions"] == 1 and t["promptMaxLength"] == 12
    t = tasks.build_prompt_enhance("a fox", "u", versions=9, max_length=99999)
    assert t["promptVersions"] == 5 and t["promptMaxLength"] == 400


def test_build_polish_text_messages_shape():
    t = tasks.build_polish_text("google:gemini@3.5-flash", "a fox in a forest", "u", versions=1)
    assert t["taskType"] == "textInference" and t["model"] == "google:gemini@3.5-flash"
    assert t["messages"][0] == {"role": "system", "content": tasks.POLISH_SYSTEM}
    assert t["messages"][1] == {"role": "user", "content": "a fox in a forest"}
    assert t["outputFormat"] == "TEXT" and t["includeCost"] is True


def test_build_polish_text_appends_numbering_hint_only_above_one_version():
    single = tasks.build_polish_text("m", "a fox", "u", versions=1)
    assert single["messages"][0]["content"] == tasks.POLISH_SYSTEM
    multi = tasks.build_polish_text("m", "a fox", "u", versions=3)
    content = multi["messages"][0]["content"]
    assert content.startswith(tasks.POLISH_SYSTEM)
    assert "3 alternatives" in content and "numbered 1., 2., 3." in content


# ---- services/polish.py pure helpers --------------------------------------
def test_clamp_versions():
    assert polish.clamp_versions(None) == 1
    assert polish.clamp_versions("garbage") == 1
    assert polish.clamp_versions(0) == 1
    assert polish.clamp_versions(2) == 2
    assert polish.clamp_versions(9) == polish.VERSIONS_MAX


def test_split_versions_numbered_and_unnumbered():
    assert polish.split_versions("1. a\n2. b\n3. c", 3) == ["a", "b", "c"]
    assert polish.split_versions("just one", 3) == ["just one"]


def test_split_versions_truncates_extra_lines_to_versions():
    assert polish.split_versions("1. a\n2. b\n3. c\n4. d", 2) == ["a", "b"]


# ---- services/polish.py::run -----------------------------------------------
async def test_run_prompt_enhance_sums_cost_and_records_task_type_as_model_air():
    fake = FakeRunware()
    fake.script["run"] = [[{"text": "a", "cost": 0.0002}, {"text": "b", "cost": 0.0002}]]
    result = await polish.run(
        fake_factory(fake), "key", "rest", mode="promptEnhance", composed="a fox", versions=2
    )
    assert [v.text for v in result.versions] == ["a", "b"]
    assert result.cost == pytest.approx(0.0004)
    # No model AIR exists for promptEnhance; the literal task type is recorded instead
    # so the usage row's model_air stays non-null and stable.
    assert result.mode == "promptEnhance" and result.model == "promptEnhance"
    assert fake.calls[-1][0] == "run"
    assert "model" not in fake.calls[-1][1]


async def test_run_prompt_enhance_tolerates_fewer_rows_than_versions_requested():
    """Live API note: promptVersions=2 can still come back as a single row."""
    fake = FakeRunware()
    fake.script["run"] = [[{"text": "a", "cost": 0.0002}]]
    result = await polish.run(
        fake_factory(fake), "key", "rest", mode="promptEnhance", composed="a fox", versions=2
    )
    assert [v.text for v in result.versions] == ["a"]
    assert result.cost == pytest.approx(0.0002)
    assert result.model == "promptEnhance"


async def test_run_text_inference_splits_one_reply_sharing_one_cost():
    fake = FakeRunware()
    fake.script["run"] = [[{"text": "1. a\n2. b", "cost": 0.01}]]
    result = await polish.run(
        fake_factory(fake),
        "key",
        "rest",
        mode="textInference",
        composed="a fox",
        model="google:gemini@3.5-flash",
        versions=2,
    )
    assert [v.text for v in result.versions] == ["a", "b"]
    assert result.cost == pytest.approx(0.01)
    assert result.mode == "textInference" and result.model == "google:gemini@3.5-flash"
    assert fake.calls[-1][1]["model"] == "google:gemini@3.5-flash"


async def test_run_raises_on_empty_composed():
    fake = FakeRunware()
    with pytest.raises(ValueError, match="Write a prompt first"):
        await polish.run(fake_factory(fake), "key", "rest", mode="promptEnhance", composed="   ")
    assert fake.calls == []  # never even opened the client


async def test_run_requires_a_text_model_for_text_inference():
    fake = FakeRunware()
    with pytest.raises(ValueError, match="text model"):
        await polish.run(fake_factory(fake), "key", "rest", mode="textInference", composed="a fox")
    assert fake.calls == []


async def test_run_is_bounded_by_the_whole_call_budget_across_retries(monkeypatch):
    """A single slow attempt (or several rateLimit/connection retries) must not push an
    inline polish call past the spec's 30s -- I3. ``timeout_s`` alone only bounds one
    attempt inside run_with_policy, so the whole call is wrapped in the module-level
    ``POLISH_TIMEOUT_S`` too; lowering it here stands in for the 30s budget expiring."""
    monkeypatch.setattr(polish, "POLISH_TIMEOUT_S", 0.05)

    class SlowRunware:
        async def run(self, params, options=None):
            await asyncio.sleep(10)

    @asynccontextmanager
    async def slow_factory(api_key, transport):
        yield SlowRunware()

    with pytest.raises(RunwareError) as exc_info:
        await polish.run(slow_factory, "key", "rest", mode="promptEnhance", composed="a fox")
    assert exc_info.value.code == "timeout"
