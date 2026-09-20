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
