"""Thin shell-out wrapper for git operations the replay runner needs."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path


class GitError(RuntimeError):
    pass


def is_clean(cwd: Path | None = None) -> bool:
    """True iff the working tree (excluding untracked) has no staged/unstaged changes."""
    r = _run(["git", "status", "--porcelain"], cwd=cwd, capture=True)
    return r.stdout.strip() == ""


def add(paths: list[Path], cwd: Path | None = None) -> None:
    if not paths:
        return
    _run(["git", "add", "--"] + [str(p) for p in paths], cwd=cwd)


def commit(
    message: str,
    *,
    author_name: str,
    author_email: str,
    timestamp: datetime,
    cwd: Path | None = None,
) -> str:
    """Create a commit with the given author identity + timestamp.

    Returns the new commit's SHA.
    """
    iso = timestamp.isoformat()
    env = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_AUTHOR_DATE": iso,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
        "GIT_COMMITTER_DATE": iso,
    }
    _run(
        ["git", "commit", "-m", message, "--allow-empty"],
        cwd=cwd, extra_env=env,
    )
    r = _run(["git", "rev-parse", "HEAD"], cwd=cwd, capture=True)
    return r.stdout.strip()


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    import os
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    r = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=capture,
        text=True,
    )
    if r.returncode != 0:
        out = (r.stdout or "") + (r.stderr or "")
        raise GitError(f"{' '.join(cmd)} failed: {out.strip()}")
    return r
