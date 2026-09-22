"""Per-model constraints: what a model accepts (sizes, durations, inputs), merged from the
docs page, RunWare's validation errors and real-job corrections. See the spec section
"Model constraints". Everything here is pure dict logic except ``store``."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime

from vjhstudio import db
from vjhstudio.models import utcnow
from vjhstudio.models.catalog import CatalogModel
from vjhstudio.runware import docs_pages, probe
from vjhstudio.runware.sizes import (  # noqa: F401  (re-exported for callers)
    nearest_size_in,
    snap_to_rule,
)
from vjhstudio.services import account

log = logging.getLogger(__name__)

Size = tuple[int, int]
PARAM_KEYS = ("duration", "fps", "steps", "strength", "CFGScale")
_ATTR_KEYS = ("type", "min", "max", "step", "default", "values")


def _dims(c: dict | None) -> dict:
    d = (c or {}).get("dims") or {}
    return d if isinstance(d, dict) else {}


def _label(w: int, h: int, labels: dict) -> str:
    name = labels.get(f"{w}x{h}")
    return f"{name} — {w}×{h}" if name else f"{w}×{h}"


def size_options(c: dict | None, kind: str, fallback: list[tuple[int, int, str]]) -> list[dict]:
    d = _dims(c)
    if d.get("mode") == "list":
        labels = d.get("labels") or {}
        return [
            {"w": int(w), "h": int(h), "label": _label(int(w), int(h), labels)}
            for w, h in d.get("list") or []
        ]
    if d.get("mode") == "rule":
        step = int(d.get("step") or 1)
        out, seen = [], set()
        for w, h, name in fallback:
            sw, sh = snap_to_rule(w, h, d)
            # A preset that needs more than a quarter-step of correction on either axis
            # isn't a good fit for this model's grid; the free width/height inputs (also
            # shown for rule mode) cover it instead of offering a misleading preset.
            if abs(w - sw) * 4 > step or abs(h - sh) * 4 > step:
                continue
            if (sw, sh) in seen:
                continue
            seen.add((sw, sh))
            out.append({"w": sw, "h": sh, "label": f"{name} — {sw}×{sh}"})
        return out
    return [{"w": w, "h": h, "label": f"{name} — {w}×{h}"} for w, h, name in fallback]


def nearest_size(c: dict | None, w: int, h: int) -> Size:
    return nearest_size_in(_dims(c), w, h)


def duration_spec(c: dict | None) -> dict:
    spec = (c or {}).get("duration") or {}
    if not isinstance(spec, dict):
        return {}
    if spec.get("values"):
        return {k: spec[k] for k in ("values", "default") if k in spec}
    return {k: spec[k] for k in ("min", "max", "step", "default") if k in spec}


def requires_input_video(c: dict | None) -> bool:
    video = ((c or {}).get("inputs") or {}).get("video") or {}
    return bool(isinstance(video, dict) and video.get("required"))


def can_start_from_text(capabilities: list[str]) -> bool:
    return "io:text-to-video" in (capabilities or []) or "io:text-to-image" in (capabilities or [])


def needs_first_frame(capabilities: list[str], c: dict | None) -> bool:
    caps = capabilities or []
    return "io:image-to-video" in caps and "io:text-to-video" not in caps


def is_generate_capable(kind: str, capabilities: list[str], c: dict | None) -> bool:
    if requires_input_video(c):
        return False
    if kind != "video":
        return True
    caps = capabilities or []
    if not caps:
        return True  # a search-added row with no tags: let the runner find out
    return "io:text-to-video" in caps or "io:image-to-video" in caps


def _docs_dims(docs: dict) -> dict | None:
    if docs.get("dims"):
        return {
            "mode": "list",
            "list": [[int(w), int(h)] for w, h in docs["dims"]],
            "labels": dict(docs.get("dim_labels") or {}),
        }
    width = (docs.get("params") or {}).get("width") or {}
    if width.get("min") and width.get("max"):
        return {
            "mode": "rule",
            "min": int(width["min"]),
            "max": int(width["max"]),
            "step": int(width.get("step") or 1),
        }
    return None


def merge_sources(existing: dict | None, *, docs: dict | None, api: dict | None, now: str) -> dict:
    out: dict = dict(existing or {})
    sources = dict(out.get("sources") or {"docs": None, "api": None, "observed": None})
    if docs:
        sources["docs"] = now
        for key in PARAM_KEYS:
            attrs = (docs.get("params") or {}).get(key)
            if attrs:
                out[key] = {k: attrs[k] for k in _ATTR_KEYS if k in attrs}
        if docs.get("inputs"):
            out["inputs"] = {**(out.get("inputs") or {}), **docs["inputs"]}
        dd = _docs_dims(docs)
        if dd:
            out["dims"] = dd
    if api:
        sources["api"] = now
        if api.get("params"):
            out["params"] = list(api["params"])
        if api.get("dims") and api["dims"].get("mode") != "unknown":
            dims = dict(api["dims"])
            if dims.get("mode") == "list":
                dims.setdefault("labels", (out.get("dims") or {}).get("labels") or {})
            out["dims"] = dims
        for path in api.get("missing") or []:
            if path.startswith("inputs."):
                out.setdefault("inputs", {})[path.split(".", 1)[1]] = {"required": True}
    out.setdefault("dims", {"mode": "unknown"})
    out["sources"] = sources
    return out


def observe_dims(existing: dict | None, dims: dict, now: str) -> dict:
    out = dict(existing or {})
    out["dims"] = dict(dims)
    sources = dict(out.get("sources") or {"docs": None, "api": None, "observed": None})
    sources["observed"] = now
    out["sources"] = sources
    return out


def store(session, model: CatalogModel, constraints: dict) -> None:
    model.constraints_json = constraints
    model.constraints_updated_at = utcnow()
    session.flush()


# --- the harvest -----------------------------------------------------------
#
# Free by construction: the docs page is a plain GET, and the two API probes are
# built so RunWare must reject them before it bills (see the spec's probe safety
# rules and runware/probe.py). The balance is read before and after anyway; if it
# moved at all, the run stops loudly and sends nothing more.

NO_KEY_MESSAGE = "Add your RunWare API key in Settings to run the API probes; docs pages only."
BALANCE_EPSILON = 1e-6


@dataclass
class HarvestState:
    """The live state of one harvest run. Written by the background task, polled by
    the Models page; the lock keeps a poll from reading a half-written row list."""

    running: bool = False
    total: int = 0
    done: int = 0
    ok: int = 0
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rows: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def begin(self) -> bool:
        """Claim the state for a new run. False when one is already in flight."""
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.total = self.done = self.ok = 0
            self.message = ""
            self.rows = []
            self.started_at = utcnow()
            self.finished_at = None
            return True

    def finish(self) -> None:
        with self._lock:
            self.running = False
            self.finished_at = utcnow()

    def add_row(self, row: dict) -> None:
        """Record one finished model. ``ok`` counts the rows that actually *learned*
        something — a model with no docs page and no probes is done, not harvested."""
        with self._lock:
            self.rows.append(row)
            self.done += 1
            if row["docs"] == "ok" or row["api"] == "ok":
                self.ok += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "total": self.total,
                "done": self.done,
                "ok": self.ok,
                "message": self.message,
                "started_at": self.started_at.isoformat() if self.started_at else "",
                "finished_at": self.finished_at.isoformat() if self.finished_at else "",
                "rows": list(self.rows),
            }


STATE = HarvestState()


def _catalog_rows(session_factory, kinds, airs) -> list[dict]:
    # Local import: services/catalog imports this module, so importing it at module
    # level here would be a cycle.
    from vjhstudio.services import catalog

    wanted = set(airs) if airs else None
    rows: list[dict] = []
    with db.session_scope(session_factory) as s:
        for kind in kinds:
            for m in catalog.list_models(s, kind):
                if wanted is None or m.air in wanted:
                    rows.append(
                        {"id": m.id, "air": m.air, "name": m.name, "kind": m.kind, "slug": m.slug}
                    )
    return rows


async def _fetch_docs(rows: list[dict], transport, concurrency: int) -> dict[str, tuple]:
    """air -> (status, parsed | None) where status is "ok", "missing" or "error: …"."""
    sem = asyncio.Semaphore(concurrency)

    async def one(row: dict) -> tuple[str, tuple]:
        if not row["slug"]:
            return row["air"], ("missing", None)
        try:
            async with sem:
                html = await docs_pages.fetch_docs_html(row["slug"], transport=transport)
            if html is None:
                return row["air"], ("missing", None)
            return row["air"], ("ok", docs_pages.parse_docs(html))
        except Exception as e:  # noqa: BLE001 - one bad page must not stop the harvest
            log.warning("docs page failed for %s: %s", row["air"], e)
            return row["air"], (f"error: {e}", None)

    return dict(await asyncio.gather(*(one(r) for r in rows)))


def _merge_and_store(session_factory, row: dict, docs: dict | None, api: dict | None) -> str:
    now = utcnow().isoformat()
    with db.session_scope(session_factory) as s:
        m = s.get(CatalogModel, row["id"])
        if m is None:  # deleted while the harvest ran
            return "unknown"
        merged = merge_sources(m.constraints_json, docs=docs, api=api, now=now)
        store(s, m, merged)
    return (merged.get("dims") or {}).get("mode") or "unknown"


def _record(state: HarvestState, session_factory, row: dict, docs_result, api_status, api_data):
    docs_status, parsed = docs_result or ("skipped", None)
    try:
        mode = _merge_and_store(session_factory, row, parsed, api_data)
    except Exception as e:  # noqa: BLE001 - a bad row must not stop the harvest
        log.warning("storing constraints failed for %s: %s", row["air"], e)
        docs_status, mode = f"error: not stored ({e})", "unknown"
    state.add_row(
        {
            "air": row["air"],
            "name": row["name"],
            "docs": docs_status,
            "api": api_status,
            "dims_mode": mode,
        }
    )


async def _balance(client) -> float:
    rows = await client.account_management({"operation": "getDetails"})
    amount, _currency, _free = account.parse_balance(rows[0] if rows else {})
    return amount


def _money(amount: float) -> str:
    """Dollars with at least two decimals, and more when the change is smaller than a
    cent — "before $10.00, after $10.00" would be a useless thing to report."""
    whole, _, frac = f"{amount:,.6f}".rstrip("0").partition(".")
    return f"${whole}.{frac.ljust(2, '0')}"


async def _probe_one(state: HarvestState, session_factory, row: dict, docs_result, client) -> None:
    """Probe one model and record it. Lets ``ProbeBilledError`` through — that one stops
    the whole harvest — while every other RunWare failure stays in this row's ``api``
    column so the next model still gets its turn."""
    try:
        res = await probe.probe_model(client, row["air"], row["kind"])
    except probe.ProbeBilledError:
        raise
    except Exception as e:  # noqa: BLE001 - one model's failure is per-row
        log.warning("probe failed for %s: %s", row["air"], e)
        _record(state, session_factory, row, docs_result, f"error: {e}", None)
        return
    if res.params:
        api: dict | None = {"params": res.params, "dims": res.dims, "missing": res.missing}
        status = "ok"
    else:
        api, status = None, "error: " + ("; ".join(res.errors) or "no parameter list returned")
    _record(state, session_factory, row, docs_result, status, api)


async def _probe_all(
    state: HarvestState,
    session_factory,
    rows: list[dict],
    docs_by_air: dict,
    *,
    client,
    concurrency: int,
) -> None:
    """The API half: one bounded pool, stopped for good the moment a probe comes back
    accepted or a row falls over. Two independent brakes, because a request that goes
    out after the closing balance read is a request nobody is watching:

    * a ``stopped`` flag, checked by every task *before* it sends anything, which is
      what actually keeps the queued probes in;
    * cancelling the pending tasks on the way out, for a task parked anywhere else.
    """
    sem = asyncio.Semaphore(concurrency)
    stopped: list[str] = []

    async def one(row: dict) -> None:
        async with sem:
            if stopped:  # the run is over; send nothing, record nothing
                return
            try:
                await _probe_one(state, session_factory, row, docs_by_air.get(row["air"]), client)
            except probe.ProbeBilledError as e:
                stopped.append(f"STOPPED: {e}. Nothing more was sent. Please report this.")
            except BaseException:
                # Not this row's problem but the run's: hold the rest of the pool
                # before the failure travels up.
                stopped.append("")
                raise

    tasks = [asyncio.create_task(one(r)) for r in rows]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        # return_exceptions already absorbs the children's CancelledError; no suppress
        # here, so a shutdown cancel that lands on this await still stops the harvest.
        await asyncio.gather(*tasks, return_exceptions=True)
    if stopped and stopped[0]:
        state.message = stopped[0]


async def _harvest(
    state: HarvestState,
    session_factory,
    rows: list[dict],
    *,
    client_factory,
    api_key: str,
    transport: str,
    api: bool,
    docs_by_air: dict,
    api_concurrency: int,
) -> None:
    def docs_only(note: str = "") -> None:
        for row in rows:
            _record(state, session_factory, row, docs_by_air.get(row["air"]), "skipped", None)
        if note:
            state.message = note

    if not api:
        docs_only()
        return
    opened = False
    try:
        async with client_factory(api_key, transport) as client:
            opened = True
            try:
                before = await _balance(client)
            except Exception as e:  # noqa: BLE001
                docs_only(
                    f"STOPPED: the account balance could not be read before the harvest ({e}). "
                    "No probes were sent."
                )
                return
            try:
                await _probe_all(
                    state,
                    session_factory,
                    rows,
                    docs_by_air,
                    client=client,
                    concurrency=api_concurrency,
                )
            finally:
                # Safety rule 4, and it runs even if the pool itself blew up: the
                # balance must be checked whenever a probe has been sent at all.
                await _check_balance(state, client, before)
    except Exception as e:  # noqa: BLE001
        if opened:  # the client was fine; this is the run itself failing
            raise
        # No client, no probes — but the docs half already ran and is free to keep.
        log.warning("opening the RunWare client failed: %s", e)
        docs_only(f"STOPPED: could not reach RunWare ({e}). No probes were sent.")


async def _check_balance(state: HarvestState, client, before: float) -> None:
    """Compare the balance with the reading taken before the probes. A change (or a
    reading we cannot take) is the loudest thing the harvest can say."""
    stopped = state.message
    try:
        after = await _balance(client)
    except Exception as e:  # noqa: BLE001
        state.message = (
            f"STOPPED: the account balance could not be read after the harvest ({e}). "
            "Check your RunWare balance."
        )
        return
    if abs(after - before) > BALANCE_EPSILON:
        state.message = (
            "STOPPED: the balance changed during the harvest "
            f"(before {_money(before)}, after {_money(after)}). Nothing more was sent. "
            "Please report this."
        )
    else:
        state.message = stopped


def _summary(state: HarvestState) -> str:
    """The line the Models page shows when a run ends without being stopped."""
    rows = state.snapshot()["rows"]
    sizes = sum(1 for r in rows if r["dims_mode"] in ("list", "rule"))
    errors = sum(
        1 for r in rows if str(r["docs"]).startswith("error") or str(r["api"]).startswith("error")
    )
    text = f"Harvested {state.ok} of {state.total} models ({sizes} with sizes known)."
    return text + (f" {errors} had errors." if errors else "")


async def harvest(
    session_factory,
    *,
    client_factory,
    api_key: str,
    kinds: tuple[str, ...] = ("video", "image"),
    airs: list[str] | None = None,
    docs: bool = True,
    api: bool = True,
    transport: str = "rest",
    docs_transport=None,
    state: HarvestState | None = None,
    claimed: bool = False,
    docs_concurrency: int = 5,
    # Sequential: an accepted probe must stop the run before another request is in flight.
    api_concurrency: int = 1,
) -> HarvestState:
    """Learn what every catalog model accepts, for free.

    A crash inside the run is reported through ``state.message`` rather than raised,
    so the poll on the Models page always ends. The one exception is a state that is
    already in flight: joining it would let two runs write the same rows and close
    each other's log, so that raises instead. ``claimed`` is for ``start_harvest``,
    which has already taken the claim with the same ``begin()``."""
    state = STATE if state is None else state
    if not claimed and not state.begin():
        raise RuntimeError("harvest already running")
    try:
        rows = _catalog_rows(session_factory, kinds, airs)
        state.total = len(rows)
        if api and not api_key:
            api, state.message = False, NO_KEY_MESSAGE
        docs_by_air = await _fetch_docs(rows, docs_transport, docs_concurrency) if docs else {}
        await _harvest(
            state,
            session_factory,
            rows,
            client_factory=client_factory,
            api_key=api_key,
            transport=transport,
            api=api,
            docs_by_air=docs_by_air,
            api_concurrency=api_concurrency,
        )
        if not state.message:
            state.message = _summary(state)
    except Exception as e:  # noqa: BLE001 - the state must always close
        log.exception("harvest crashed")
        state.message = f"Harvest failed: {e}"
    finally:
        # Cancellation at shutdown must close the state too, or the Models page
        # would poll a run that can never finish.
        state.finish()
    return state


def start_harvest(app_state, **kwargs) -> bool:
    """Run a harvest in the background. False when one is already running."""
    state = kwargs.pop("state", None) or STATE
    if not state.begin():
        return False
    session_factory = kwargs.pop("session_factory", None) or app_state.boot.session_factory
    kwargs.setdefault("client_factory", app_state.client_factory)
    try:
        app_state.harvest_task = asyncio.create_task(
            harvest(session_factory, state=state, claimed=True, **kwargs), name="vjh-harvest"
        )
    except BaseException:
        state.finish()  # the claim must not outlive a task that never started
        raise
    return True
