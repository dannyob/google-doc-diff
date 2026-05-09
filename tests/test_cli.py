"""Tests for top-level CLI commands."""

from google_doc_diff.cli import _slugify, cli


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
