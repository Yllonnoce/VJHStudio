"""Typed key/value settings. Precedence: env > settings table > SPEC default."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any, Mapping
from sqlalchemy.orm import Session
from ..models import Setting


@dataclass(frozen=True)
class Spec:
    type: type
    default: Any
    choices: tuple[str, ...] | None = None
    env: str | None = None


SPEC: dict[str, Spec] = {
    "runware.transport": Spec(str, "rest", ("rest", "websocket"), "RUNWARESTUDIO_TRANSPORT"),
    "runware.timeout_s": Spec(int, 1200),
    "jobs.concurrency": Spec(int, 3),
    "paths.outputs_dir": Spec(str, ""),
    "defaults.image_model": Spec(str, "runware:101@1"),
    "defaults.video_model": Spec(str, "lightricks:ltx@2.3"),
    "defaults.polish_model": Spec(str, ""),
    "defaults.output_format_image": Spec(str, "PNG", ("PNG", "JPG", "WEBP")),
    "defaults.output_format_video": Spec(str, "MP4", ("MP4", "WEBM")),
    "defaults.negative_prompt": Spec(str, "blurry, low quality, watermark, text, deformed"),
    "prompt.polish_mode": Spec(str, "promptEnhance", ("promptEnhance", "textInference")),
    "ui.theme": Spec(str, "dark", ("dark", "light")),
    "uploads.max_mb": Spec(int, 200),
}


def _cast(key: str, raw: str) -> Any:
    spec = SPEC[key]
    if spec.type is int:
        try:
            return int(raw)
        except ValueError as e:
            raise ValueError(f"{key} must be an integer") from e
    if spec.type is bool:
        return raw.lower() in ("1", "true", "yes", "on")
    if spec.choices and raw not in spec.choices:
        raise ValueError(f"{key} must be one of {', '.join(spec.choices)}")
    return raw


def get(session: Session, key: str, env: Mapping[str, str] | None = None) -> Any:
    spec = SPEC[key]
    env = os.environ if env is None else env
    if spec.env and env.get(spec.env):
        return _cast(key, env[spec.env])
    row = session.get(Setting, key)
    return _cast(key, row.value) if row else spec.default


def set_many(session: Session, values: Mapping[str, str]) -> None:
    for key, raw in values.items():
        if key not in SPEC:
            raise ValueError(f"unknown setting {key}")
        _cast(key, raw)
    for key, raw in values.items():
        row = session.get(Setting, key)
        if row:
            row.value = raw
        else:
            session.add(Setting(key=key, value=raw))
    session.flush()


def all_values(session: Session, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return {k: get(session, k, env) for k in SPEC}
