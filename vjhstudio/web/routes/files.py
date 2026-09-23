"""Serving generated files off disk, with a containment guard on every path.

The filename comes straight from a URL, so it is never joined to the outputs root
without resolving the result and checking it is still *inside* that root: ``..``
segments, absolute paths and symlinks all collapse to a 404.

These routes serve any peer, loopback or not: the README's "on your phone or
tablet" workflow is a LAN browser reading exactly these URLs. That is also what
lets an MCP agent on another machine download what it made, so the bearer token
(`mcp.http.token_ok`) is accepted here but is not required -- locking these
routes down would take the phone with it, and is a decision of its own.
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


@router.get("/files/uploads/{filename}")
def upload_file(request: Request, filename: str, download: int = 0):
    uploads = request.app.state.paths.uploads
    return _serve(outputs_svc.contained(uploads, filename), bool(download))


@router.get("/files/asset-thumbs/{name}")
def asset_thumb_file(request: Request, name: str, download: int = 0):
    """Asset thumbnails live in the same ``paths.thumbs`` directory as output
    thumbnails (named ``asset-<sha12>.jpg`` so the two never collide); the separate
    URL prefix just keeps the asset library's own namespace."""
    thumbs = request.app.state.paths.thumbs
    return _serve(outputs_svc.contained(thumbs, name), bool(download))
