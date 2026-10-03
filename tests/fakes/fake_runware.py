"""Scripted stand-in for runware.Runware. Each method pops the next scripted reply for its
name; a reply that is an Exception is raised instead of returned."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any


class FakeRunware:
    def __init__(self, script: dict[str, list[Any]] | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[tuple[str, dict]] = []

    def _reply(self, name: str, params: dict) -> Any:
        self.calls.append((name, params))
        queue = self.script.get(name) or []
        if not queue:
            raise AssertionError(f"FakeRunware has no scripted reply for {name}")
        r = queue.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    async def run(self, params: dict, options: Any = None) -> list[dict]:
        cancel_event = getattr(options, "cancel_event", None) if options is not None else None
        if cancel_event is not None and cancel_event.is_set():
            from runware import RunwareError

            raise RunwareError("aborted", "Request aborted")
        reply = self._reply("run", params)
        if isinstance(reply, tuple) and len(reply) == 3 and reply[0] == "progress":
            _, progress_values, result = reply
            on_progress = getattr(options, "on_progress", None) if options is not None else None
            for p in progress_values:
                if on_progress is not None:
                    on_progress({"progress": p})
            return result
        return reply

    async def stream(self, params: dict, options: Any = None) -> FakeStream:
        """Scripted as ``{"pieces": [...], "cost": 0.001, "usage": {...}, "finish": "stop"}``;
        an exception instead is raised by the call itself, and ``"error": exc`` inside the
        dict is raised after the pieces (a stream that breaks half way)."""
        return FakeStream(self._reply("stream", params), getattr(options, "cancel_event", None))

    async def account_management(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("account_management", params)

    async def model_search(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("model_search", params)

    async def media_storage(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("media_storage", params)


class FakeStream:
    """Stand-in for the SDK's ``TextStream``: ``text_stream`` and ``result()``."""

    def __init__(self, reply: dict, cancel_event: Any = None):
        self.reply = reply
        self.cancel_event = cancel_event
        self.sent: list[str] = []

    @property
    def text_stream(self):
        return self._pieces()

    async def _pieces(self):
        from runware import RunwareError

        for piece in self.reply.get("pieces") or []:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise RunwareError("aborted", "Request aborted")
            self.sent.append(piece)
            yield piece
        if self.reply.get("error") is not None:
            raise self.reply["error"]

    async def result(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            text="".join(self.sent),
            reasoning_content="",
            finish_reason=self.reply.get("finish", "stop"),
            usage=self.reply.get("usage"),
            cost=self.reply.get("cost"),
        )


def fake_factory(fake: FakeRunware):
    @asynccontextmanager
    async def _open(api_key: str, transport: str = "rest"):
        fake.calls.append(("open", {"api_key_len": len(api_key), "transport": transport}))
        yield fake

    return _open
