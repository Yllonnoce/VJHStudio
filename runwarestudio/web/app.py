from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from typing import Mapping
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .. import boot as _boot, config, db, secrets
from ..runware.client import open_client
from ..services import migrate, settings as settings_svc
from .deps import STATIC_DIR
from .routes import pages, settings as settings_routes, system

log = logging.getLogger(__name__)


def create_app(paths: config.Paths, client_factory=open_client, env: Mapping[str, str] | None = None,
               port: int = config.DEFAULT_PORT) -> FastAPI:
    env = os.environ if env is None else env

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.boot = _boot.boot(paths)
        except migrate.MigrationFailed as e:
            log.error(str(e))
            raise
        yield
        app.state.boot.engine.dispose()

    app = FastAPI(title="RunwareStudio", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.paths = paths
    app.state.client_factory = client_factory
    app.state.env = env
    app.state.port = port
    app.state.api_key = lambda: secrets.effective_api_key(paths, env)
    app.state.key_source = lambda: secrets.key_source(paths, env)

    def theme() -> str:
        with db.session_scope(app.state.boot.session_factory) as s:
            return settings_svc.get(s, "ui.theme", env)

    def setting(key: str):
        with db.session_scope(app.state.boot.session_factory) as s:
            return settings_svc.get(s, key, env)

    app.state.theme = theme
    app.state.setting = setting
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(system.router)
    app.include_router(pages.router)
    app.include_router(settings_routes.router)
    return app
