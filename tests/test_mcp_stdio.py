"""`vjhstudio mcp`: the same tools over stdio, in a real subprocess.

stdout is the protocol channel, so this test also proves nothing on the boot path
writes to it -- a stray `print` would corrupt the very first JSON-RPC frame and the
handshake below would fail.
"""

import asyncio
import os
import socket
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

BOOT_TIMEOUT_S = 120  # boot migrates a fresh database and seeds the curated catalog


async def test_stdio_command_serves_tools(paths):
    # `vjhstudio mcp` refuses to boot next to a running web app, and the developer
    # running this suite may well have one open on the default port: point the probe
    # at a port nothing is listening on instead.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()
    env = os.environ | {
        "VJHSTUDIO_DATA_DIR": str(paths.data),
        "VJHSTUDIO_OFFLINE": "1",
        "VJHSTUDIO_PORT": str(free),
        "PYTHONUNBUFFERED": "1",
        "RUNWARE_API_KEY": "",  # listing tools must not need a key
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "vjhstudio.main", "mcp"], env=env
    )

    async def talk():
        async with Client(params) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert "list_projects" in names
            assert "generate_image" in names
            result = await c.call_tool("list_projects", {})
            assert not result.is_error

    await asyncio.wait_for(talk(), BOOT_TIMEOUT_S)
