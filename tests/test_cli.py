"""Tests for top-level CLI commands."""

from google_doc_diff.cli import _slugify, _strip_volatile_frontmatter, cli


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
