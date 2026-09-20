from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, AsyncContextManager
from runware import Runware

ClientFactory = Callable[..., AsyncContextManager[Any]]


@asynccontextmanager
async def open_client(api_key: str, transport: str = "rest") -> AsyncIterator[Runware]:
    """One connected client. REST needs no connect; websocket connects lazily on first run()."""
    client = Runware(api_key=api_key, transport=transport)
    try:
        yield client
    finally:
        await client.close()
