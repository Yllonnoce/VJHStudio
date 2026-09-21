"""Send the composed prompt to RunWare for a rewrite (``promptEnhance`` or
``textInference``) and hand back the versions as a ``PolishResult``. Runs the task
through the existing ``runware.runner.run_with_policy`` (retry + classified errors for
free) inside the caller's ``client_factory``. Writes nothing by itself: the route it
serves records a usage row and, if the caller later saves or submits with it, a
``Prompt.polish_json`` gets written elsewhere."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from ..runware import tasks
from ..runware.runner import run_with_policy

POLISH_TIMEOUT_S = 30.0
VERSIONS_MAX = 3
MODES = ("promptEnhance", "textInference")

_NUMBERED_LINE = re.compile(r"^\s*\d+[.)]\s*", re.MULTILINE)


def clamp_versions(raw) -> int:
    """Garbage (missing, non-numeric) -> 1; otherwise clamped to 1..``VERSIONS_MAX``."""
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return 1
    return max(1, min(VERSIONS_MAX, v))


def split_versions(text: str, versions: int) -> list[str]:
    """Split a ``1. ...\\n2. ...`` reply into its numbered lines, truncated to
    ``versions``. Text with no numbering (or a single line) comes back as ``[text]``."""
    body = (text or "").strip()
    if not body:
        return [body]
    parts = [p.strip() for p in _NUMBERED_LINE.split(body)]
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return [body]
    return parts[: max(1, int(versions))]


@dataclass(frozen=True)
class PolishVersion:
    text: str
    cost: float | None


@dataclass(frozen=True)
class PolishResult:
    mode: str
    model: str
    versions: list[PolishVersion] = field(default_factory=list)
    cost: float = 0.0
    source: str = "polish"

    def to_json(self, chosen_index: int | None = None) -> dict:
        return {
            "source": self.source,
            "mode": self.mode,
            "model": self.model,
            "versions": [v.text for v in self.versions],
            "chosen_index": chosen_index,
            "cost": self.cost,
        }


def _cost_of(row: dict) -> float | None:
    raw = row.get("cost")
    return float(raw) if raw is not None else None


async def run(
    client_factory,
    api_key: str,
    transport: str,
    *,
    mode: str,
    composed: str,
    model: str = "",
    versions: int = 3,
    timeout_s: float = POLISH_TIMEOUT_S,
) -> PolishResult:
    """Raises ``ValueError`` for a caller mistake (nothing to polish, no text model
    chosen for ``textInference``) and lets ``RunwareError`` through unmodified so the
    route's ``errors.classify()`` handling stays the single place that message is worded."""
    text = (composed or "").strip()
    if not text:
        raise ValueError("Write a prompt first.")
    mode = mode if mode in MODES else MODES[0]
    v = clamp_versions(versions)
    task_uuid = str(uuid.uuid4())

    if mode == "promptEnhance":
        # No model AIR: the live API takes no `model` for promptEnhance (it's a fixed
        # server-side model). Record the task type itself so the usage row's
        # `model_air` stays non-null and stable.
        used_model = "promptEnhance"
        task = tasks.build_prompt_enhance(text, task_uuid, versions=v)
    else:
        if not model:
            raise ValueError("Choose a text model to polish with.")
        used_model = model
        task = tasks.build_polish_text(used_model, text, task_uuid, versions=v)

    async with client_factory(api_key, transport) as client:
        result = await run_with_policy(
            client, task, timeout_s=timeout_s, cancel_event=None, on_progress=None
        )

    if mode == "promptEnhance":
        versions_out = [
            PolishVersion(str(row.get("text") or ""), _cost_of(row)) for row in result.rows
        ]
        total_cost = sum(pv.cost or 0.0 for pv in versions_out)
    else:
        row = result.rows[0] if result.rows else {}
        versions_out = [
            PolishVersion(t, None) for t in split_versions(str(row.get("text") or ""), v)
        ]
        total_cost = _cost_of(row) or 0.0

    return PolishResult(mode=mode, model=used_model, versions=versions_out, cost=total_cost)
