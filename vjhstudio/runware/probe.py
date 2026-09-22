"""Free constraint discovery. RunWare validates a request before it bills; these two
requests are built so they can never pass validation (see the spec's probe safety rules):
1) an unknown key -> the model's allowed parameter list; 2) width=height=1 (only when the
model takes width/height) -> its supported sizes or size rule. Anything accepted is a bug
and is raised as ProbeBilledError so the caller stops immediately."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from runware import RunOptions, RunwareError

PROBE_KEY = "vjhProbe"
PROBE_PROMPT = "a red fox in a snowy forest"
_QUOTED = re.compile(r"'([^']+)'")
_ALLOWED = re.compile(r"Allowed values are:\s*(.*)$", re.I | re.S)
_SUPPORTED = re.compile(r"Supported values are:\s*(.*)$", re.I | re.S)
_PAIR = re.compile(r"^(\d{2,5})\s*[x×*]\s*(\d{2,5})$")
_RULE = re.compile(r"between\s+(\d+)\s+and\s+(\d+)(?:,?\s+in multiples of\s+'?(\d+)'?)?", re.I)
_MISSING = re.compile(r"Missing required parameter:\s*'([^']+)'", re.I)


class ProbeBilledError(RuntimeError):
    """A probe was accepted, which means it was (or will be) billed."""


def task_type(kind: str) -> str:
    return "videoInference" if kind == "video" else "imageInference"


def parse_allowed_params(message: str) -> list[str]:
    m = _ALLOWED.search(message or "")
    return _QUOTED.findall(m.group(1)) if m else []


def parse_supported_dims(message: str) -> dict:
    msg = message or ""
    m = _SUPPORTED.search(msg)
    if m:
        pairs = []
        for token in _QUOTED.findall(m.group(1)):
            pm = _PAIR.match(token.strip())
            if pm:
                pairs.append([int(pm.group(1)), int(pm.group(2))])
        if pairs:
            return {"mode": "list", "list": pairs}
    r = _RULE.search(msg)
    if r:
        return {
            "mode": "rule",
            "min": int(r.group(1)),
            "max": int(r.group(2)),
            "step": int(r.group(3) or 1),
        }
    return {"mode": "unknown"}


def parse_missing_required(message: str) -> str | None:
    m = _MISSING.search(message or "")
    return m.group(1) if m else None


@dataclass(frozen=True)
class ProbeResult:
    params: list[str] = field(default_factory=list)
    dims: dict | None = None
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _base(air: str, kind: str) -> dict:
    return {
        "taskType": task_type(kind),
        "taskUUID": str(uuid.uuid4()),
        "model": air,
        "positivePrompt": PROBE_PROMPT,
    }


async def _send(client, task: dict) -> RunwareError:
    """Returns the rejection. An accepted request is a safety failure."""
    try:
        await client.run(task, RunOptions(timeout=30_000, validate=False))
    except RunwareError as e:
        return e
    raise ProbeBilledError(
        f"probe for {task.get('model')} was accepted; stop and check the RunWare balance"
    )


async def probe_model(client, air: str, kind: str) -> ProbeResult:
    errors: list[str] = []
    e1 = await _send(client, {**_base(air, kind), PROBE_KEY: 1})
    if e1.code != "validation":
        return ProbeResult(errors=[f"{e1.code}: {e1.message}"])
    params = parse_allowed_params(e1.message or "")
    if not params:
        errors.append(f"unexpected reply to the parameter probe: {e1.message}")
    if "width" not in params or "height" not in params:
        return ProbeResult(params=params, dims=None, errors=errors)  # safety rule 3
    e2 = await _send(client, {**_base(air, kind), "width": 1, "height": 1})
    if e2.code != "validation":
        return ProbeResult(params=params, dims=None, errors=errors + [f"{e2.code}: {e2.message}"])
    missing = [m] if (m := parse_missing_required(e2.message or "")) else []
    return ProbeResult(
        params=params, dims=parse_supported_dims(e2.message or ""), missing=missing, errors=errors
    )
