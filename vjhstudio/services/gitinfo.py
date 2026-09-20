"""Read-only git facts about the checkout. Never prompts, never fails loudly."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from ..config import REPO_ROOT

GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -oBatchMode=yes")


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    short: str
    subject: str


def git_bin() -> str:
    return os.environ.get("VJHSTUDIO_GIT") or "git"


def run_git(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    # Fixed argv, no shell; args are built by this module, never by a user.
    return subprocess.run(
        [git_bin(), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,  # noqa: S603
        timeout=timeout,
        env=GIT_ENV,
        check=False,
    )


def is_git_install() -> bool:
    return (REPO_ROOT / ".git").exists()


def current_commit() -> CommitInfo | None:
    if not is_git_install():
        return None
    try:
        p = run_git(["log", "-1", "--format=%H%x00%h%x00%s"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    sha, short, subject = p.stdout.strip().split("\x00", 2)
    return CommitInfo(sha, short, subject)
