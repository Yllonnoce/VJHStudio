"""Plain-file API key stash with owner-only permissions. Env var wins."""
from __future__ import annotations
import os, sys
from typing import Literal, Mapping
from .config import Paths

ENV_KEY = "RUNWARE_API_KEY"


def write_api_key(paths: Paths, key: str) -> None:
    key = (key or "").strip()
    if not key:
        raise ValueError("API key is empty")
    paths.secrets.mkdir(parents=True, exist_ok=True)
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
    try:
        paths.api_key_file.unlink()
    except FileNotFoundError:
        pass


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
