import subprocess
from datetime import UTC, datetime

from google_doc_diff.replay import git as gitwrap
from google_doc_diff.replay.state import reconstruct_committed_set
from google_doc_diff.replay.timeline import Event


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)


def _ev(kind, ts, *, revision_id=None, comment_id=None, reply_id=None):
    return Event(kind=kind, timestamp=ts, author="a@b",
                 revision_id=revision_id, comment_id=comment_id, reply_id=reply_id)


def test_reconstruct_matches_by_trailer(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "x.md").write_text("v1")
    gitwrap.add([tmp_path / "x.md"], cwd=tmp_path)
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    e1 = _ev("prose_change", ts, revision_id="9245")
    sha1 = gitwrap.commit("prose: revision 9245", author_name="A", author_email="a@b",
                          timestamp=ts, cwd=tmp_path, event_id=e1.event_id)
    (tmp_path / "x.md").write_text("v2")
    gitwrap.add([tmp_path / "x.md"], cwd=tmp_path)
    ts2 = datetime(2026, 1, 2, tzinfo=UTC)
    e2 = _ev("comment_create", ts2, comment_id="c-XYZ6")
    sha2 = gitwrap.commit("comment: c-XYZ6", author_name="A", author_email="a@b",
                          timestamp=ts2, cwd=tmp_path, event_id=e2.event_id)

    # e3 is in the timeline but never committed -> not in the result.
    e3 = _ev("prose_change", datetime(2026, 1, 3, tzinfo=UTC), revision_id="9300")

    result = reconstruct_committed_set([e1, e2, e3], tmp_path)
    assert result == {e1.event_id: sha1, e2.event_id: sha2}


def test_reconstruct_falls_back_to_message_and_date_without_trailer(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "x.md").write_text("v1")
    gitwrap.add([tmp_path / "x.md"], cwd=tmp_path)
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    # Pre-trailer commit: no event_id passed.
    sha = gitwrap.commit("prose: revision 9245", author_name="A", author_email="a@b",
                         timestamp=ts, cwd=tmp_path)
    e1 = _ev("prose_change", ts, revision_id="9245")
    result = reconstruct_committed_set([e1], tmp_path)
    assert result == {e1.event_id: sha}


def test_reconstruct_empty_when_no_repo_history(tmp_path):
    _init_repo(tmp_path)
    e1 = _ev("prose_change", datetime(2026, 1, 1, tzinfo=UTC), revision_id="1")
    assert reconstruct_committed_set([e1], tmp_path) == {}
