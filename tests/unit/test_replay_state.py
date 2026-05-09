"""Tests for replay/state.py."""

from google_doc_diff.replay.state import (
    EventState,
    ReplayState,
    read_state,
    remove_state,
    state_path,
    write_state,
)


def _make_state():
    return ReplayState(
        doc_id="DOC",
        out_path="x.md",
        extract_assets=False,
        include_comments=True,
        since=None,
        until=None,
        timeline_hash="sha256:abc",
        events=[
            EventState(id="rev-1", kind="prose_change",
                       timestamp="2026-05-01T00:00:00+00:00",
                       author="a@x", revision_id="1"),
            EventState(id="comment_create-c-A", kind="comment_create",
                       timestamp="2026-05-01T01:00:00+00:00",
                       author="a@x", comment_id="c-A"),
        ],
    )


def test_round_trip_through_disk(tmp_path):
    s = _make_state()
    write_state(s, tmp_path)
    loaded = read_state(tmp_path)
    assert loaded is not None
    assert loaded.doc_id == "DOC"
    assert loaded.timeline_hash == "sha256:abc"
    assert len(loaded.events) == 2
    assert loaded.events[0].id == "rev-1"
    assert loaded.events[1].comment_id == "c-A"


def test_state_path_in_cwd(tmp_path):
    assert state_path(tmp_path) == tmp_path / ".gdoc-replay-state.json"


def test_read_state_returns_none_when_missing(tmp_path):
    assert read_state(tmp_path) is None


def test_remove_state_idempotent(tmp_path):
    remove_state(tmp_path)            # no error when missing
    s = _make_state()
    write_state(s, tmp_path)
    assert state_path(tmp_path).exists()
    remove_state(tmp_path)
    assert not state_path(tmp_path).exists()


def test_status_default_pending():
    s = _make_state()
    assert all(e.status == "pending" for e in s.events)


def test_event_state_records_git_sha():
    s = _make_state()
    s.events[0].status = "committed"
    s.events[0].git_sha = "abc123"
    j = s.to_json()
    s2 = ReplayState.from_json(j)
    assert s2.events[0].status == "committed"
    assert s2.events[0].git_sha == "abc123"
