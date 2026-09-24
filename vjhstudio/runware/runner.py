"""Retry + parameter-fallback policy around client.run()."""

from __future__ import annotations

import asyncio
import copy
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from runware import RunOptions, RunwareError

from .probe import parse_allowed_params, parse_supported_dims
from .results import TaskResult, parse_items
from .sizes import nearest_size_in

MAX_ATTEMPTS = 5
PROTECTED = ("taskType", "taskUUID", "model", "positivePrompt")
PAIR = ("width", "height")
# RunWare words an unknown key two ways: "unsupported use of 'x' parameter" and
# "Invalid parameter detected. The parameter "'x'" is not recognized or supported".
_UNSUPPORTED = re.compile(
    r"unsupported use of '?([A-Za-z0-9_.]+)'? parameter"
    r"""|parameter\s+["']*([A-Za-z0-9_.]+)["']*\s+is not recognized""",
    re.I,
)
# The fallback contract is about *unsupported* parameters only. RunWare reports an
# out-of-range value ("Invalid value for 'height' parameter…") with the same
# ``validation`` code and a populated ``.parameter``; dropping that field would delete a
# required input, retry blindly and finally fail with a misleading cause, so every
# message that does not say the *parameter* is unknown must surface as-is.
_UNSUPPORTED_HINT = re.compile(r"unsupported|not recognized", re.I)
# "Parameter 'inputs.frameImages' and Parameter 'inputs.referenceImages' cannot be used
# together" (Grok), "Parameter 'width' and Parameter 'inputs.frameImages' cannot be used
# together" (SkyReels).
_CONFLICT = re.compile(
    r"parameter\s+'([A-Za-z0-9_.]+)'\s+and\s+parameter\s+'([A-Za-z0-9_.]+)'"
    r"\s+cannot be used together",
    re.I,
)
# "Invalid value for 'duration' parameter. … Supported values are: '6', '10'" (MiniMax).
_INVALID_VALUE = re.compile(r"invalid value for '([A-Za-z0-9_.]+)' parameter", re.I)
# "Supported values are: '6', '10'" and "…with one of the following supported values:
# '480p', '768p'." are the two wordings seen live.
_SUPPORTED_VALUES = re.compile(r"supported values(?: are)?:\s*(.*)$", re.I | re.S)
_QUOTED = re.compile(r"'([^']+)'")
_LEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)")
# "Invalid number of elements for 'inputs.frameImages' parameter. Frame images must
# contain between 0 and 1 images." (Grok has no last-frame slot.)
_ELEMENT_COUNT = re.compile(
    r"invalid number of elements for '([A-Za-z0-9_.]+)' parameter.*?between\s+(\d+)\s+and\s+(\d+)",
    re.I | re.S,
)
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
        cand = (m.group(1) or m.group(2)) if m else None
    if not cand:
        return None
    path = cand if _has_path(task, cand) else _find_path(task, cand.split(".")[-1])
    if not path or path.split(".")[0] in PROTECTED or path in PROTECTED:
        return None
    if path in PAIR:
        # Only the task's OWN width/height: those are corrected (size_correction) rather
        # than dropped. A nested one — a frame image's size, say — is an ordinary field.
        return None
    return path


def size_correction(err: BaseException, task: dict) -> tuple[dict, dict] | None:
    """RunWare's own rejection names the model's supported sizes (a list) or its size
    rule (min/max/step); pick the nearest one instead of dropping width/height, which
    would delete required fields and still fail. Returns ``None`` when the message
    isn't about width/height or the correction wouldn't change anything."""
    message = getattr(err, "message", None) or str(err)
    param = str(getattr(err, "parameter", "") or "")
    # Only the top-level pair: a nested field such as inputs.frameImages.0.width must
    # never rewrite the task's own width/height.
    if not (param in PAIR or param == "width/height" or "width/height" in message):
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


def _pair_named(err: BaseException, task: dict) -> bool:
    """True when RunWare's complaint is about the task's own width/height *as a
    parameter* (unsupported here, or in conflict with an input), not about its value."""
    message = getattr(err, "message", None) or str(err)
    if "width" not in task or "height" not in task:
        return False
    conflict = _CONFLICT.search(message)
    if conflict and {conflict.group(1), conflict.group(2)} & set(PAIR):
        return True
    if not _UNSUPPORTED_HINT.search(message):
        return False
    param = str(getattr(err, "parameter", "") or "")
    if param in PAIR:
        return True
    m = _UNSUPPORTED.search(message)
    cand = (m.group(1) or m.group(2)) if m else None
    return cand in PAIR


def size_to_resolution(
    err: BaseException, task: dict, resolution: str | None
) -> tuple[dict, dict] | None:
    """Some models stop taking width/height the moment a frame or reference image is
    attached -- the image fixes the aspect ratio and a ``resolution`` preset picks the
    tier (Kling 3, Kling 2.6 Pro, MiniMax H3, HappyHorse, SkyReels). RunWare words it
    either as "unsupported use of 'width'" with an allowed list, or as a width/frame
    conflict. The pair goes; the preset the request was built from takes its place when
    the model lists ``resolution`` (or names no list at all), else nothing does and the
    frame alone sizes the clip. ``None`` when the message is about anything else --
    a size *list* ("Supported values are: '3840x2160'") is ``size_correction``'s."""
    if not _pair_named(err, task):
        return None
    message = getattr(err, "message", None) or str(err)
    if parse_supported_dims(message).get("mode") != "unknown":
        return None
    allowed = parse_allowed_params(message)
    new = copy.deepcopy(task)
    new.pop("width", None)
    new.pop("height", None)
    if resolution and (not allowed or "resolution" in allowed):
        new["resolution"] = resolution
        return new, {"field": "width/height", "action": "converted_to_resolution", "to": resolution}
    return new, {"field": "width/height", "action": "dropped"}


def conflict_field(err: BaseException, task: dict) -> str | None:
    """Two inputs the model will not take together: drop the second one named (the
    first is the more specific intent -- a frame over a reference image), or the first
    when the second is already gone. A conflict involving width/height is not a plain
    drop; ``size_to_resolution`` owns it."""
    message = getattr(err, "message", None) or str(err)
    m = _CONFLICT.search(message)
    if not m:
        return None
    first, second = m.group(1), m.group(2)
    if {first, second} & set(PAIR):
        return None
    for cand in (second, first):
        path = cand if _has_path(task, cand) else _find_path(task, cand.split(".")[-1])
        if path and path.split(".")[0] not in PROTECTED:
            return path
    return None


def _number_of(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _LEADING_NUMBER.match(str(value))
    return float(m.group(1)) if m else None


def _typed(token: str):
    try:
        f = float(token)
    except ValueError:
        return token
    return int(f) if f.is_integer() else f


def value_correction(err: BaseException, task: dict) -> tuple[dict, dict] | None:
    """A top-level scalar the model enumerates -- "Invalid value for 'duration' …
    Supported values are: '6', '10'" -- is moved to the nearest listed value (ties to
    the smaller, i.e. cheaper, one); a named preset such as '768p' is compared by its
    number. Width/height stay ``size_correction``'s, and a message with no list
    surfaces as-is."""
    message = getattr(err, "message", None) or str(err)
    m = _INVALID_VALUE.search(message)
    field = str(getattr(err, "parameter", "") or "") or (m.group(1) if m else "")
    if not field or "." in field or field in PROTECTED or field in PAIR or field not in task:
        return None
    listed = _SUPPORTED_VALUES.search(message)
    values = [_typed(v) for v in _QUOTED.findall(listed.group(1))] if listed else []
    if not values:
        return None
    current = task[field]
    if current in values:
        return None
    want = _number_of(current)
    numbered = [(v, _number_of(v)) for v in values]
    if want is not None and all(n is not None for _, n in numbered):
        chosen = min(numbered, key=lambda vn: (abs(vn[1] - want), vn[1]))[0]
    else:
        chosen = values[0]
    new = copy.deepcopy(task)
    new[field] = chosen
    return new, {
        "field": field,
        "action": "corrected",
        "from": current,
        "to": chosen,
        "values": values,
    }


def trim_correction(err: BaseException, task: dict) -> tuple[dict, dict] | None:
    """A list input longer than the model allows ("Frame images must contain between 0
    and 1 images") keeps its first items: the first frame outranks the last."""
    message = getattr(err, "message", None) or str(err)
    m = _ELEMENT_COUNT.search(message)
    if not m:
        return None
    field, cap = m.group(1), int(m.group(3))
    path = field if _has_path(task, field) else _find_path(task, field.split(".")[-1])
    if not path:
        return None
    parts = path.split(".")
    cur: object = task
    for part in parts:
        cur = cur[part] if isinstance(cur, dict) else None
    if not isinstance(cur, list) or len(cur) <= cap:
        return None
    new = copy.deepcopy(task)
    holder = new
    for part in parts[:-1]:
        holder = holder[part]
    holder[parts[-1]] = list(cur[:cap])
    return new, {"field": path, "action": "trimmed", "max": cap}


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


def _validation_fallback(
    err: RunwareError, task: dict, corrected: set[str], resolution: str | None
) -> tuple[dict, dict] | None:
    """One free retry per rule, in order: the task's own size (corrected once), pixels
    that must become a preset, a value the model enumerates (once per field), a list
    that is too long, two inputs in conflict, and finally an unsupported field."""
    if "width/height" not in corrected:
        fixed = size_correction(err, task)
        if fixed is not None:
            corrected.add("width/height")
            return fixed
    fixed = size_to_resolution(err, task, resolution)
    if fixed is not None:
        return fixed
    fixed = value_correction(err, task)
    if fixed is not None and fixed[1]["field"] not in corrected:
        corrected.add(fixed[1]["field"])
        return fixed
    if fixed is not None:
        return None
    fixed = trim_correction(err, task)
    if fixed is not None:
        return fixed
    fld = conflict_field(err, task) or rejected_field(err, task)
    if fld is None:
        return None
    return apply_fallback(task, fld)


async def run_with_policy(
    client,
    task: dict,
    *,
    timeout_s: float,
    cancel_event: asyncio.Event | None,
    on_progress: Callable[[int], None] | None,
    on_attempt: Callable[[dict, list[dict]], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    resolution: str | None = None,
) -> TaskResult:
    """``resolution`` is the preset the task's width/height were built from (\"720p\"):
    what replaces the pair when a model refuses pixels next to a frame image."""
    dropped: list[dict] = []
    corrected: set[str] = set()
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
                fixed = _validation_fallback(e, current, corrected, resolution)
                if fixed is None:
                    raise
                current, rec = fixed
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
