"""Read/write .gdoc-state/<doc_id>.json — replay's resumable progress file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_DIRNAME = ".gdoc-state"
LEGACY_STATE_FILENAME = ".gdoc-replay-state.json"


def commit_message_for(ev) -> str:
    """Commit subject for a replay event. Shared by the runner (to write
    commits) and reconstruction (to match pre-trailer commits)."""
    if ev.kind == "prose_change":
        return f"prose: revision {ev.revision_id}"
    if ev.kind == "comment_create":
        return f"comment: {ev.comment_id}"
    if ev.kind == "comment_edit":
        return f"comment edit: {ev.comment_id}"
    if ev.kind == "comment_delete":
        return f"comment delete: {ev.comment_id}"
    if ev.kind == "reply_create":
        return f"reply: {ev.comment_id} {ev.reply_id}"
    if ev.kind == "reply_resolve":
        return f"resolve: {ev.comment_id}"
    if ev.kind == "reply_reopen":
        return f"reopen: {ev.comment_id}"
    return ev.kind


def reconstruct_committed_set(events, cwd: Path, out_path=None) -> dict[str, str]:
    """Recover {event_id: sha} from git history when the state file is gone.

    Each replay commit carries a `Gdoc-event: <event_id>` trailer (exact
    match). Commits predating that trailer are matched by
    (commit_message_for(ev), author-date), which is unique in practice.
    Only events present in `events` are returned.

    When `out_path` is given, the git log is restricted to commits that touch
    that file, preventing commits from other docs (which may share the same
    integer revision id sequence) from being matched. Pass the doc's output
    .md path to enable cross-doc isolation in a shared repo.
    """
    import subprocess
    from datetime import datetime

    # sha \0 author-date \0 subject \0 trailer-value, one record per commit.
    fmt = "%H%x00%aI%x00%s%x00%(trailers:key=Gdoc-event,valueonly)"
    argv = ["git", "log", f"--format={fmt}"]
    if out_path is not None:
        try:
            relpath = str(out_path.relative_to(cwd))
        except (ValueError, AttributeError):
            relpath = str(out_path)
        argv += ["--", relpath]
    try:
        out = subprocess.run(
            argv,
            cwd=str(cwd), capture_output=True, text=True,
        )
    except FileNotFoundError:
        return {}
    if out.returncode != 0:
        return {}

    by_event: dict[str, str] = {}                 # event_id -> sha (from trailer)
    by_msg_date: dict[tuple[str, str], str] = {}  # (subject, iso) -> sha (fallback)
    for line in out.stdout.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\x00")
        if len(parts) < 4:
            continue
        sha, author_date, subject, trailer = parts[0], parts[1], parts[2], parts[3].strip()
        if trailer:
            by_event.setdefault(trailer, sha)
        try:
            iso = datetime.fromisoformat(author_date).isoformat()
        except ValueError:
            iso = author_date
        by_msg_date.setdefault((subject, iso), sha)

    result: dict[str, str] = {}
    for ev in events:
        if ev.event_id in by_event:
            result[ev.event_id] = by_event[ev.event_id]
            continue
        key = (commit_message_for(ev), ev.timestamp.isoformat())
        if key in by_msg_date:
            result[ev.event_id] = by_msg_date[key]
    return result


@dataclass
class EventState:
    id: str
    kind: str
    timestamp: str            # ISO-8601
    author: str
    revision_id: str | None = None
    comment_id: str | None = None
    reply_id: str | None = None
    status: str = "pending"   # pending | committed | failed
    git_sha: str | None = None


@dataclass
class ReplayState:
    doc_id: str
    out_path: str
    extract_assets: bool
    include_comments: bool
    since: str | None
    until: str | None
    timeline_hash: str
    events: list[EventState] = field(default_factory=list)

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, blob: str) -> ReplayState:
        d = json.loads(blob)
        events = [EventState(**e) for e in d.pop("events", [])]
        return cls(events=events, **d)


def default_state_path(doc_id: str, cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / STATE_DIRNAME / f"{doc_id}.json"


def legacy_state_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / LEGACY_STATE_FILENAME


def write_state(state: ReplayState, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.to_json())
    return path


def read_state(path: Path) -> ReplayState | None:
    if not path.exists():
        return None
    return ReplayState.from_json(path.read_text())


def remove_state(path: Path) -> None:
    if path.exists():
        path.unlink()
