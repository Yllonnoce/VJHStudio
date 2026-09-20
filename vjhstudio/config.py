"""Environment and filesystem configuration. No I/O beyond mkdir."""
from __future__ import annotations
import os, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
RESTART_EXIT_CODE = 75
MIGRATION_FAIL_EXIT_CODE = 3
DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"
ENV_PREFIX = "VJHSTUDIO_"


@dataclass(frozen=True)
class Paths:
    data: Path
    db: Path
    backups: Path
    uploads: Path
    outputs: Path
    secrets: Path
    api_key_file: Path


def resolve_paths(env: Mapping[str, str] | None = None) -> Paths:
    env = os.environ if env is None else env
    raw = env.get(ENV_PREFIX + "DATA_DIR")
    data = Path(raw).expanduser().resolve() if raw else REPO_ROOT / "data"
    secrets = data / "secrets"
    return Paths(
        data=data, db=data / "vjh.db", backups=data / "backups",
        uploads=data / "uploads", outputs=data / "outputs",
        secrets=secrets, api_key_file=secrets / "api_key",
    )


def ensure_dirs(paths: Paths) -> None:
    for d in (paths.data, paths.backups, paths.uploads, paths.outputs, paths.secrets):
        d.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(paths.secrets, 0o700)


def env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


def env_str(env: Mapping[str, str], name: str, default: str) -> str:
    v = env.get(name)
    return v if v else default
