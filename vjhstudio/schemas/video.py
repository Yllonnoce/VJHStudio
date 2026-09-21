from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .image import PromptForm

DURATION_MIN = 1.0
DURATION_MAX = 30.0


class VideoRequest(BaseModel):
    """A video generation request. Deliberately *not* an ``ImageRequest`` subclass: video
    models take a duration and a resolution preset where image models take pixels, and no
    negative prompt at all."""

    project_id: int
    prompt_id: int | None = None
    model: str
    form: PromptForm = Field(default_factory=lambda: PromptForm(no_text=False))
    final_prompt: str | None = None
    duration: float = 5
    resolution: str = "720p"
    fps: int | None = Field(None, ge=1, le=120)
    seed: int | None = None
    output_format: Literal["MP4", "WEBM"] = "MP4"
    first_frame_asset_id: int | None = None
    last_frame_asset_id: int | None = None
    reference_asset_ids: list[int] = Field(default_factory=list)
    provider_settings: dict = Field(default_factory=dict)
    extra_json: dict = Field(default_factory=dict)
    title: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_text_defaults_off(cls, data):
        """``PromptForm.no_text`` defaults True, which suits stills; a video prompt is
        rarely hurt by on-screen text, so video flips the default. Only the *default*
        moves: a value the caller actually set — in a dict, or on a ``PromptForm`` whose
        ``model_fields_set`` records the assignment — is left exactly as given."""
        if not isinstance(data, dict):
            return data
        if "form" not in data:
            return {**data, "form": {"no_text": False}}
        form = data["form"]
        if isinstance(form, dict) and "no_text" not in form:
            return {**data, "form": {**form, "no_text": False}}
        if isinstance(form, PromptForm) and "no_text" not in form.model_fields_set:
            return {**data, "form": {**form.model_dump(), "no_text": False}}
        return data

    @field_validator("duration")
    @classmethod
    def _duration(cls, v: float) -> float:
        if not DURATION_MIN <= v <= DURATION_MAX:
            raise ValueError(f"must be between {DURATION_MIN:g} and {DURATION_MAX:g} seconds")
        return v
