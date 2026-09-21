"""In-process job runner: one asyncio task per job, a swappable concurrency semaphore.

A session is never held across an ``await``: every state change opens its own short
session, and the blocking tail (sidecars, thumbnails, row writes) runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from runware import RunwareError

from .. import db
from ..config import Paths
from ..models import Job, JobStatus, utcnow
from ..runware import download
from ..runware import runner as policy
from ..runware.download import DownloadError
from ..runware.errors import classify
from ..runware.tasks import build_image_task
from ..schemas.image import ImageRequest
from . import assets, catalog, costs, projects
from . import outputs as outputs_svc
from . import settings as settings_svc

log = logging.getLogger(__name__)
PROGRESS_WRITE_S = 2.0
STOP_TIMEOUT_S = 10.0
DOWNLOAD_PROGRESS = 92


def estimate_progress(elapsed_ms: float, expected_ms: int) -> int:
    """A smooth, never-finishing curve: honest about not knowing, useful as a bar."""
    return min(90, int(100 * (1 - math.exp(-elapsed_ms / max(expected_ms, 1000)))))


@dataclass
class _Plan:
    job_id: str
    project_id: int
    slug: str
    model_air: str
    req: ImageRequest
    negative: str
    family: str
    timeout_s: float
    expected_ms: int
    outputs_dir: str
    asset_ids: list[int]


@dataclass
class _Live:
    stage: str = "submitting"
    progress: int = 0
    real: bool = False  # True once RunWare has reported an actual percentage
    started_at: object = None
    expected_ms: int = 20000
    t0: float = field(default_factory=time.monotonic)


class JobRunner:
    def __init__(
        self,
        session_factory,
        paths: Paths,
        *,
        client_factory,
        api_key_getter: Callable[[], str],
        transport_getter: Callable[[], str],
        concurrency: int = 3,
        download_transport=None,
        catalog_family: Callable = catalog.family,
    ):
        self.session_factory = session_factory
        self.paths = paths
        self.client_factory = client_factory
        self.api_key_getter = api_key_getter
        self.transport_getter = transport_getter
        self.download_transport = download_transport
        self.catalog_family = catalog_family
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._sem = asyncio.Semaphore(max(1, int(concurrency)))
        self._concurrency = max(1, int(concurrency))
        self._tasks: set[asyncio.Task] = set()
        self._events: dict[str, asyncio.Event] = {}
        self._live: dict[str, _Live] = {}
        self._cancelled: set[str] = set()
        self._last_write: dict[str, float] = {}
        self._dispatcher: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- lifecycle -------------------------------------------------------
    async def start(self, requeue: list[str] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        if self._dispatcher is None or self._dispatcher.done():
            self._dispatcher = asyncio.create_task(self._dispatch())
        for job_id in requeue or []:
            self.submit(job_id)

    async def stop(self) -> None:
        for ev in list(self._events.values()):
            ev.set()
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
            self._dispatcher = None
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            done, still = await asyncio.wait(pending, timeout=STOP_TIMEOUT_S)
            for t in still:
                t.cancel()
        self._loop = None

    def set_concurrency(self, n: int) -> None:
        """Swap the semaphore; in-flight jobs release the one they took."""
        self._concurrency = max(1, int(n))
        self._sem = asyncio.Semaphore(self._concurrency)

    # ---- queue -----------------------------------------------------------
    def _on_loop(self, fn: Callable, *args) -> None:
        """Sync routes run in a threadpool: asyncio primitives must be touched on the loop."""
        loop = self._loop
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if loop is not None and current is not loop:
            loop.call_soon_threadsafe(fn, *args)
        else:
            fn(*args)

    def submit(self, job_id: str) -> None:
        self._cancelled.discard(job_id)
        self._on_loop(self._queue.put_nowait, job_id)

    def cancel(self, job_id: str) -> bool:
        # flag first, then look: _execute re-reads _cancelled after registering its event,
        # so a cancel that arrives in between is never lost.
        self._cancelled.add(job_id)
        ev = self._events.get(job_id)
        if ev is not None:  # running: ask the client to abort, the pipeline records it
            self._on_loop(ev.set)
            with db.session_scope(self.session_factory) as s:
                job = s.get(Job, job_id)
                if job is None:
                    return False
                job.cancel_requested = True
                job.status_text = "cancelling"
            return True
        with db.session_scope(self.session_factory) as s:  # queued: cancel it outright
            job = s.get(Job, job_id)
            if job is None or job.status not in (JobStatus.queued.value, JobStatus.running.value):
                return False
            job.status = JobStatus.cancelled.value
            job.cancel_requested = True
            job.status_text = "cancelled"
            job.finished_at = utcnow()
        return True

    def active_ids(self) -> list[str]:
        return list(self._events)

    def snapshot(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for job_id, live in list(self._live.items()):
            elapsed_ms = (time.monotonic() - live.t0) * 1000
            out[job_id] = {
                "progress": max(live.progress, estimate_progress(elapsed_ms, live.expected_ms)),
                "stage": live.stage,
                "real": live.real,
                "started_at": live.started_at,
            }
        return out

    async def wait_idle(self, timeout: float = 10) -> None:
        """Test helper: the queue is drained *and* every job task has finished."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("jobs still in flight")
            await asyncio.wait_for(self._queue.join(), remaining)
            pending = [t for t in self._tasks if not t.done()]
            if not pending:
                if self._queue.empty():
                    return
                continue
            await asyncio.wait(pending, timeout=max(0.0, deadline - loop.time()))

    # ---- dispatch --------------------------------------------------------
    async def _dispatch(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                task = asyncio.create_task(self._run_job(job_id))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            finally:
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        sem = self._sem  # release the one we took, even if set_concurrency swapped it
        await sem.acquire()
        try:
            await self._execute(job_id)
        finally:
            sem.release()

    # ---- pipeline --------------------------------------------------------
    def _begin(self, job_id: str) -> _Plan | None:
        with db.session_scope(self.session_factory) as s:
            job = s.get(Job, job_id)
            if job is None or job.status != JobStatus.queued.value or job_id in self._cancelled:
                return None
            job.status = JobStatus.running.value
            job.started_at = utcnow()
            job.attempts += 1
            job.progress = 0
            job.status_text = "submitting"
            job.error_code = None
            job.error_message = None
            data = dict(job.request_json or {})
            negative = str(data.pop("negative", "") or "")
            req = ImageRequest(**data)
            model = catalog.get_by_air(s, job.model_air)
            project = projects.get(s, job.project_id)
            asset_ids: list[int] = []
            if req.seed_image_asset_id is not None:
                asset_ids.append(req.seed_image_asset_id)
            for rid in req.reference_asset_ids:
                if rid not in asset_ids:
                    asset_ids.append(rid)
            return _Plan(
                job_id=job_id,
                project_id=job.project_id,
                slug=project.slug if project else "default",
                model_air=job.model_air,
                req=req,
                negative=negative,
                family=self.catalog_family(model) if model is not None else "diffusion",
                timeout_s=float(settings_svc.get(s, "runware.timeout_s")),
                expected_ms=int(job.expected_ms or costs.DEFAULT_EXPECTED_MS),
                outputs_dir=projects.root_override(s),
                asset_ids=asset_ids,
            )

    async def _execute(self, job_id: str) -> None:
        try:
            # inside the try: a row whose request_json no longer validates must end up
            # failed with a message, not stuck in `running` behind a swallowed traceback.
            plan = self._begin(job_id)
            if plan is None:
                return
            ev = asyncio.Event()
            self._events[job_id] = ev
            self._live[job_id] = _Live(started_at=utcnow(), expected_ms=plan.expected_ms)
            if job_id in self._cancelled:
                ev.set()
            api_key = self.api_key_getter() or ""
            transport = self.transport_getter() or "rest"
            async with self.client_factory(api_key, transport) as client:
                media = await assets.media_map(
                    client, self.session_factory, self.paths, plan.asset_ids
                )
                task = build_image_task(plan.req, job_id, media, plan.family, plan.negative)
                self._save_task(job_id, task, [])
                # spec stage sequence: queued -> submitting -> rendering -> downloading -> done
                self._stage(job_id, "rendering")
                result = await policy.run_with_policy(
                    client,
                    task,
                    timeout_s=plan.timeout_s,
                    cancel_event=ev,
                    on_progress=lambda p: self._on_progress(job_id, p),
                    on_attempt=lambda t, d: self._save_task(job_id, t, d),
                )
            if ev.is_set():
                raise RunwareError("aborted", "Request aborted")
            self._stage(job_id, "downloading", DOWNLOAD_PROGRESS)
            dest = projects.dir_for(self.paths, plan.slug, plan.outputs_dir)
            saved = await download.download_items(
                result.items, dest, plan.req.output_format, transport=self.download_transport
            )
            self._stage(job_id, "saving", DOWNLOAD_PROGRESS)
            cost = await asyncio.to_thread(self._persist, plan, result, saved)
            self._succeed(job_id, cost)
        except assets.MediaUploadError as e:
            err = classify(e.cause)
            self._fail(
                job_id, "upload", f"Could not upload {e.original_name} to RunWare: {err.message}"
            )
        except RunwareError as e:
            err = classify(e)
            self._fail(job_id, err.code, err.message, cancelled=err.code == "aborted")
        except DownloadError as e:
            self._fail(job_id, "download", str(e))
        except Exception as e:  # noqa: BLE001 - one bad job must never take the runner down
            log.error("job %s failed:\n%s", job_id, traceback.format_exc())
            self._fail(job_id, "unknown", classify(e).message)
        finally:
            self._events.pop(job_id, None)
            self._live.pop(job_id, None)
            self._last_write.pop(job_id, None)
            self._cancelled.discard(job_id)

    def _persist(self, plan: _Plan, result, saved: list) -> float:
        """Blocking tail, run in a worker thread: sidecars and thumbnails first, then one
        short transaction. The outputs root comes from the plan, so a settings change
        mid-job cannot make the relative paths unresolvable."""
        req = plan.req
        params = {
            "width": req.width,
            "height": req.height,
            "steps": req.steps,
            "cfg": req.cfg_scale,
            "seed": req.seed,
            "strength": req.strength,
            "output_format": req.output_format,
            "number_results": req.number_results,
        }
        extra = {"task_sent": result.task_sent, "dropped_params": result.dropped}
        meta = outputs_svc.OutputMeta(
            job_id=plan.job_id,
            project_id=plan.project_id,
            project_slug=plan.slug,
            kind="image",
            model_air=plan.model_air,
            prompt_text=str(result.task_sent.get("positivePrompt") or ""),
            negative_prompt=plan.negative,
        )
        root = projects.outputs_root(self.paths, plan.outputs_dir)
        prepared = outputs_svc.prepare(self.paths, meta, saved, params, extra, root=root)
        total = 0.0
        with db.session_scope(self.session_factory) as s:
            job = s.get(Job, plan.job_id)
            outputs_svc.insert(s, meta, prepared)
            for f in saved:
                c = float(f.item.cost or 0.0)
                total += c
                costs.record_usage(
                    s, job=job, task_type="imageInference", cost=c, model_air=plan.model_air
                )
            if result.duration_ms is not None:  # retries/backoff would poison the average
                costs.observe_latency(s, plan.model_air, result.duration_ms)
        return total

    # ---- state writes ----------------------------------------------------
    def _save_task(self, job_id: str, task: dict, dropped: list[dict]) -> None:
        with db.session_scope(self.session_factory) as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.task_json = task
            job.runware_task_uuid = task.get("taskUUID")
            job.dropped_params_json = list(dropped)

    def _on_progress(self, job_id: str, pct: int) -> None:
        live = self._live.get(job_id)
        if live is None:
            return
        live.progress = max(live.progress, min(99, int(pct)))
        live.real = True  # the bar is now measured, not modelled: the card drops its ETA
        live.stage = "rendering"
        self._write_live(job_id, force=False)

    def _stage(self, job_id: str, stage: str, progress: int | None = None) -> None:
        live = self._live.get(job_id)
        if live is None:
            return
        live.stage = stage
        if progress is not None:
            live.progress = max(live.progress, progress)
        self._write_live(job_id, force=True)

    def _write_live(self, job_id: str, force: bool) -> None:
        live = self._live.get(job_id)
        if live is None:
            return
        now = time.monotonic()
        if not force and now - self._last_write.get(job_id, 0.0) < PROGRESS_WRITE_S:
            return
        self._last_write[job_id] = now
        with db.session_scope(self.session_factory) as s:
            job = s.get(Job, job_id)
            if job is None or job.status != JobStatus.running.value:
                return
            job.progress = live.progress
            job.status_text = live.stage

    def _succeed(self, job_id: str, cost: float) -> None:
        with db.session_scope(self.session_factory) as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            if job.status == JobStatus.cancelled.value:
                return  # a cancel that landed while we were finishing wins
            job.status = JobStatus.succeeded.value
            job.progress = 100
            job.status_text = "done"
            job.cost = cost
            job.finished_at = utcnow()

    def _fail(self, job_id: str, code: str, message: str, cancelled: bool = False) -> None:
        with db.session_scope(self.session_factory) as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            if job.status == JobStatus.cancelled.value:
                return
            job.status = JobStatus.cancelled.value if cancelled else JobStatus.failed.value
            job.status_text = "cancelled" if cancelled else "failed"
            job.error_code = code
            job.error_message = message
            job.finished_at = utcnow()
