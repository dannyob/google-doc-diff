"""Tests for CLI commands."""

from google_doc_diff.cli import cli


def test_version(runner):
    """Test --version flag."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_no_args_shows_help(runner):
    """With no args, click prints help."""
    result = runner.invoke(cli, [])
    assert "Pull Google Docs" in result.output or "Usage:" in result.output
