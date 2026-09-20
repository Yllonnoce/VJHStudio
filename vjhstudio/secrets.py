"""Plain-file API key stash with owner-only permissions. Env var wins."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Literal

from .config import Paths

ENV_KEY = "RUNWARE_API_KEY"


def write_api_key(paths: Paths, key: str) -> None:
    key = (key or "").strip()
    if not key:
        raise ValueError("API key is empty")
    paths.secrets.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        # This can run before ensure_dirs(), which is the other place the 0700
        # is applied. On Windows the mode is a no-op: the dir ACL is inherited.
        os.chmod(paths.secrets, 0o700)
    fd = os.open(paths.api_key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    if sys.platform != "win32":
        os.chmod(paths.api_key_file, 0o600)


def read_api_key(paths: Paths) -> str | None:
    try:
        v = paths.api_key_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return v or None


def clear_api_key(paths: Paths) -> None:
    paths.api_key_file.unlink(missing_ok=True)


def effective_api_key(paths: Paths, env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    v = (env.get(ENV_KEY) or "").strip()
    return v or read_api_key(paths)


def key_source(paths: Paths, env: Mapping[str, str] | None = None) -> Literal["env", "file", "none"]:
    env = os.environ if env is None else env
    if (env.get(ENV_KEY) or "").strip():
        return "env"
    return "file" if read_api_key(paths) else "none"


def mask(key: str | None) -> str:
    if not key:
        return ""
    tail = key[-4:] if len(key) >= 8 else ""
    return "••••••••" + tail
