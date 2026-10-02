"""Pure task builders for music & sound effects, speech and 3D objects.

Every model of these kinds takes a different set of parameters, so each builder reads
the model's harvested ``constraints.fields`` (``catalog.view()["constraints"]``) and
sends an optional field only when the model lists it. A model with no harvested fields
at all gets everything that was asked for: RunWare's free validation rejection and the
runner's fallback chain then drop what it does not take."""

from __future__ import annotations

from ..schemas.media import TASK_TYPES, AudioRequest, MediaRequest, Model3DRequest, SpeechRequest

PROTECTED = ("taskType", "taskUUID", "model")


def _constraints(model_row: dict | None) -> dict:
    c = (model_row or {}).get("constraints")
    return c if isinstance(c, dict) else {}


def _fields(model_row: dict | None) -> dict:
    fields = _constraints(model_row).get("fields")
    return fields if isinstance(fields, dict) else {}


def _takes(fields: dict, name: str) -> bool:
    """Unknown model (nothing harvested): assume yes and let RunWare say otherwise."""
    return not fields or name in fields


def _clamp(value: float, spec: dict | None) -> float | int:
    """Into the model's own ``min``/``max``; whole numbers stay ints on the wire."""
    v = float(value)
    spec = spec if isinstance(spec, dict) else {}
    if isinstance(spec.get("min"), (int, float)):
        v = max(float(spec["min"]), v)
    if isinstance(spec.get("max"), (int, float)):
        v = min(float(spec["max"]), v)
    return int(v) if v.is_integer() else v


def _settings(requested: dict, fields: dict) -> dict:
    """The posted ``settings.*`` values the model actually lists."""
    return {
        str(k): v
        for k, v in (requested or {}).items()
        if v is not None and v != "" and _takes(fields, f"settings.{k}")
    }


def _base(kind: str, req: MediaRequest, task_uuid: str) -> dict:
    return {
        "taskType": TASK_TYPES[kind],
        "taskUUID": task_uuid,
        "model": req.model,
        "outputType": "URL",
        "outputFormat": req.output_format,
        "includeCost": True,
    }


def fold_seed(seed: int | None, spec: dict | None) -> int | None:
    """``seed`` mapped into the model's own ``min``..``max`` when it lies outside them.

    Seed ranges differ per model (ACE-Step 0..2147483647, MiniMax Music 0..1000000, Tripo
    1..20240919), and a seed easily travels from one to another: a remix, a retry, a
    value left in the box when the model changes. A seed is only a starting number, so
    an out-of-range one is folded in by modulo rather than refused: the same seed always
    gives the same value, so repeating a job still repeats it."""
    if seed is None or not isinstance(spec, dict):
        return seed
    lo, hi = spec.get("min"), spec.get("max")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or hi < lo:
        return seed
    lo, hi = int(lo), int(hi)
    if lo <= seed <= hi:
        return seed
    return lo + (int(seed) - lo) % (hi - lo + 1)


def _finish(task: dict, req: MediaRequest, settings: dict, fields: dict) -> dict:
    if req.seed is not None and _takes(fields, "seed"):
        task["seed"] = fold_seed(req.seed, fields.get("seed"))
    if settings:
        task["settings"] = settings
    task.update({k: v for k, v in (req.extra_json or {}).items() if k not in PROTECTED})
    return task


def build_audio_task(req: AudioRequest, task_uuid: str, model_row: dict | None) -> dict:
    fields = _fields(model_row)
    task = _base("audio", req, task_uuid)
    task["positivePrompt"] = req.prompt
    if req.negative_prompt.strip() and _takes(fields, "negativePrompt"):
        task["negativePrompt"] = req.negative_prompt.strip()
    if req.duration is not None and _takes(fields, "duration"):
        task["duration"] = _clamp(req.duration, fields.get("duration"))
    settings = _settings(req.settings, fields)
    if settings.get("lyricsOptimizer") is False:
        settings.pop("lyricsOptimizer")  # like instrumental: only ever sent as true
    if req.lyrics.strip() and _takes(fields, "settings.lyrics"):
        settings["lyrics"] = req.lyrics.strip()
    # Sent only as ``true``. MiniMax's rule is about the switch being *provided* ("when
    # neither instrumental nor lyricsOptimizer is provided, lyrics is required"), so an
    # explicit false is at best noise.
    if req.instrumental is True and _takes(fields, "settings.instrumental"):
        settings["instrumental"] = True
    return _finish(task, req, settings, fields)


NEEDS_LYRICS = (
    "Type the lyrics for a song with vocals, or choose Instrumental, or let the model "
    "write the lyrics."
)


def resolve_audio(req: AudioRequest, fields: dict) -> AudioRequest:
    """Make a music request one the model will accept, or refuse it before it is sent.

    Only for a model that lists ``settings.instrumental`` (MiniMax Music): its rule is
    "when neither instrumental nor lyricsOptimizer is provided, lyrics is required",
    "when instrumental is true, lyrics cannot be used" and "…lyricsOptimizer cannot be
    true". So:

    * no lyrics and no word about vocals  -> instrumental (lyrics are not needed for
      music without singing; the request is completed rather than sent broken);
    * instrumental                        -> any lyrics and the lyrics writer are left out;
    * vocals with the model writing them  -> ``settings.lyricsOptimizer`` stays true;
    * vocals with your own lyrics, none typed -> ``ValueError(NEEDS_LYRICS)``: this is
      the one case where lyrics really are required, and nothing is sent.

    The returned request is what gets stored on the job, so it says what was asked of
    the model. Any other model (ACE-Step: optional lyrics, no such rule) is returned
    untouched."""
    if "settings.instrumental" not in (fields or {}):
        return req
    lyrics = req.lyrics.strip()
    settings = dict(req.settings or {})
    writes_lyrics = settings.get("lyricsOptimizer") is True
    if req.instrumental is True or (req.instrumental is None and not lyrics and not writes_lyrics):
        settings.pop("lyricsOptimizer", None)
        return req.model_copy(update={"instrumental": True, "lyrics": "", "settings": settings})
    if not lyrics and not writes_lyrics:
        raise ValueError(NEEDS_LYRICS)
    return req


def build_speech_task(req: SpeechRequest, task_uuid: str, model_row: dict | None) -> dict:
    fields = _fields(model_row)
    task = _base("speech", req, task_uuid)
    speech: dict = {"text": req.text_to_read}
    if req.voice.strip():
        # sent as typed even when it is not on the harvested list: a voice RunWare
        # added since is valid, and one it does not know is rejected for free
        speech["voice"] = req.voice.strip()
    if req.language.strip() and _takes(fields, "speech.language"):
        speech["language"] = req.language.strip()
    if req.speed is not None and _takes(fields, "speech.speed"):
        speech["speed"] = _clamp(req.speed, fields.get("speech.speed"))
    task["speech"] = speech
    return _finish(task, req, _settings(req.settings, fields), fields)


def resolve(req: MediaRequest, kind: str, fields: dict) -> MediaRequest:
    """The request as it will actually be sent, or a ``ValueError`` when it cannot be
    made valid: the seed inside the model's range, and for music the vocals/lyrics rule
    (``resolve_audio``). ``generate.enqueue_media`` stores what this returns, so a job's
    request says what was asked of the model."""
    seed = fold_seed(req.seed, (fields or {}).get("seed"))
    if seed != req.seed:
        req = req.model_copy(update={"seed": seed})
    if kind == "audio":
        req = resolve_audio(req, fields)
    return req


def image_input_key(model_row: dict | None) -> str:
    """Where a 3D model takes its source image: ``inputs.images`` (a list: Tripo,
    Meshy) when its docs list that, else ``inputs.image`` (Hunyuan, TRELLIS)."""
    inputs = _constraints(model_row).get("inputs")
    inputs = inputs if isinstance(inputs, dict) else {}
    return "images" if "images" in inputs and "image" not in inputs else "image"


def max_images(model_row: dict | None) -> int:
    """How many pictures a 3D model takes: its ``inputs.images`` cap (Tripo and Meshy 4,
    Rodin 5, Hunyuan Pro 8), or 1 for a single-image model and for one nothing is known
    about."""
    inputs = _constraints(model_row).get("inputs")
    spec = inputs.get("images") if isinstance(inputs, dict) else None
    if image_input_key(model_row) != "images" or not isinstance(spec, dict):
        return 1
    try:
        return max(1, int(spec.get("max_items") or 1))
    except (TypeError, ValueError):
        return 1


def build_3d_task(
    req: Model3DRequest, task_uuid: str, media: dict[int, str], model_row: dict | None
) -> dict:
    fields = _fields(model_row)
    task = _base("3d", req, task_uuid)
    if req.prompt:
        task["positivePrompt"] = req.prompt
    if req.negative_prompt.strip() and _takes(fields, "negativePrompt"):
        task["negativePrompt"] = req.negative_prompt.strip()
    uuids = [media[i] for i in req.image_asset_ids if media.get(i)]
    if uuids:
        key = image_input_key(model_row)
        # several views, in the order they were picked, for a model that takes a list;
        # the first (main) one alone for a model that takes a single image
        task["inputs"] = {key: uuids[: max_images(model_row)] if key == "images" else uuids[0]}
    return _finish(task, req, _settings(req.settings, fields), fields)


def build_media_task(
    kind: str, req: MediaRequest, task_uuid: str, media: dict[int, str], model_row: dict | None
) -> dict:
    if kind == "audio":
        return build_audio_task(req, task_uuid, model_row)
    if kind == "speech":
        return build_speech_task(req, task_uuid, model_row)
    if kind == "3d":
        return build_3d_task(req, task_uuid, media, model_row)
    raise ValueError(f"not a media kind: {kind}")
