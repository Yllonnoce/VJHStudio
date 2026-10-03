"""The ``textInference`` task behind the Chat page. Pure: no I/O.

Checked live on 2026-10-03: pictures are a task-level ``inputs.images`` list (not part of
a message) and RunWare does not remember them between turns, so the caller passes every
picture of the conversation each time. ``settings.maxTokens`` is deliberately absent: a
reasoning model given a small one spends it all thinking and returns no text."""

from __future__ import annotations

from collections.abc import Sequence


def build_chat_task(
    model: str,
    messages: list[dict],
    task_uuid: str,
    *,
    system_prompt: str = "",
    images: Sequence[str] = (),
) -> dict:
    """``messages`` is the conversation as ``{"role", "content"}`` rows ending on the
    user's turn (``services.chat.history``)."""
    task: dict = {
        "taskType": "textInference",
        "taskUUID": task_uuid,
        "model": model,
        "messages": messages,
        "includeCost": True,
        "includeUsage": True,
    }
    if system_prompt.strip():
        task["settings"] = {"systemPrompt": system_prompt.strip()}
    if images:
        task["inputs"] = {"images": list(images)}
    return task
