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
        return self._reply("run", params)

    async def account_management(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("account_management", params)

    async def model_search(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("model_search", params)

    async def media_storage(self, params: dict, options: Any = None) -> list[dict]:
        return self._reply("media_storage", params)


def fake_factory(fake: FakeRunware):
    @asynccontextmanager
    async def _open(api_key: str, transport: str = "rest"):
        fake.calls.append(("open", {"api_key_len": len(api_key), "transport": transport}))
        yield fake
    return _open
