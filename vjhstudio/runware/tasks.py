"""Pure builders: typed request -> RunWare task dict. No I/O."""

from __future__ import annotations

from ..schemas.image import ImageRequest
from ..services import prompts

PROTECTED = ("taskType", "taskUUID", "model")


def dim64(v: int) -> int:
    return int(max(512, min(2048, round(v / 64) * 64)))


def clamp_aspect(w: int, h: int) -> tuple[int, int]:
    if w > 2 * h:
        w = dim64(2 * h)
    elif h > 2 * w:
        h = dim64(2 * w)
    return w, h


def build_image_task(
    req: ImageRequest, task_uuid: str, media: dict[int, str], family: str, negative: str
) -> dict:
    diffusion = family != "instruction"
    w, h = (
        clamp_aspect(dim64(req.width), dim64(req.height)) if diffusion else (req.width, req.height)
    )
    task: dict = {
        "taskType": "imageInference",
        "taskUUID": task_uuid,
        "model": req.model,
        "positivePrompt": prompts.final_prompt(req),
        "outputType": "URL",
        "outputFormat": req.output_format,
        "includeCost": True,
        "numberResults": req.number_results,
        "width": w,
        "height": h,
    }
    if req.seed is not None:
        task["seed"] = req.seed
    if diffusion:
        if negative:
            task["negativePrompt"] = negative
        for key, val in (
            ("steps", req.steps),
            ("CFGScale", req.cfg_scale),
            ("scheduler", req.scheduler),
        ):
            if val is not None:
                task[key] = val
    refs = [media[i] for i in req.reference_asset_ids if i in media]
    seed_img = media.get(req.seed_image_asset_id) if req.seed_image_asset_id is not None else None
    inputs: dict = {}
    if diffusion:
        if seed_img:
            inputs["seedImage"] = seed_img
            task["strength"] = req.strength if req.strength is not None else 0.8
        if refs:
            inputs["referenceImages"] = refs
    else:
        all_refs = ([seed_img] if seed_img else []) + refs
        if all_refs:
            inputs["referenceImages"] = all_refs
    if inputs:
        task["inputs"] = inputs
    task.update({k: v for k, v in (req.extra_json or {}).items() if k not in PROTECTED})
    return task
