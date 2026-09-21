"""URL helpers for served output files, shared between the jobs views and the gallery.

Keeping this in one place means `/files/outputs/<slug>/<filename>` and
`/files/thumbs/<name>` are spelled exactly once outside of `routes/files.py` itself.
"""

from __future__ import annotations


def output_url(slug: str, filename: str) -> str:
    return f"/files/outputs/{slug}/{filename}"


def thumb_url(thumb_rel_path: str | None) -> str | None:
    """``thumb_rel_path`` is ``thumbs/<stem>.jpg`` relative to the data dir; the route
    only needs the filename, since the thumbs root is fixed."""
    if not thumb_rel_path:
        return None
    name = thumb_rel_path.rsplit("/", 1)[-1]
    return f"/files/thumbs/{name}"


def asset_url(filename: str) -> str:
    return f"/files/uploads/{filename}"


def asset_thumb_url(thumb_name: str | None) -> str | None:
    """``thumb_name`` is the bare filename ``services.assets.thumb_rel`` returns (already
    just a name, unlike ``thumb_rel_path`` above); ``None`` for a video (no thumbnail)."""
    return f"/files/asset-thumbs/{thumb_name}" if thumb_name else None
