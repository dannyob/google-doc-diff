"""Read/write .gdoc-state/<doc_id>.json — replay's resumable progress file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_DIRNAME = ".gdoc-state"
LEGACY_STATE_FILENAME = ".gdoc-replay-state.json"


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
