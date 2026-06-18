"""Thin shell-out wrapper for git operations the replay runner needs."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path


class GitError(RuntimeError):
    pass


def is_clean(
    cwd: Path | None = None,
    ignore: list[str] | None = None,
    ignore_prefixes: list[str] | None = None,
) -> bool:
    """True iff the working tree has no staged/unstaged changes (excluding
    untracked, plus any paths in `ignore` or under any of `ignore_prefixes`)."""
    r = _run(["git", "status", "--porcelain"], cwd=cwd, capture=True)
    ignored = set(ignore or [])
    prefixes = tuple(ignore_prefixes or ())
    for line in r.stdout.splitlines():
        path = line[3:].strip()
        if path in ignored:
            continue
        if prefixes and path.startswith(prefixes):
            continue
        return False
    return True


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
    event_id: str | None = None,
) -> str:
    """Create a commit with the given author identity + timestamp.

    When event_id is given, append a `Gdoc-event: <event_id>` trailer so the
    committed event is recoverable from `git log` without the state file.

    Returns the new commit's SHA.
    """
    iso = timestamp.isoformat()
    full_message = message
    if event_id:
        full_message = f"{message}\n\nGdoc-event: {event_id}\n"
    env = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_AUTHOR_DATE": iso,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
        "GIT_COMMITTER_DATE": iso,
    }
    _run(
        ["git", "commit", "-m", full_message, "--allow-empty"],
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
