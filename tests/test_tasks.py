from vjhstudio.runware import tasks
from vjhstudio.schemas.image import ImageRequest, PromptForm


def req(**kw):
    base = dict(project_id=1, model="runware:101@1", form=PromptForm(subject="fox", no_text=False))
    base.update(kw)
    return ImageRequest(**base)


def test_dim64_and_aspect():
    assert tasks.dim64(1000) == 1024 and tasks.dim64(100) == 512 and tasks.dim64(5000) == 2048
    assert tasks.clamp_aspect(2048, 512) == (1024, 512)
    assert tasks.clamp_aspect(512, 2048) == (512, 1024)
    assert tasks.clamp_aspect(1024, 768) == (1024, 768)


def test_diffusion_task_shape():
    t = tasks.build_image_task(
        req(steps=28, cfg_scale=3.5, seed=7, extra_json={"scheduler": "Euler", "model": "hack"}),
        "uuid-1",
        {},
        "diffusion",
        "blurry",
    )
    assert (
        t["taskType"] == "imageInference"
        and t["taskUUID"] == "uuid-1"
        and t["model"] == "runware:101@1"
    )
    assert t["positivePrompt"] == "fox" and t["negativePrompt"] == "blurry"
    assert t["outputType"] == "URL" and t["includeCost"] is True and t["outputFormat"] == "PNG"
    assert t["width"] == 1024 and t["height"] == 1024 and t["numberResults"] == 1
    assert (
        t["steps"] == 28 and t["CFGScale"] == 3.5 and t["seed"] == 7 and t["scheduler"] == "Euler"
    )


def test_instruction_task_drops_diffusion_knobs_and_uses_reference_images():
    t = tasks.build_image_task(
        req(model="google:4@2", steps=28, seed_image_asset_id=5, reference_asset_ids=[6]),
        "u",
        {5: "uuid-5", 6: "uuid-6"},
        "instruction",
        "blurry",
    )
    assert "steps" not in t and "negativePrompt" not in t and "strength" not in t
    assert t["inputs"]["referenceImages"] == ["uuid-5", "uuid-6"]


def test_diffusion_seed_image_and_strength():
    t = tasks.build_image_task(req(seed_image_asset_id=5), "u", {5: "uuid-5"}, "diffusion", "")
    assert (
        t["inputs"]["seedImage"] == "uuid-5" and t["strength"] == 0.8 and "negativePrompt" not in t
    )


def test_no_text_suffix_applied():
    r = req()
    r.form.no_text = True
    t = tasks.build_image_task(r, "u", {}, "diffusion", "")
    assert t["positivePrompt"].endswith("no user interface elements.")
