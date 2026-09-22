"""Fetch RunWare output URLs to disk (they expire), with sidecars and thumbnails."""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from .results import ResultItem

log = logging.getLogger(__name__)

POSTER_TIMEOUT_S = 30
_CREATE_NO_WINDOW = 0x08000000  # Windows: never flash a console for a background frame grab


class DownloadError(Exception):
    pass


@dataclass(frozen=True)
class SavedFile:
    path: Path
    size: int
    url: str
    item: ResultItem


def new_stem(now: datetime | None = None) -> str:
    return (
        f"{(now or datetime.now().astimezone()).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    )


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
            part = dest.with_name(dest.name + ".part")
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
                part.unlink(missing_ok=True)
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


def ffmpeg_exe() -> str | None:
    """The ffmpeg binary to shell out to, or None when there is none.

    ``imageio-ffmpeg`` ships a binary in its wheel for Windows, macOS and Linux, which
    is the whole point: this app installs on all three and a system ffmpeg cannot be
    assumed. A real one on PATH is the fallback for the rare platform the wheel skips.
    """
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception as e:  # noqa: BLE001 - no bundled binary is a fallback, not a failure
        log.debug("imageio-ffmpeg unavailable: %s", e)
    return shutil.which("ffmpeg")


def _grab_frame(exe: str, src: Path, dest: Path, max_px: int, at_s: float) -> bool:
    """One ffmpeg run. True only when a non-empty JPEG actually landed: seeking past the
    end of a very short clip exits 0 and writes nothing at all -- which is also why the
    destination is cleared first, so a leftover JPEG from an earlier run (a poster for a
    video since replaced, or the previous seek attempt) can never be mistaken for the
    frame this call asked for."""
    with suppress(OSError):
        dest.unlink(missing_ok=True)
    # The commas inside min() belong to the expression, not to the filter graph.
    scale = f"scale=min({max_px}\\,iw):min({max_px}\\,ih):force_original_aspect_ratio=decrease"
    cmd = [
        exe, "-nostdin", "-y",
        "-ss", f"{at_s:g}",
        "-i", str(src),
        "-frames:v", "1",
        "-vf", scale,
        "-pix_fmt", "yuvj420p",
        "-q:v", "3",
        "-an", "-sn",
        "-f", "image2",
        str(dest),
    ]  # fmt: skip
    extra = {"creationflags": _CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell, path from our own config
            cmd,
            timeout=POSTER_TIMEOUT_S,
            capture_output=True,
            stdin=subprocess.DEVNULL,  # belt and braces with -nostdin: never inherit a tty
            **extra,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.info("ffmpeg could not run on %s: %s", src.name, e)
        return False
    if proc.returncode != 0:
        log.debug("ffmpeg exited %d on %s", proc.returncode, src.name)
    try:
        return dest.is_file() and dest.stat().st_size > 0
    except OSError:
        return False


def make_poster(src: Path, dest: Path, max_px: int = 384, at_s: float = 0.5) -> Path | None:
    """A JPEG frame from a video, no larger than ``max_px`` on its longest side.

    Tries ``at_s`` first and falls back to the very first frame, which is what a clip
    shorter than ``at_s`` has to offer. Returns None (never raises) when there is no
    ffmpeg or it cannot read the file: a missing poster is a generic icon, not an error.
    """
    if not src.is_file():
        return None
    exe = ffmpeg_exe()
    if exe is None:
        log.info("no ffmpeg available: %s gets the generic video poster", src.name)
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.info("could not create %s: %s", dest.parent, e)
        return None
    seeks = (at_s, 0.0) if at_s > 0 else (0.0,)
    for seek in seeks:
        if _grab_frame(exe, src, dest, max_px, seek):
            return dest
    with suppress(OSError):
        dest.unlink(missing_ok=True)  # an empty stub is worse than no poster at all
    log.info("no frame could be read from %s", src.name)
    return None
