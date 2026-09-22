from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from runware import Runware

ClientFactory = Callable[..., AbstractAsyncContextManager[Any]]


@asynccontextmanager
async def open_client(api_key: str, transport: str = "websocket") -> AsyncIterator[Runware]:
    """One connected client. REST needs no connect; websocket connects lazily on first run()."""
    client = Runware(api_key=api_key, transport=transport)
    try:
        yield client
    finally:
        await client.close()
