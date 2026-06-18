"""Tests for top-level CLI commands."""

import json
import os
from datetime import UTC, datetime
from unittest import mock

import click
import pytest
from click.testing import CliRunner

from google_doc_diff.cli import (
    _can_reconcile,
    _slugify,
    _strip_volatile_frontmatter,
    cli,
    resolve_doc_target,
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


# --- resolve_doc_target ----------------------------------------------------


def test_resolve_doc_target_bare_id():
    doc_id, path = resolve_doc_target("1aBcDeFGhIjKLMN_example_id_1234567")
    assert doc_id == "1aBcDeFGhIjKLMN_example_id_1234567"
    assert path is None


def test_resolve_doc_target_url():
    url = "https://docs.google.com/document/d/1aBcDeFGhIjKLMN/edit?tab=t.0"
    doc_id, path = resolve_doc_target(url)
    assert doc_id == "1aBcDeFGhIjKLMN"
    assert path is None


def test_resolve_doc_target_md_file_reads_frontmatter_doc_id(tmp_path):
    f = tmp_path / "my-notes.md"
    f.write_text(
        "---\n"
        "doc_id: 1aBcExample\n"
        "title: My Notes\n"
        "---\n"
        "body\n"
    )
    doc_id, path = resolve_doc_target(str(f))
    assert doc_id == "1aBcExample"
    assert path == f


def test_resolve_doc_target_quoted_doc_id_value(tmp_path):
    """YAML often quotes the doc_id; the resolver strips the quotes."""
    f = tmp_path / "q.md"
    f.write_text("---\ndoc_id: '1aBcQuoted'\n---\nbody\n")
    doc_id, _ = resolve_doc_target(str(f))
    assert doc_id == "1aBcQuoted"


def test_resolve_doc_target_md_file_without_doc_id_raises(tmp_path):
    f = tmp_path / "nope.md"
    f.write_text("---\ntitle: Just A Title\n---\nbody\n")
    with pytest.raises(click.ClickException):
        resolve_doc_target(str(f))


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


def test_reconcile_tolerates_committed_event_changing_author():
    """Drive returns inconsistent lastModifyingUser values for the same
    revision id (spec-documented best-effort behavior). A flaky author
    shouldn't force --restart."""
    t = datetime(2026, 5, 1, tzinfo=UTC)
    saved = _state([_state_evt("rev-1", "prose_change", t, "julian")])
    new = [_ev("rev-1", "prose_change", t, "orjan")]
    ok, _ = _can_reconcile(saved, new)
    assert ok


def test_reconcile_rejects_when_committed_event_changes_timestamp():
    saved = _state([_state_evt(
        "rev-1", "prose_change",
        datetime(2026, 5, 1, tzinfo=UTC), "a@x",
    )])
    new = [_ev("rev-1", "prose_change",
               datetime(2026, 5, 2, tzinfo=UTC), "a@x")]
    ok, reason = _can_reconcile(saved, new)
    assert not ok
    assert "timestamp" in reason


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


# -- chip-counts attachment ---------------------------------------------------


_DOC = "CCCCCCCCCCCCCCCCCCCCCCC"


def test_legacy_state_is_migrated(tmp_path):
    legacy = tmp_path / ".gdoc-replay-state.json"
    legacy.write_text(json.dumps({
        "doc_id": _DOC, "out_path": "x.md", "extract_assets": False,
        "include_comments": True, "since": None, "until": None,
        "timeline_hash": "h", "events": [],
    }))
    runner = CliRunner()
    with mock.patch("google_doc_diff.cli.GdocAPI") as api_cls, \
         mock.patch("google_doc_diff.cli.load_credentials", return_value=mock.Mock()):
        api = api_cls.return_value
        api.list_revisions.return_value = []
        api.list_comments.return_value = []
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            # --resume is required here: without it the command exits with an
            # error ("replay history exists … use --resume") once it finds the
            # migrated legacy state, so the migration path is only exercised
            # under --resume.
            res = runner.invoke(cli, ["replay", _DOC, "--out", "x.md",
                                      "--no-commit", "--resume"])
        finally:
            os.chdir(cwd)
    assert res.exit_code == 0, res.output
    assert (tmp_path / ".gdoc-state" / f"{_DOC}.json").exists()
    # CliRunner (click 8.x) folds stderr into res.output.  The migration notice
    # ("migrated legacy ...") is printed via click.echo(..., err=True).
    # We check for "migrated legacy" (two words) rather than just "migrated",
    # because pytest embeds the test name in tmp_path and the path printed in
    # the normal output already contains the substring "migrated" from
    # "test_legacy_state_is_migrated0".  The two-word phrase only appears when
    # the migration block actually ran.
    assert "migrated legacy" in res.output


def test_state_override_path(tmp_path):
    custom = tmp_path / "custom-state.json"
    runner = CliRunner()
    with mock.patch("google_doc_diff.cli.GdocAPI") as api_cls, \
         mock.patch("google_doc_diff.cli.load_credentials", return_value=mock.Mock()):
        api = api_cls.return_value
        api.list_revisions.return_value = []
        api.list_comments.return_value = []
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            res = runner.invoke(cli, ["replay", _DOC, "--out", "x.md",
                                      "--no-commit", "--state", str(custom)])
        finally:
            os.chdir(cwd)
    assert res.exit_code == 0, res.output
    assert custom.exists()
    assert not (tmp_path / ".gdoc-state" / f"{_DOC}.json").exists()


def test_attach_chip_counts_logs_on_failure(caplog):
    """A failed markdown-export fetch must be reported, not swallowed."""
    import logging

    from google_doc_diff.cli import _attach_chip_counts

    class FailingAPI:
        def list_revisions(self, doc_id):
            raise RuntimeError("export timed out")

    with caplog.at_level(logging.WARNING, logger="google_doc_diff.cli"):
        _attach_chip_counts(FailingAPI(), "doc123", document=None)

    assert any("chip" in r.message.lower() for r in caplog.records)


def test_attach_chip_counts_attaches_renderings():
    from datetime import UTC, datetime

    from google_doc_diff.ast.nodes import Document, Paragraph, Run, SmartChip, Tab
    from google_doc_diff.cli import _attach_chip_counts

    chip = SmartChip(kind="reaction", data={"glyph": "U+E907"}, display_text="?")
    doc = Document(
        doc_id="doc123", title="T", revision_id="r1",
        drive_url="https://docs.google.com/document/d/doc123/edit",
        captured_at=datetime.now(UTC), schema_version=1,
        last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t.0", title="Tab", level=0,
                  blocks=[Paragraph(runs=[Run(text="rate it: "), chip])])],
    )

    class API:
        def list_revisions(self, doc_id):
            return [{"exportLinks": {"text/markdown": "https://export.invalid/md"}}]

        def fetch_revision_export(self, url):
            return "rate it: (➕ 7)\n".encode()

    _attach_chip_counts(API(), "doc123", document=doc)
    assert chip.data.get("count") == 7
    assert chip.data.get("emoji") == "➕"
