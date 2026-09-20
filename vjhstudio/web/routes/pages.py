from __future__ import annotations
from fastapi import APIRouter, Request
from .. import deps

router = APIRouter()


@router.get("/")
def index(request: Request):
    return deps.render(request, "pages/index.html")
