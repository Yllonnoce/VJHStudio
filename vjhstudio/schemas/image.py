from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PromptForm(BaseModel):
    subject: str = ""
    style: str = ""
    mood: str = ""
    lighting: str = ""
    camera: str = ""
    composition: str = ""
    colour: str = ""
    extras: str = ""
    negative: str = ""
    use_default_negative: bool = True
    no_text: bool = True


class ImageRequest(BaseModel):
    project_id: int
    prompt_id: int | None = None
    model: str
    form: PromptForm = Field(default_factory=PromptForm)
    final_prompt: str | None = None
    width: int = 1024
    height: int = 1024
    number_results: int = Field(1, ge=1, le=8)
    seed: int | None = None
    steps: int | None = Field(None, ge=1, le=150)
    cfg_scale: float | None = Field(None, ge=0, le=30)
    scheduler: str | None = None
    strength: float | None = Field(None, ge=0, le=1)
    output_format: Literal["PNG", "JPG", "WEBP"] = "PNG"
    seed_image_asset_id: int | None = None
    reference_asset_ids: list[int] = Field(default_factory=list)
    extra_json: dict = Field(default_factory=dict)
    title: str | None = None

    @field_validator("width", "height")
    @classmethod
    def _dim(cls, v: int) -> int:
        if v % 64 or not 128 <= v <= 2048:
            raise ValueError("must be a multiple of 64 between 128 and 2048")
        return v
