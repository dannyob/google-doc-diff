"""Tests for replay/timeline.py."""

from datetime import UTC, datetime, timedelta

from google_doc_diff.replay.timeline import (
    build_timeline,
    timeline_hash,
)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def _rev(rid, when, user="alice@example.com"):
    return {
        "id": str(rid),
        "modifiedDate": when.isoformat().replace("+00:00", "Z"),
        "lastModifyingUser": {"emailAddress": user},
        "exportLinks": {"text/markdown": f"https://x/{rid}.md"},
    }


def _comment(cid, created, content="x", replies=None, modified=None, deleted=False):
    c = {
        "id": cid,
        "createdTime": created.isoformat().replace("+00:00", "Z"),
        "modifiedTime": (modified or created).isoformat().replace("+00:00", "Z"),
        "author": {"emailAddress": "alice@example.com"},
        "content": content,
        "quotedFileContent": {"value": "snippet"},
        "deleted": deleted,
        "replies": replies or [],
    }
    return c


def _reply(rid, created, action=None, author="bob@example.com"):
    return {
        "id": rid,
        "createdTime": created.isoformat().replace("+00:00", "Z"),
        "modifiedTime": created.isoformat().replace("+00:00", "Z"),
        "author": {"emailAddress": author},
        "content": "reply text",
        "action": action,
    }


def test_prose_changes_become_events_in_order():
    revs = [_rev(1, utc(2026, 5, 1, 10)), _rev(2, utc(2026, 5, 1, 11))]
    events = build_timeline(revs, [])
    assert [e.kind for e in events] == ["prose_change", "prose_change"]
    assert events[0].revision_id == "1"
    assert events[0].author == "alice@example.com"


def test_comment_create_and_reply_events():
    cmt = _comment("AAA", utc(2026, 5, 1, 10), replies=[
        _reply("R1", utc(2026, 5, 1, 11)),
    ])
    events = build_timeline([], [cmt])
    kinds = [e.kind for e in events]
    assert kinds == ["comment_create", "reply_create"]


def test_resolve_and_reopen_replies():
    cmt = _comment("AAA", utc(2026, 5, 1, 10), replies=[
        _reply("R1", utc(2026, 5, 1, 11), action="resolve"),
        _reply("R2", utc(2026, 5, 1, 12), action="reopen"),
    ])
    events = build_timeline([], [cmt])
    kinds = [e.kind for e in events]
    assert kinds == ["comment_create", "reply_resolve", "reply_reopen"]


def test_chronological_sort_across_sources():
    revs = [_rev(1, utc(2026, 5, 1, 10)), _rev(2, utc(2026, 5, 1, 14))]
    cmt = _comment("AAA", utc(2026, 5, 1, 12))
    events = build_timeline(revs, [cmt])
    times = [e.timestamp for e in events]
    assert times == sorted(times)


def test_since_until_filters_apply():
    revs = [_rev(i, utc(2026, 5, i, 10)) for i in (1, 2, 3, 4)]
    events = build_timeline(
        revs, [],
        since=utc(2026, 5, 2),
        until=utc(2026, 5, 3, 23, 59),
    )
    rev_ids = [e.revision_id for e in events]
    assert rev_ids == ["2", "3"]


def test_squash_by_author_collapses_within_window():
    # Three quick saves by alice within 5 min, then a save by bob
    revs = [
        _rev(1, utc(2026, 5, 1, 10, 0)),
        _rev(2, utc(2026, 5, 1, 10, 1)),
        _rev(3, utc(2026, 5, 1, 10, 4)),
        _rev(4, utc(2026, 5, 1, 10, 30), user="bob@example.com"),
    ]
    events = build_timeline(revs, [], squash_by_author=timedelta(minutes=5))
    rev_ids = [e.revision_id for e in events]
    assert rev_ids == ["3", "4"]   # alice's 1+2 collapsed into 3; bob untouched


def test_squash_does_not_cross_author_boundary():
    revs = [
        _rev(1, utc(2026, 5, 1, 10, 0)),
        _rev(2, utc(2026, 5, 1, 10, 1), user="bob@example.com"),
        _rev(3, utc(2026, 5, 1, 10, 2)),
    ]
    events = build_timeline(revs, [], squash_by_author=timedelta(minutes=5))
    assert [e.revision_id for e in events] == ["1", "2", "3"]


def test_timeline_hash_is_stable_and_changes_on_edits():
    revs = [_rev(1, utc(2026, 5, 1, 10))]
    a = timeline_hash(build_timeline(revs, []))
    b = timeline_hash(build_timeline(revs, []))
    assert a == b
    revs2 = [_rev(1, utc(2026, 5, 1, 11))]   # different time
    c = timeline_hash(build_timeline(revs2, []))
    assert a != c


def test_event_id_distinct_per_kind_for_same_revision():
    revs = [_rev(1, utc(2026, 5, 1, 10))]
    cmt = _comment("AAA", utc(2026, 5, 1, 10))
    events = build_timeline(revs, [cmt])
    ids = {e.event_id for e in events}
    assert len(ids) == len(events)
