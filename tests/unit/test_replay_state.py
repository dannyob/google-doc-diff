"""Tests for replay/state.py."""

from __future__ import annotations

from google_doc_diff.replay.state import (
    EventState,
    ReplayState,
    default_state_path,
    read_state,
    remove_state,
    write_state,
)


def _sample_state(doc_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"):
    return ReplayState(
        doc_id=doc_id, out_path="x.md", extract_assets=False,
        include_comments=True, since=None, until=None, timeline_hash="h",
        events=[EventState(id="rev-1", kind="prose_change",
                           timestamp="2026-01-01T00:00:00+00:00", author="a@b")],
    )


def test_default_state_path_is_per_doc(tmp_path):
    p1 = default_state_path("DOCA", tmp_path)
    p2 = default_state_path("DOCB", tmp_path)
    assert p1 == tmp_path / ".gdoc-state" / "DOCA.json"
    assert p2 == tmp_path / ".gdoc-state" / "DOCB.json"
    assert p1 != p2


def test_write_creates_parent_dir_and_round_trips(tmp_path):
    state = _sample_state()
    path = default_state_path(state.doc_id, tmp_path)
    assert not path.parent.exists()
    write_state(state, path)
    assert path.exists()
    back = read_state(path)
    assert back.doc_id == state.doc_id
    assert [e.id for e in back.events] == ["rev-1"]


def test_read_missing_returns_none(tmp_path):
    assert read_state(default_state_path("NOPE", tmp_path)) is None


def test_two_docs_one_dir_do_not_collide(tmp_path):
    a = _sample_state("DOCA")
    b = _sample_state("DOCB")
    write_state(a, default_state_path("DOCA", tmp_path))
    write_state(b, default_state_path("DOCB", tmp_path))
    assert read_state(default_state_path("DOCA", tmp_path)).doc_id == "DOCA"
    assert read_state(default_state_path("DOCB", tmp_path)).doc_id == "DOCB"


def test_remove_state(tmp_path):
    path = default_state_path("DOCA", tmp_path)
    write_state(_sample_state("DOCA"), path)
    remove_state(path)
    assert not path.exists()
    remove_state(path)  # idempotent


def test_status_default_pending():
    s = _sample_state()
    assert all(e.status == "pending" for e in s.events)


def test_event_state_records_git_sha():
    s = _sample_state()
    s.events[0].status = "committed"
    s.events[0].git_sha = "abc123"
    j = s.to_json()
    s2 = ReplayState.from_json(j)
    assert s2.events[0].status == "committed"
    assert s2.events[0].git_sha == "abc123"
