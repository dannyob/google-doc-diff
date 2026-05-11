"""Tests for top-level CLI commands."""

from datetime import UTC, datetime

from google_doc_diff.cli import (
    _can_reconcile,
    _slugify,
    _strip_volatile_frontmatter,
    cli,
)
from google_doc_diff.replay.state import EventState, ReplayState
from google_doc_diff.replay.timeline import Event


def test_version(runner):
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_no_args_shows_help(runner):
    result = runner.invoke(cli, [])
    assert "Pull Google Docs" in result.output or "Usage:" in result.output


def test_subcommands_present(runner):
    result = runner.invoke(cli, ["--help"])
    assert "pull" in result.output
    assert "diff" in result.output
    assert "revisions" in result.output
    assert "auth" in result.output


def test_auth_subcommands_present(runner):
    result = runner.invoke(cli, ["auth", "--help"])
    assert "login" in result.output
    assert "logout" in result.output
    assert "status" in result.output


def test_slugify_simple():
    assert _slugify("My Document") == "my-document"


def test_slugify_strips_punctuation():
    assert _slugify("A: comprehensive guide!") == "a-comprehensive-guide"


def test_slugify_handles_empty():
    assert _slugify("") == "untitled"


def test_slugify_handles_unicode_fallback():
    # Non-ascii chars become hyphens; result is at least non-empty.
    assert _slugify("你好") == "untitled"


def test_strip_volatile_frontmatter_drops_captured_at_and_revision_id():
    md = (
        "---\n"
        "captured_at: '2026-05-09T08:00:00+00:00'\n"
        "comments_preserved: true\n"
        "doc_id: ABC\n"
        "last_modifying_user: alice@example.com\n"
        "revision_id: rev_xyz\n"
        "title: My Doc\n"
        "---\n"
        "body content\n"
    )
    out = _strip_volatile_frontmatter(md)
    assert "captured_at" not in out
    assert "last_modifying_user" not in out
    assert "revision_id" not in out
    assert "doc_id: ABC" in out
    assert "title: My Doc" in out
    assert "body content" in out


def test_strip_volatile_frontmatter_no_change_for_identical_pulls():
    """Two pulls of the same doc differing only in captured_at compare equal
    after stripping."""
    a = "---\ncaptured_at: '2026-05-09T08:00:00+00:00'\ndoc_id: X\n---\nhello\n"
    b = "---\ncaptured_at: '2026-05-09T09:00:00+00:00'\ndoc_id: X\n---\nhello\n"
    assert _strip_volatile_frontmatter(a) == _strip_volatile_frontmatter(b)


def test_strip_volatile_frontmatter_preserves_real_content_diffs():
    """A title change still shows up after stripping."""
    a = "---\ncaptured_at: '2026-05-09T08:00:00+00:00'\ntitle: Old\n---\nbody\n"
    b = "---\ncaptured_at: '2026-05-09T09:00:00+00:00'\ntitle: New\n---\nbody\n"
    assert _strip_volatile_frontmatter(a) != _strip_volatile_frontmatter(b)


def test_strip_volatile_frontmatter_no_frontmatter_passthrough():
    md = "no frontmatter here\n"
    assert _strip_volatile_frontmatter(md) == md


# --- _can_reconcile ---------------------------------------------------------


def _ev(ev_id, kind, ts, author):
    return Event(kind=kind, timestamp=ts, author=author,
                 revision_id=ev_id.removeprefix("rev-") if ev_id.startswith("rev-") else None,
                 comment_id=ev_id if ev_id.startswith("c-") else None)


def _state_evt(ev_id, kind, ts, author, status="committed"):
    return EventState(id=ev_id, kind=kind, timestamp=ts.isoformat(),
                      author=author, status=status)


def _state(events):
    return ReplayState(doc_id="X", out_path="d.md",
                       extract_assets=False, include_comments=True,
                       since=None, until=None, timeline_hash="sha:old",
                       events=events)


def test_reconcile_accepts_identical_timeline():
    t = datetime(2026, 5, 1, tzinfo=UTC)
    saved = _state([_state_evt("rev-1", "prose_change", t, "a@x")])
    new = [_ev("rev-1", "prose_change", t, "a@x")]
    ok, _ = _can_reconcile(saved, new)
    assert ok


def test_reconcile_accepts_purely_additive_timeline():
    """Old committed events still match; new events appended upstream."""
    t = datetime(2026, 5, 1, tzinfo=UTC)
    saved = _state([_state_evt("rev-1", "prose_change", t, "a@x")])
    new = [
        _ev("rev-1", "prose_change", t, "a@x"),
        _ev("c-new", "comment_create", datetime(2026, 5, 2, tzinfo=UTC), "b@y"),
    ]
    ok, _ = _can_reconcile(saved, new)
    assert ok


def test_reconcile_tolerates_committed_event_vanishing():
    """Drive compaction routinely drops historical revisions; the local
    commit is still the record. Comments may be deleted upstream similarly.
    Neither should require --restart."""
    t = datetime(2026, 5, 1, tzinfo=UTC)
    saved = _state([
        _state_evt("rev-1", "prose_change", t, "a@x"),
        _state_evt("rev-128871", "prose_change",
                   datetime(2026, 5, 2, tzinfo=UTC), "a@x"),
    ])
    new = [_ev("rev-1", "prose_change", t, "a@x")]   # rev-128871 compacted away
    ok, _ = _can_reconcile(saved, new)
    assert ok


def test_reconcile_rejects_when_committed_event_changes_author():
    t = datetime(2026, 5, 1, tzinfo=UTC)
    saved = _state([_state_evt("rev-1", "prose_change", t, "a@x")])
    new = [_ev("rev-1", "prose_change", t, "b@y")]
    ok, reason = _can_reconcile(saved, new)
    assert not ok
    assert "author" in reason


def test_reconcile_ignores_uncommitted_event_drift():
    """Pending/failed events can drift freely — we only enforce that
    already-committed events are unchanged."""
    t = datetime(2026, 5, 1, tzinfo=UTC)
    saved = _state([
        _state_evt("rev-1", "prose_change", t, "a@x", status="committed"),
        _state_evt("c-pending", "comment_create", t, "a@x", status="failed"),
    ])
    new = [_ev("rev-1", "prose_change", t, "a@x")]  # c-pending vanished
    ok, _ = _can_reconcile(saved, new)
    assert ok
