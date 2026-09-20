"""Serving generated files off disk, with a containment guard on every path.

The filename comes straight from a URL, so it is never joined to the outputs root
without resolving the result and checking it is still *inside* that root: ``..``
segments, absolute paths and symlinks all collapse to a 404.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ... import db
from ...services import outputs as outputs_svc
from ...services import projects

router = APIRouter()


def _serve(path: Path | None, download: bool) -> FileResponse:
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if download:
        return FileResponse(path, filename=path.name)
    return FileResponse(path, headers={"Content-Disposition": "inline"})


@router.get("/files/outputs/{slug}/{filename}")
def output_file(request: Request, slug: str, filename: str, download: int = 0):
    with db.session_scope(request.app.state.boot.session_factory) as s:
        root = projects.root_for(s, request.app.state.paths)
    return _serve(outputs_svc.contained(root, f"{slug}/{filename}"), bool(download))


@router.get("/files/thumbs/{name}")
def thumb_file(request: Request, name: str, download: int = 0):
    thumbs = request.app.state.paths.thumbs
    return _serve(outputs_svc.contained(thumbs, name), bool(download))
