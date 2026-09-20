from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Request
from ...services import restart as restart_svc

router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    b = request.app.state.boot
    return {"ok": True, "app": "RunwareStudio", "version": b.version,
            "commit": b.commit.sha if b.commit else "", "schema": b.schema_revision,
            "boot_id": b.boot_id, "started_at": b.started_at.isoformat(), "pid": os.getpid(),
            "port": request.app.state.port}


def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "testclient", ""):
        raise HTTPException(403, "local only")


@router.post("/api/restart", status_code=202)
async def api_restart(request: Request):
    _local_only(request)
    return {"restarting": True, "strategy": restart_svc.request_restart()}


@router.post("/api/shutdown", status_code=202)
async def api_shutdown(request: Request):
    _local_only(request)
    restart_svc.request_shutdown()
    return {"stopping": True}
