"""Retry + parameter-fallback policy around client.run()."""

from __future__ import annotations

import asyncio
import copy
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from runware import RunOptions, RunwareError

from .probe import parse_supported_dims
from .results import TaskResult, parse_items
from .sizes import nearest_size_in

MAX_ATTEMPTS = 5
PROTECTED = ("taskType", "taskUUID", "model", "positivePrompt")
PAIR = ("width", "height")
_UNSUPPORTED = re.compile(r"unsupported use of '?([A-Za-z0-9_.]+)'? parameter", re.I)
# The fallback contract is about *unsupported* parameters only. RunWare reports an
# out-of-range value ("Invalid value for 'height' parameter…") with the same
# ``validation`` code and a populated ``.parameter``; dropping that field would delete a
# required input, retry blindly and finally fail with a misleading cause, so every
# message that does not say "unsupported" must surface as-is.
_UNSUPPORTED_HINT = re.compile(r"unsupported", re.I)
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
    message = getattr(err, "message", None) or str(err)
    if not _UNSUPPORTED_HINT.search(message):
        return None
    cand = getattr(err, "parameter", None)
    if not cand:
        m = _UNSUPPORTED.search(message)
        cand = m.group(1) if m else None
    if not cand:
        return None
    path = cand if _has_path(task, cand) else _find_path(task, cand.split(".")[-1])
    if not path or path.split(".")[0] in PROTECTED or path in PROTECTED:
        return None
    if path.split(".")[-1] in PAIR:
        return None  # width/height are corrected (size_correction), never dropped
    return path


def size_correction(err: BaseException, task: dict) -> tuple[dict, dict] | None:
    """RunWare's own rejection names the model's supported sizes (a list) or its size
    rule (min/max/step); pick the nearest one instead of dropping width/height, which
    would delete required fields and still fail. Returns ``None`` when the message
    isn't about width/height or the correction wouldn't change anything."""
    message = getattr(err, "message", None) or str(err)
    param = str(getattr(err, "parameter", "") or "")
    if not ("width" in param or "height" in param or "width/height" in message):
        return None
    dims = parse_supported_dims(message)
    if dims.get("mode") == "unknown" or "width" not in task or "height" not in task:
        return None
    w, h = int(task["width"]), int(task["height"])
    nw, nh = nearest_size_in(dims, w, h)
    if (nw, nh) == (w, h):
        return None
    new = copy.deepcopy(task)
    new["width"], new["height"] = nw, nh
    return new, {
        "field": "width/height",
        "action": "corrected",
        "from": [w, h],
        "to": [nw, nh],
        "dims": dims,
    }


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
        # timed per attempt: a rateLimit backoff of up to 15s must not be charged to the
        # model's rolling latency average, which drives the estimated progress bar.
        attempt_started_at = time.monotonic()
        try:
            rows = await client.run(current, opts)
            duration_ms = int((time.monotonic() - attempt_started_at) * 1000)
            return TaskResult(
                parse_items(rows),
                current["taskUUID"],
                current,
                dropped,
                attempt,
                duration_ms,
                list(rows or []),
            )
        except RunwareError as e:
            last = e
            if e.code == "validation":
                if not any(r.get("action") == "corrected" for r in dropped):
                    corrected = size_correction(e, current)
                    if corrected is not None:
                        current, rec = corrected
                        dropped.append(rec)
                        current["taskUUID"] = str(uuid.uuid4())
                        if on_attempt:
                            on_attempt(current, dropped)
                        continue
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
