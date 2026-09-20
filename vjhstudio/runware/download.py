"""Fetch RunWare output URLs to disk (they expire), with sidecars and thumbnails."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .results import ResultItem


class DownloadError(Exception):
    pass


@dataclass(frozen=True)
class SavedFile:
    path: Path
    size: int
    url: str
    item: ResultItem


def new_stem(now: datetime | None = None) -> str:
    return f"{(now or datetime.now(UTC)).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


async def _fetch(client: httpx.AsyncClient, url: str, dest: Path) -> int:
    part = dest.with_name(dest.name + ".part")
    size = 0
    async with client.stream("GET", url) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            async for chunk in r.aiter_bytes(1024 * 1024):
                f.write(chunk)
                size += len(chunk)
    os.replace(part, dest)
    return size


async def download_items(
    items: list[ResultItem],
    dest_dir: Path,
    ext: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    retries: int = 2,
    timeout: float = 120.0,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[SavedFile]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[SavedFile] = []
    async with httpx.AsyncClient(
        transport=transport, timeout=timeout, follow_redirects=True
    ) as client:
        for n, item in enumerate(items, 1):
            dest = dest_dir / f"{new_stem()}.{ext.lower()}"
            last: Exception | None = None
            for _ in range(retries + 1):
                try:
                    size = await _fetch(client, item.url, dest)
                    saved.append(SavedFile(dest, size, item.url, item))
                    last = None
                    break
                except httpx.HTTPError as e:
                    last = e
            if last is not None:
                raise DownloadError(f"could not download {item.url}: {last}") from last
            if on_progress:
                on_progress(n, len(items))
    return saved


def write_sidecar(media_path: Path, data: dict) -> Path:
    side = media_path.with_suffix(".json")
    part = side.with_name(side.name + ".part")
    part.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(part, side)
    return side


def make_thumbnail(src: Path, dest: Path, max_px: int = 384) -> Path | None:
    try:
        from PIL import Image

        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "JPEG", quality=85)
        return dest
    except Exception:  # noqa: BLE001
        return None
