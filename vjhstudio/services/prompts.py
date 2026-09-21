"""Deterministic prompt composition. Mirrored byte-for-byte by static/js/app.js composePrompt."""

from __future__ import annotations

import re

from ..schemas.image import ImageRequest, PromptForm
from ..schemas.video import VideoRequest

FIELD_ORDER = ("subject", "style", "mood", "lighting", "camera", "composition", "colour", "extras")
PROMPT_MAX = 2900
NO_TEXT_SUFFIX = (
    " Pure artwork only: absolutely no text, no words, no letters, no labels, no captions,"
    " no title, no watermark, no signature, no borders, no user interface elements."
)
NO_TEXT_NEGATIVE = (
    "text, words, letters, typography, writing, labels, captions, title, watermark,"
    " signature, logo, user interface, borders, frame, split image, collage"
)
_WS = re.compile(r"\s+")


def clean(s: str | None) -> str:
    return _WS.sub(" ", s or "").strip().rstrip(" ,;.").strip()


def compose(form: PromptForm | dict) -> str:
    data = form.model_dump() if isinstance(form, PromptForm) else dict(form)
    parts: list[str] = []
    seen: set[str] = set()
    for key in FIELD_ORDER:
        v = clean(data.get(key))
        if v and v.lower() not in seen:
            parts.append(v)
            seen.add(v.lower())
    return ", ".join(parts)


def _tokens(text: str) -> list[str]:
    return [t for t in (clean(x) for x in (text or "").split(",")) if t]


def build_negative(form: PromptForm, default_negative: str, no_text: bool) -> str:
    toks = _tokens(form.negative)
    if form.use_default_negative:
        toks += _tokens(default_negative)
    if no_text:
        toks += _tokens(NO_TEXT_NEGATIVE)
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t.lower() not in seen:
            out.append(t)
            seen.add(t.lower())
    return ", ".join(out)


def cap(prompt: str, suffix: str = "") -> str:
    body = (prompt or "")[: PROMPT_MAX - len(suffix)]
    return body + suffix


def apply_no_text(prompt: str) -> str:
    return cap(prompt, NO_TEXT_SUFFIX)


def final_prompt(req: ImageRequest | VideoRequest) -> str:
    base = clean(req.final_prompt) if req.final_prompt else compose(req.form)
    return cap(base, NO_TEXT_SUFFIX if req.form.no_text else "")
