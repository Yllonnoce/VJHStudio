"""Pure builders: typed request -> RunWare task dict. No I/O."""

from __future__ import annotations

from ..schemas.image import ImageRequest
from ..schemas.video import VideoRequest
from ..services import prompts

PROTECTED = ("taskType", "taskUUID", "model")
PROMPT_ENHANCE_MAX_CHARS = 300
PROMPT_ENHANCE_MAX_LENGTH = 300
POLISH_SYSTEM = (
    "Rewrite the following into a single vivid image/video generation prompt of at most"
    " 120 words. Output only the prompt."
)
RESOLUTIONS: dict[str, tuple[int, int]] = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}
DEFAULT_RESOLUTION = "720p"
FRAME_ROLES = ("first", "last")


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


# ---- prompt polish ---------------------------------------------------------
def build_prompt_enhance(
    prompt: str, task_uuid: str, *, versions: int = 3, max_length: int = PROMPT_ENHANCE_MAX_LENGTH
) -> dict:
    """RunWare's ``promptEnhance``: a small, fixed-model task that only ever takes a
    prompt (<=300 chars, suffix-free — there is nothing to preserve past the cut,
    unlike ``prompts.cap``'s NO_TEXT_SUFFIX case) and returns ``promptVersions``
    rewrites. The live API rejects a ``model`` key here ("Invalid value for 'model'
    parameter...") -- the model is fixed server-side, so none is sent."""
    v = max(1, min(5, int(versions)))
    ml = max(12, min(400, int(max_length)))
    return {
        "taskType": "promptEnhance",
        "taskUUID": task_uuid,
        "prompt": (prompt or "")[:PROMPT_ENHANCE_MAX_CHARS],
        "promptMaxLength": ml,
        "promptVersions": v,
        "includeCost": True,
    }


def build_polish_text(
    model: str, composed: str, task_uuid: str, *, versions: int = 3, system: str = POLISH_SYSTEM
) -> dict:
    """RunWare's ``textInference`` used as a rewrite: one round trip, one cost row.
    Asking for more than one version appends a numbered-lines instruction so the single
    reply can be split client-side (``polish.split_versions``) instead of firing N calls
    or relying on an unverified ``numberResults`` knob."""
    v = max(1, int(versions))
    content = system
    if v > 1:
        content += (
            f" Give {v} alternatives, each on its own line numbered 1., 2., 3., and nothing else."
        )
    return {
        "taskType": "textInference",
        "taskUUID": task_uuid,
        "model": model,
        "messages": [
            {"role": "system", "content": content},
            {"role": "user", "content": composed},
        ],
        "outputFormat": "TEXT",
        "includeCost": True,
    }


# ---- video ---------------------------------------------------------------
def resolution_wh(resolution: str, video: dict | None = None) -> tuple[int, int]:
    """A preset name -> pixels. A curated ``video.dims`` entry wins over ``RESOLUTIONS``:
    LTX-2.3 only accepts dimensions that are multiples of 64, so its 720p is 1280x704
    while Veo's stays 1280x720. Unknown names fall back to 720p: the API is always sent
    width/height, never the preset string, so a stray value must still be renderable."""
    key = (resolution or "").strip().lower()
    dims = (video or {}).get("dims")
    if isinstance(dims, dict):
        for name, pair in dims.items():
            if str(name).strip().lower() != key:
                continue
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                return int(pair[0]), int(pair[1])
    return RESOLUTIONS.get(key, RESOLUTIONS[DEFAULT_RESOLUTION])


def provider_key(air: str) -> str:
    """``"google:3@2"`` -> ``"google"``: the key ``providerSettings`` is nested under."""
    return (air or "").split(":", 1)[0]


def _tidy(v: float) -> float | int:
    return int(v) if float(v).is_integer() else float(v)


def nearest(value: float, allowed: list) -> float | int | None:
    """The closest of ``allowed`` to ``value``; ties go to the shorter (cheaper) option."""
    choices = [c for c in allowed or [] if isinstance(c, (int, float)) and not isinstance(c, bool)]
    if not choices:
        return None
    return min(choices, key=lambda c: (abs(float(c) - float(value)), float(c)))


def build_video_task(
    req: VideoRequest, task_uuid: str, media: dict[int, str], model_row: dict
) -> dict:
    """``model_row`` is a ``catalog.view()`` dict: its ``tiers.video`` block says which
    durations and frame rates the provider will actually accept."""
    video = dict(((model_row or {}).get("tiers") or {}).get("video") or {})
    duration = nearest(req.duration, video.get("durations") or [])
    width, height = resolution_wh(req.resolution, video)
    task: dict = {
        "taskType": "videoInference",
        "taskUUID": task_uuid,
        "model": req.model,
        "positivePrompt": prompts.final_prompt(req),
        "outputType": "URL",
        "outputFormat": req.output_format,
        "includeCost": True,
        "duration": _tidy(duration if duration is not None else req.duration),
        "width": width,
        "height": height,
    }
    if req.fps is not None and video.get("fps"):
        task["fps"] = int(req.fps)
    if req.seed is not None:
        task["seed"] = req.seed
    inputs: dict = {}
    frames = [
        {"image": media[asset_id], "frame": role}
        for role, asset_id in zip(
            FRAME_ROLES, (req.first_frame_asset_id, req.last_frame_asset_id), strict=True
        )
        if asset_id is not None and asset_id in media
    ]
    if frames:
        inputs["frameImages"] = frames
    refs = [media[i] for i in req.reference_asset_ids if i in media]
    if refs:
        inputs["referenceImages"] = refs
    if inputs:
        task["inputs"] = inputs
    if req.provider_settings:
        task["providerSettings"] = {provider_key(req.model): dict(req.provider_settings)}
    task.update({k: v for k, v in (req.extra_json or {}).items() if k not in PROTECTED})
    return task
