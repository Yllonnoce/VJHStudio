from __future__ import annotations
import os
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    b = request.app.state.boot
    return {"ok": True, "app": "RunwareStudio", "version": b.version,
            "commit": b.commit.sha if b.commit else "", "schema": b.schema_revision,
            "boot_id": b.boot_id, "started_at": b.started_at.isoformat(), "pid": os.getpid(),
            "port": request.app.state.port}
