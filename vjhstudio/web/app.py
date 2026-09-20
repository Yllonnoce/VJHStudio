from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .. import boot as _boot
from .. import config, db, secrets
from ..models import Job, JobStatus
from ..runware.catalog_api import ContentAPI
from ..runware.client import open_client
from ..services import catalog, migrate
from ..services import jobs as jobs_svc
from ..services import settings as settings_svc
from .csrf import CrossSiteBlockMiddleware
from .deps import STATIC_DIR
from .routes import catalog as catalog_routes
from .routes import files as files_routes
from .routes import generate as generate_routes
from .routes import jobs as jobs_routes
from .routes import pages, system
from .routes import settings as settings_routes

log = logging.getLogger(__name__)
_ACTIVE_STATUSES = (JobStatus.queued.value, JobStatus.running.value)


def create_app(
    paths: config.Paths,
    client_factory=open_client,
    env: Mapping[str, str] | None = None,
    port: int = config.DEFAULT_PORT,
    boot_info: _boot.BootInfo | None = None,
    auto_refresh: bool = True,
    download_transport=None,
) -> FastAPI:
    env = os.environ if env is None else env

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.boot = boot_info or _boot.boot(paths)
        except migrate.MigrationFailed as e:
            log.error(str(e))
            raise

        async def _maybe_refresh():
            try:
                with db.session_scope(app.state.boot.session_factory) as s:
                    due = catalog.needs_refresh(s)
                if due and env.get("VJHSTUDIO_OFFLINE") != "1":
                    await asyncio.sleep(3)
                    res = await catalog.refresh_from_content_api(
                        app.state.boot.session_factory, ContentAPI()
                    )
                    log.info(
                        "catalog refreshed: %d models, %d priced, %d errors",
                        res.models,
                        res.priced,
                        len(res.errors),
                    )
            except Exception as e:  # noqa: BLE001 - a background refresh must never crash the app
                log.warning("catalog refresh skipped: %s", e)

        app.state.runner = jobs_svc.JobRunner(
            app.state.boot.session_factory,
            paths,
            client_factory=app.state.client_factory,
            api_key_getter=app.state.api_key,
            transport_getter=lambda: app.state.setting("runware.transport"),
            concurrency=app.state.setting("jobs.concurrency"),
            download_transport=app.state.download_transport,
        )
        await app.state.runner.start(requeue=app.state.boot.requeued_jobs)
        task = asyncio.create_task(_maybe_refresh()) if app.state.auto_refresh else None
        yield
        if task:
            task.cancel()
        await app.state.runner.stop()
        app.state.boot.engine.dispose()

    app = FastAPI(title="VJHStudio", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.paths = paths
    app.state.client_factory = client_factory
    app.state.env = env
    app.state.port = port
    app.state.auto_refresh = auto_refresh
    app.state.download_transport = download_transport
    app.state.runner = None
    app.state.api_key = lambda: secrets.effective_api_key(paths, env)
    app.state.key_source = lambda: secrets.key_source(paths, env)

    def theme() -> str:
        with db.session_scope(app.state.boot.session_factory) as s:
            return settings_svc.get(s, "ui.theme", env)

    def setting(key: str):
        with db.session_scope(app.state.boot.session_factory) as s:
            return settings_svc.get(s, key, env)

    def active_jobs() -> int:
        """Queued + running, for the header badge. One indexed COUNT per page render."""
        with db.session_scope(app.state.boot.session_factory) as s:
            q = select(func.count(Job.id)).where(Job.status.in_(_ACTIVE_STATUSES))
            return int(s.execute(q).scalar() or 0)

    app.state.theme = theme
    app.state.setting = setting
    app.state.active_jobs = active_jobs
    app.add_middleware(CrossSiteBlockMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(system.router)
    app.include_router(pages.router)
    app.include_router(settings_routes.router)
    app.include_router(catalog_routes.router)
    app.include_router(generate_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(files_routes.router)
    return app
