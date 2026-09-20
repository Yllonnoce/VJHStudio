import json
from pathlib import Path

import pytest

from vjhstudio.schemas.image import ImageRequest, PromptForm
from vjhstudio.services import prompts

CASES = json.loads((Path(__file__).parent / "fixtures" / "compose_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["composed"][:20] or "empty" for c in CASES])
def test_compose_cases(case):
    assert prompts.compose(case["form"]) == case["composed"]


def test_compose_is_deterministic_and_ordered():
    form = PromptForm(extras="z", subject="a", colour="c")
    assert prompts.compose(form) == "a, c, z" == prompts.compose(form)


def test_build_negative_merges_and_dedupes():
    form = PromptForm(negative="blurry, Text", use_default_negative=True, no_text=True)
    neg = prompts.build_negative(form, "blurry, low quality", no_text=True)
    toks = [t.strip() for t in neg.split(",")]
    assert toks[:3] == ["blurry", "Text", "low quality"]
    assert "watermark" in toks and len(toks) == len({t.lower() for t in toks})


def test_build_negative_without_defaults():
    assert (
        prompts.build_negative(
            PromptForm(negative="", use_default_negative=False, no_text=False),
            "x",
            no_text=False,
        )
        == ""
    )


def test_cap_is_suffix_safe():
    body = "a" * 5000
    out = prompts.cap(body, prompts.NO_TEXT_SUFFIX)
    assert len(out) <= prompts.PROMPT_MAX and out.endswith(prompts.NO_TEXT_SUFFIX)


def test_final_prompt_prefers_user_text():
    req = ImageRequest(
        project_id=1,
        model="runware:101@1",
        form=PromptForm(subject="fox", no_text=False),
        final_prompt="my own words",
    )
    assert prompts.final_prompt(req) == "my own words"
    req2 = ImageRequest(
        project_id=1, model="runware:101@1", form=PromptForm(subject="fox", no_text=True)
    )
    assert prompts.final_prompt(req2) == "fox" + prompts.NO_TEXT_SUFFIX


def test_image_request_validation():
    with pytest.raises(ValueError):
        ImageRequest(project_id=1, model="m", form=PromptForm(), width=1000)
    with pytest.raises(ValueError):
        ImageRequest(project_id=1, model="m", form=PromptForm(), number_results=9)
    assert (
        ImageRequest(project_id=1, model="m", form=PromptForm(), width=1024, height=768).height
        == 768
    )
