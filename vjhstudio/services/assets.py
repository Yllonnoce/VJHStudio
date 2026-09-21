"""Asset library: store uploads (deduped by content), tag, thumbnail, list, delete.

Mirrors the guard pattern in ``services/outputs.py``: a row's path is only ever
followed onto the filesystem after ``_contained`` confirms it resolves under the
expected root.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Paths
from ..models import Asset, Job, JobStatus
from ..runware.download import make_thumbnail

PER_PAGE = 48

ALLOWED_IMAGE: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
ALLOWED_VIDEO: dict[str, str] = {
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
}

_SINGLE_REF_FIELDS = ("seed_image_asset_id", "first_frame_asset_id", "last_frame_asset_id")
_ACTIVE_STATUSES = (JobStatus.queued.value, JobStatus.running.value)


class UploadError(Exception):
    """Raised by ``store_upload`` for a rejected file. ``.status`` is an HTTP status:
    413 (too large) or 415 (unsupported content type)."""

    def __init__(self, status: int, message: str = ""):
        super().__init__(message or f"upload error {status}")
        self.status = status


def normalize_tags(text: str) -> str:
    """``"Fox, animals , fox"`` -> ``",fox,animals,"``: lowercase, trimmed, de-duplicated,
    order preserved, stored with leading/trailing commas so ``LIKE '%,tag,%'`` matches."""
    seen: list[str] = []
    for part in (text or "").split(","):
        t = part.strip().lower()
        if t and t not in seen:
            seen.append(t)
    return "," + ",".join(seen) + "," if seen else ","


def tags_list(tags: str) -> list[str]:
    return [t for t in (tags or "").split(",") if t]


def _contained(root: Path, name: str | None) -> Path | None:
    """Resolve ``name`` under ``root``; None when it escapes (``..``, absolute, symlink)."""
    if not name:
        return None
    base = root.resolve()
    target = Path(base / name).resolve()
    return target if target.is_relative_to(base) else None


def _dims(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.width, im.height
    except Exception:  # noqa: BLE001 - a non-image (or corrupt) file just has no dimensions
        return None, None


def _thumb_name(asset: Asset) -> str:
    return f"asset-{asset.sha256[:12]}.jpg"


def _like_escape(s: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so a LIKE pattern built from user input can't
    accidentally use SQL wildcards; pair with ``.like(pattern, escape="\\\\")``."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _find_by_sha256(session: Session, digest: str) -> Asset | None:
    return session.execute(select(Asset).where(Asset.sha256 == digest)).scalar_one_or_none()


def store_upload(
    session: Session,
    paths: Paths,
    *,
    original_name: str,
    content: bytes,
    mime: str,
    tags: str = "",
    max_mb: int = 200,
) -> tuple[Asset, bool]:
    """Write ``content`` under ``data/uploads/<sha12>.<ext>`` and record it. Identical bytes
    (by sha256) return the existing row with ``created=False`` instead of writing again."""
    if mime in ALLOWED_IMAGE:
        kind, ext = "image", ALLOWED_IMAGE[mime]
    elif mime in ALLOWED_VIDEO:
        kind, ext = "video", ALLOWED_VIDEO[mime]
    else:
        raise UploadError(415, f"unsupported content type: {mime}")
    if len(content) > max_mb * 1024 * 1024:
        raise UploadError(413, f"file exceeds {max_mb} MB limit")

    digest = hashlib.sha256(content).hexdigest()
    existing = _find_by_sha256(session, digest)
    if existing is not None:
        return existing, False

    sha12 = digest[:12]
    filename = f"{sha12}.{ext}"
    paths.uploads.mkdir(parents=True, exist_ok=True)
    dest = paths.uploads / filename
    if not dest.exists():
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(content)
        os.replace(part, dest)

    width = height = None
    if kind == "image":
        width, height = _dims(dest)
        paths.thumbs.mkdir(parents=True, exist_ok=True)
        make_thumbnail(dest, paths.thumbs / f"asset-{sha12}.jpg")

    asset = Asset(
        filename=filename,
        original_name=original_name,
        kind=kind,
        mime=mime,
        size_bytes=len(content),
        width=width,
        height=height,
        sha256=digest,
        tags=normalize_tags(tags),
    )
    try:
        with session.begin_nested():
            session.add(asset)
            session.flush()
    except IntegrityError:
        # Another store_upload for the same bytes committed between our lookup and our
        # insert; the savepoint above already rolled itself back. session_scope owns the
        # outer transaction, so we recover here rather than rolling that back too.
        existing = _find_by_sha256(session, digest)
        if existing is None:
            raise
        return existing, False
    return asset, True


def list_assets(
    session: Session,
    *,
    kind: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    page: int = 1,
    per_page: int = PER_PAGE,
) -> tuple[list[Asset], int]:
    """Newest first; ``q`` matches ``original_name``/``notes``, ``tag`` matches the
    ``,a,b,`` column with ``LIKE '%,tag,%'``."""
    filters = []
    if kind:
        filters.append(Asset.kind == kind)
    if tag:
        pattern = f"%,{_like_escape(tag.strip().lower())},%"
        filters.append(Asset.tags.like(pattern, escape="\\"))
    if q:
        pattern = f"%{_like_escape(q.strip())}%"
        filters.append(
            or_(
                Asset.original_name.like(pattern, escape="\\"),
                Asset.notes.like(pattern, escape="\\"),
            )
        )
    total = int(session.execute(select(func.count(Asset.id)).where(*filters)).scalar() or 0)
    page = max(1, int(page))
    per_page = max(1, int(per_page))
    rows = session.execute(
        select(Asset)
        .where(*filters)
        .order_by(Asset.created_at.desc(), Asset.id.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    ).scalars()
    return list(rows), total


def get(session: Session, asset_id: int) -> Asset | None:
    return session.get(Asset, asset_id)


def set_tags(session: Session, asset_id: int, tags: str) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise LookupError(asset_id)
    asset.tags = normalize_tags(tags)
    session.flush()
    return asset


def set_notes(session: Session, asset_id: int, notes: str | None) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise LookupError(asset_id)
    asset.notes = notes
    session.flush()
    return asset


def _referenced_by_active_job(session: Session, asset_id: int) -> bool:
    jobs = session.execute(select(Job).where(Job.status.in_(_ACTIVE_STATUSES))).scalars()
    for job in jobs:
        request = job.request_json or {}
        if any(request.get(field) == asset_id for field in _SINGLE_REF_FIELDS):
            return True
        if asset_id in (request.get("reference_asset_ids") or []):
            return True
    return False


def delete(session: Session, paths: Paths, asset_id: int) -> bool:
    """Delete the file, thumbnail and row. Refused (returns False) when a queued or
    running job's request still references this asset."""
    asset = session.get(Asset, asset_id)
    if asset is None:
        return False
    if _referenced_by_active_job(session, asset_id):
        return False
    upload_path = _contained(paths.uploads, asset.filename)
    if upload_path is not None:
        upload_path.unlink(missing_ok=True)
    if asset.kind == "image":
        thumb_path = _contained(paths.thumbs, _thumb_name(asset))
        if thumb_path is not None:
            thumb_path.unlink(missing_ok=True)
    session.delete(asset)
    session.flush()
    return True


def abs_path(paths: Paths, asset: Asset) -> Path:
    """The uploaded file for ``asset``. Raises LookupError when the row points outside
    the uploads root, so a file route can 404 instead of serving whatever it escaped to."""
    path = _contained(paths.uploads, asset.filename)
    if path is None:
        raise LookupError(asset.filename)
    return path


def thumb_rel(asset: Asset) -> str | None:
    """The thumbnail filename for an image asset, or None (videos have no thumbnail)."""
    return _thumb_name(asset) if asset.kind == "image" else None


def public_urls(asset: Asset) -> dict[str, str | None]:
    thumb = thumb_rel(asset)
    return {
        "url": f"/files/uploads/{asset.filename}",
        "thumb_url": f"/files/asset-thumbs/{thumb}" if thumb else None,
    }
