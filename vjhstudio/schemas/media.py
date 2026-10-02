"""Requests for the three kinds beside image and video: music & sound effects
(``audio``), text-to-speech (``speech``) and 3D objects (``3d``). Each is deliberately
small: what a model accepts beyond these named fields differs per model and travels in
``settings`` (the model's own ``settings.*`` keys) or ``extra_json`` (merged last)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MEDIA_KINDS = ("audio", "speech", "3d")
AUDIO_FORMATS = ("MP3", "WAV", "FLAC", "OGG")
MODEL3D_FORMATS = ("GLB", "FBX")
TASK_TYPES = {"audio": "audioInference", "speech": "audioInference", "3d": "3dInference"}


class _MediaRequest(BaseModel):
    project_id: int
    model: str
    seed: int | None = None
    settings: dict = Field(default_factory=dict)
    extra_json: dict = Field(default_factory=dict)
    title: str | None = None


class AudioRequest(_MediaRequest):
    """A piece of music or a sound effect, described in words."""

    prompt: str
    negative_prompt: str = ""
    lyrics: str = ""
    instrumental: bool | None = None  # None = the model has no such switch / leave it alone
    duration: float | None = Field(None, gt=0, le=3600)
    output_format: Literal["MP3", "WAV", "FLAC", "OGG"] = "MP3"

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 2:
            raise ValueError("describe the music or sound you want")
        return v

    def text(self) -> str:
        return self.prompt


class SpeechRequest(_MediaRequest):
    """A text read aloud by one voice."""

    text_to_read: str = Field(alias="text")
    voice: str = ""
    language: str = ""
    speed: float | None = Field(None, gt=0, le=4)
    output_format: Literal["MP3", "WAV", "FLAC", "OGG"] = "MP3"

    model_config = {"populate_by_name": True}

    @field_validator("text_to_read")
    @classmethod
    def _text(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("type the text to read aloud")
        return v

    def text(self) -> str:
        return self.text_to_read


class Model3DRequest(_MediaRequest):
    """A 3D object from a description or from an image -- never both."""

    prompt: str = ""
    negative_prompt: str = ""
    # ``image_asset_ids`` is the truth: one picture, or several views of the same object
    # in the order they were picked (the first is the main, front view). ``image_asset_id``
    # is always its first entry; it is what requests stored before multi-view carry.
    image_asset_id: int | None = None
    image_asset_ids: list[int] = Field(default_factory=list)
    output_format: Literal["GLB", "FBX"] = "GLB"

    @model_validator(mode="after")
    def _something_to_build_from(self):
        self.prompt = (self.prompt or "").strip()
        ids = list(self.image_asset_ids or [])
        if not ids and self.image_asset_id is not None:
            ids = [self.image_asset_id]
        self.image_asset_ids = list(dict.fromkeys(ids))  # de-duplicated, order kept
        self.image_asset_id = self.image_asset_ids[0] if self.image_asset_ids else None
        if not self.prompt and self.image_asset_id is None:
            raise ValueError("describe the object or pick an image to build it from")
        if self.prompt and self.image_asset_id is not None:
            # Models take one or the other (Hunyuan: "provide exactly one of"); sent
            # both, the image was dropped by a free retry and the text rendered instead.
            raise ValueError("use a description or an image, not both")
        return self

    def text(self) -> str:
        return self.prompt or "3D object from an image"


REQUEST_FOR_KIND: dict[str, type[_MediaRequest]] = {
    "audio": AudioRequest,
    "speech": SpeechRequest,
    "3d": Model3DRequest,
}
MediaRequest = AudioRequest | SpeechRequest | Model3DRequest
REQUEST_FOR_KIND_TYPES = (AudioRequest, SpeechRequest, Model3DRequest)


def request_from_json(kind: str, data: dict) -> MediaRequest:
    """Rebuild the stored ``jobs.request_json`` (written with ``dump``)."""
    return REQUEST_FOR_KIND[kind](**data)


def dump(req: MediaRequest) -> dict:
    """``model_dump`` with the speech text under its public name, so the stored JSON
    reads ``{"text": …}`` and round-trips through ``request_from_json``."""
    return req.model_dump(by_alias=True)
