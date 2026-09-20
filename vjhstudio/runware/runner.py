"""Retry + parameter-fallback policy around client.run()."""

from __future__ import annotations

import asyncio
import copy
import re
import uuid
from collections.abc import Awaitable, Callable

from runware import RunOptions, RunwareError

from .results import TaskResult, parse_items

MAX_ATTEMPTS = 5
PROTECTED = ("taskType", "taskUUID", "model", "positivePrompt")
_UNSUPPORTED = re.compile(r"unsupported use of '?([A-Za-z0-9_.]+)'? parameter", re.I)
_BACKOFF = {"rateLimit": [2, 5, 15], "connection": [1, 3, 8], "serverError": [5]}


def _has_path(task: dict, path: str) -> bool:
    cur: object = task
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _find_path(task: dict, name: str) -> str | None:
    if name in task:
        return name
    for parent in ("inputs", "providerSettings", "settings"):
        sub = task.get(parent)
        if isinstance(sub, dict):
            if name in sub:
                return f"{parent}.{name}"
            for k2, v2 in sub.items():
                if isinstance(v2, dict) and name in v2:
                    return f"{parent}.{k2}.{name}"
    return None


def rejected_field(err: BaseException, task: dict) -> str | None:
    cand = getattr(err, "parameter", None)
    if not cand:
        m = _UNSUPPORTED.search(getattr(err, "message", None) or str(err))
        cand = m.group(1) if m else None
    if not cand:
        return None
    path = cand if _has_path(task, cand) else _find_path(task, cand.split(".")[-1])
    if not path or path.split(".")[0] in PROTECTED or path in PROTECTED:
        return None
    return path


def _delete_path(task: dict, path: str) -> None:
    parts = path.split(".")
    cur = task
    for p in parts[:-1]:
        cur = cur[p]
    cur.pop(parts[-1], None)
    if parts[:-1] and isinstance(task.get(parts[0]), dict) and not task[parts[0]]:
        task.pop(parts[0])


def apply_fallback(task: dict, field: str) -> tuple[dict, dict]:
    new = copy.deepcopy(task)
    if field == "inputs.seedImage":
        val = new["inputs"].pop("seedImage")
        new["inputs"].setdefault("referenceImages", []).insert(0, val)
        new.pop("strength", None)
        return new, {"field": field, "action": "converted_to_referenceImages"}
    if field == "strength":
        new.pop("strength", None)
        return new, {"field": field, "action": "dropped_strength"}
    _delete_path(new, field)
    action = "dropped_reference_images" if field == "inputs.referenceImages" else "dropped"
    return new, {"field": field, "action": action}


async def run_with_policy(
    client,
    task: dict,
    *,
    timeout_s: float,
    cancel_event: asyncio.Event | None,
    on_progress: Callable[[int], None] | None,
    on_attempt: Callable[[dict, list[dict]], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> TaskResult:
    dropped: list[dict] = []
    backoffs: dict[str, int] = {}
    current = copy.deepcopy(task)
    last: RunwareError | None = None

    def _progress(item: dict) -> None:
        p = item.get("progress") if isinstance(item, dict) else None
        if on_progress and isinstance(p, (int, float)):
            on_progress(int(p))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RunwareError("aborted", "Request aborted")
        opts = RunOptions(
            timeout=int(timeout_s * 1000),
            cancel_event=cancel_event,
            on_progress=_progress,
            validate=False,
        )
        try:
            rows = await client.run(current, opts)
            return TaskResult(parse_items(rows), current["taskUUID"], current, dropped, attempt)
        except RunwareError as e:
            last = e
            if e.code == "validation":
                fld = rejected_field(e, current)
                if fld is None:
                    raise
                current, rec = apply_fallback(current, fld)
                dropped.append(rec)
                current["taskUUID"] = str(uuid.uuid4())
                if on_attempt:
                    on_attempt(current, dropped)
                continue
            delays = _BACKOFF.get(e.code)
            if not delays:
                raise
            backoffs[e.code] = backoffs.get(e.code, 0) + 1
            if backoffs[e.code] > len(delays):
                raise
            await sleep(delays[backoffs[e.code] - 1])
            continue
    assert last is not None
    raise last
