"""The part of a provider failure the SDK throws away.

RunWare reports a provider-side failure as ``"<provider> responded with HTTP <n>.
Additional information below."`` and puts the information in ``responseContent``, a
field ``runware.errors.parse_api_error`` does not keep. Asking for the task's stored
reply (``getResponse``) is free and returns the same error with that field intact."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)
API_URL = "https://api.runware.ai/v1"
MARKER = "additional information below"
# What a provider's own wording means for the person reading the job card.
_HINTS = (
    (
        ("jobnumexceed", "task limit has been reached"),
        "The provider only runs so many of your jobs at once, and an earlier job is still "
        "running there (a job you stopped waiting for keeps going). Try again when it has "
        "finished.",
    ),
)


def wants_detail(message: str | None) -> bool:
    return MARKER in (message or "").lower()


def hint_for(detail: str) -> str:
    low = detail.lower()
    for needles, hint in _HINTS:
        if any(n in low for n in needles):
            return hint
    return ""


async def error_detail(
    api_key: str,
    task_uuid: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 15.0,
) -> str | None:
    """``responseContent`` of a failed task, or ``None`` when it cannot be had (no key,
    network trouble, a reply in another shape): the caller then keeps RunWare's own
    message. Never raises."""
    if not api_key or not task_uuid:
        return None
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            r = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=[{"taskType": "getResponse", "taskUUID": task_uuid}],
            )
        body = r.json()
        errors = body.get("errors") if isinstance(body, dict) else None
        first = errors[0] if isinstance(errors, list) and errors else None
        content = first.get("responseContent") if isinstance(first, dict) else None
        return str(content).strip() or None if content else None
    except Exception as e:  # noqa: BLE001 - a nicer message is a bonus, never a new failure
        log.info("no error detail for task %s: %s", task_uuid, e)
        return None
