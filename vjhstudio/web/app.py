from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .. import boot as _boot
from .. import config, db, secrets
from ..models import Job, JobStatus
from ..runware.catalog_api import ContentAPI
from ..runware.client import open_client
from ..services import account, catalog, gitinfo, migrate, update
from ..services import jobs as jobs_svc
from ..services import outputs as outputs_svc
from ..services import settings as settings_svc
from .csrf import CrossSiteBlockMiddleware
from .deps import STATIC_DIR
from .routes import assets as assets_routes
from .routes import catalog as catalog_routes
from .routes import files as files_routes
from .routes import gallery as gallery_routes
from .routes import generate as generate_routes
from .routes import jobs as jobs_routes
from .routes import pages, system
from .routes import projects as projects_routes
from .routes import prompts as prompts_routes
from .routes import settings as settings_routes

log = logging.getLogger(__name__)
_ACTIVE_STATUSES = (JobStatus.queued.value, JobStatus.running.value)
_FINISHED_STATUSES = (
    JobStatus.succeeded.value,
    JobStatus.failed.value,
    JobStatus.cancelled.value,
)
UPDATE_CHECK_FIRST = 60
UPDATE_CHECK_INTERVAL = 6 * 3600


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

        async def _backfill_posters():
            """Videos saved before this app could grab a frame still show the generic
            icon; fill them in once, in a worker thread, after the app is already up.

            Cancelling the task cannot interrupt the thread, so shutdown sets
            ``app.state.stop_posters`` and the worker returns between rows -- before it
            would open the session that a disposed engine could no longer serve."""
            try:
                made = await asyncio.to_thread(
                    outputs_svc.backfill_posters,
                    app.state.boot.session_factory,
                    paths,
                    should_stop=app.state.stop_posters.is_set,
                )
                if made:
                    log.info("video posters made: %d", made)
            except Exception as e:  # noqa: BLE001 - a backfill must never crash the app
                log.warning("video poster backfill skipped: %s", e)

        async def _update_watch():
            """Cache the "N commits behind" notice for the header badge. Manual
            updates only: this never pulls anything, it only counts commits."""
            await asyncio.sleep(UPDATE_CHECK_FIRST)
            while True:
                try:
                    await asyncio.to_thread(update.check_and_store, app.state.boot.session_factory)
                except Exception as e:  # noqa: BLE001 - a check must never crash the app
                    log.warning("update check skipped: %s", e)
                await asyncio.sleep(UPDATE_CHECK_INTERVAL)

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
        app.state.poster_task = (
            asyncio.create_task(_backfill_posters()) if app.state.auto_refresh else None
        )
        watch_updates = (
            app.state.auto_refresh
            and env.get("VJHSTUDIO_OFFLINE") != "1"
            and gitinfo.is_git_install()
        )
        app.state.update_task = asyncio.create_task(_update_watch()) if watch_updates else None
        yield
        # Ask the poster worker to stop *before* anything is awaited or disposed: the
        # thread it runs in only notices between rows.
        app.state.stop_posters.set()
        for background in (
            task,
            app.state.update_task,
            app.state.harvest_task,
            app.state.poster_task,
        ):
            if background:
                background.cancel()
                # Let the cancellation land before the engine goes: a check still in
                # to_thread would otherwise touch a disposed engine on its way out.
                # A thread mid `git fetch` cannot be interrupted, so wait briefly and
                # then move on rather than hold a restart for the fetch timeout.
                with suppress(asyncio.CancelledError, asyncio.TimeoutError, TimeoutError):
                    await asyncio.wait_for(background, timeout=5)
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
    app.state.update_task = None
    app.state.poster_task = None
    app.state.stop_posters = threading.Event()  # shutdown's only handle on that worker
    app.state.harvest_task = None  # set by services.constraints.start_harvest
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

    def render_globals() -> dict:
        """Everything base.html and the header badge need, from ONE session. deps.render
        sits on the 2 s poll path, where three separate session_scopes were pure waste."""
        active = select(func.count(Job.id)).where(Job.status.in_(_ACTIVE_STATUSES))
        unseen = select(func.count(Job.id)).where(
            Job.seen_at.is_(None), Job.status.in_(_FINISHED_STATUSES)
        )
        with db.session_scope(app.state.boot.session_factory) as s:
            return {
                "theme": settings_svc.get(s, "ui.theme", env),
                "notify_desktop": settings_svc.get(s, "ui.notify_desktop", env),
                "active_jobs": int(s.execute(active).scalar() or 0),
                "unseen_jobs": int(s.execute(unseen).scalar() or 0),
                "update_behind": update.behind_count(s),
                # the header chip on EVERY page; only Home and Settings used to pass it
                "balance": account.cached_balance(s),
            }

    app.state.theme = theme
    app.state.setting = setting
    app.state.active_jobs = active_jobs
    app.state.render_globals = render_globals
    app.add_middleware(CrossSiteBlockMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(system.router)
    app.include_router(pages.router)
    app.include_router(settings_routes.router)
    app.include_router(catalog_routes.router)
    app.include_router(generate_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(files_routes.router)
    app.include_router(gallery_routes.router)
    app.include_router(projects_routes.router)
    app.include_router(assets_routes.router)
    app.include_router(prompts_routes.router)
    return app
