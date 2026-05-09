"""Build a unified chronological event timeline from revisions + comments.

Combines `drive.revisions.list` results (prose changes) with
`drive.comments.list` results (comment + reply create / edit / resolve /
reopen events) into one sorted list of `Event`s. Adjacent same-author
prose-change events within the squash window collapse to a single event.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from google_doc_diff.ast.from_docs_json import _author_email, _parse_iso

# Event kinds in order of preference when timestamps tie:
EVENT_KINDS = (
    "prose_change",
    "comment_create",
    "comment_edit",
    "comment_delete",
    "reply_create",
    "reply_resolve",
    "reply_reopen",
)


@dataclass
class Event:
    """One step in the replayed history."""

    kind: str                              # one of EVENT_KINDS
    timestamp: datetime
    author: str
    revision_id: str | None = None         # for prose_change events
    comment_id: str | None = None          # for comment_*/reply_* events
    reply_id: str | None = None            # for reply_* events
    payload: dict = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        """Stable identifier used in the replay state file."""
        if self.kind == "prose_change":
            return f"rev-{self.revision_id}"
        if self.kind.startswith("reply_"):
            return f"{self.kind}-{self.comment_id}-{self.reply_id}"
        return f"{self.kind}-{self.comment_id}"


def build_timeline(
    revisions: list[dict],
    comments: list[dict],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    squash_by_author: timedelta | None = None,
) -> list[Event]:
    events: list[Event] = []

    for rev in revisions:
        ts = _parse_iso(rev.get("modifiedDate"))
        events.append(Event(
            kind="prose_change",
            timestamp=ts,
            author=_author_email(rev.get("lastModifyingUser")),
            revision_id=rev["id"],
            payload={"export_links": rev.get("exportLinks", {})},
        ))

    for c in comments:
        cid = "c-" + c["id"]
        events.append(Event(
            kind="comment_create",
            timestamp=_parse_iso(c.get("createdTime")),
            author=_author_email(c.get("author")),
            comment_id=cid,
            payload={"comment": c},
        ))
        modified = c.get("modifiedTime")
        if modified and modified != c.get("createdTime") and not c.get("replies"):
            # If modifiedTime > createdTime AND there are no replies that
            # account for the modification, treat as an edit event. (When
            # replies exist, modifiedTime tracks reply activity.)
            events.append(Event(
                kind="comment_edit",
                timestamp=_parse_iso(modified),
                author=_author_email(c.get("author")),
                comment_id=cid,
                payload={"comment": c},
            ))
        if c.get("deleted"):
            events.append(Event(
                kind="comment_delete",
                timestamp=_parse_iso(modified or c.get("createdTime")),
                author=_author_email(c.get("author")),
                comment_id=cid,
                payload={"comment": c},
            ))
        for r in c.get("replies", []):
            action = r.get("action")
            if action == "resolve":
                kind = "reply_resolve"
            elif action == "reopen":
                kind = "reply_reopen"
            else:
                kind = "reply_create"
            events.append(Event(
                kind=kind,
                timestamp=_parse_iso(r.get("createdTime")),
                author=_author_email(r.get("author")),
                comment_id=cid,
                reply_id="r-" + r["id"],
                payload={"reply": r},
            ))

    if since:
        events = [e for e in events if e.timestamp >= since]
    if until:
        events = [e for e in events if e.timestamp <= until]

    events.sort(key=lambda e: (e.timestamp, EVENT_KINDS.index(e.kind)))

    if squash_by_author:
        events = _squash_by_author(events, squash_by_author)

    return events


def _squash_by_author(events: list[Event], window: timedelta) -> list[Event]:
    """Collapse adjacent same-author prose_change events within `window`.

    The kept event is the LAST in the window (so the saved revision is
    whichever one the author last touched). Other event kinds pass through
    unchanged.
    """
    if not events:
        return events
    out: list[Event] = []
    pending: Event | None = None
    for ev in events:
        if ev.kind != "prose_change":
            if pending:
                out.append(pending)
                pending = None
            out.append(ev)
            continue
        if (
            pending is not None
            and pending.author == ev.author
            and ev.timestamp - pending.timestamp <= window
        ):
            pending = ev      # keep the later one
        else:
            if pending:
                out.append(pending)
            pending = ev
    if pending:
        out.append(pending)
    return out


def timeline_hash(events: list[Event]) -> str:
    """Stable hash of the timeline for `--resume` correctness."""
    canon = [
        [e.event_id, e.kind, e.timestamp.isoformat(), e.author]
        for e in events
    ]
    blob = json.dumps(canon, sort_keys=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def event_to_state_dict(e: Event) -> dict:
    """Serialize for .gdoc-replay-state.json (without the heavy payload)."""
    return {
        "id": e.event_id,
        "kind": e.kind,
        "timestamp": e.timestamp.isoformat(),
        "author": e.author,
        "revision_id": e.revision_id,
        "comment_id": e.comment_id,
        "reply_id": e.reply_id,
    }


def _event_dict_no_payload(e: Event) -> dict:
    d = asdict(e)
    d.pop("payload", None)
    d["timestamp"] = e.timestamp.isoformat()
    return d
