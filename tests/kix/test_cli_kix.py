"""Tests for CLI kix flag parsing."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from google_doc_diff.cli import _try_kix_enrichment, cli


def _make_mock_doc():
    from google_doc_diff.ast.nodes import Document, Tab

    return Document(
        doc_id="test",
        title="Test",
        revision_id="r1",
        drive_url="https://docs.google.com/document/d/test/edit",
        captured_at=datetime.now(UTC),
        schema_version=1,
        last_modifying_user=None,
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=True,
        tabs=[Tab(tab_id="t.0", title="Tab 1", level=0, blocks=[])],
    )


_FAKE_DOC_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"


@patch("google_doc_diff.cli.load_credentials")
@patch("google_doc_diff.cli.GdocAPI")
@patch("google_doc_diff.cli._pull_rich_document_with_raw")
@patch("google_doc_diff.cli._try_kix_enrichment")
def test_no_kix_flag_skips_enrichment(mock_kix, mock_pull, mock_api_cls, mock_creds):
    """--no-kix should prevent any kix loading."""
    mock_pull.return_value = (_make_mock_doc(), {})

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "pull",
                _FAKE_DOC_ID,
                "--no-kix",
                "--out",
                "test.md",
            ],
        )
        assert result.exit_code == 0, result.output
        mock_kix.assert_not_called()


@patch("google_doc_diff.cli.load_credentials")
@patch("google_doc_diff.cli.GdocAPI")
@patch("google_doc_diff.cli._pull_rich_document_with_raw")
@patch("google_doc_diff.cli._try_kix_enrichment")
def test_kix_enrichment_called_by_default(mock_kix, mock_pull, mock_api_cls, mock_creds):
    """Without --no-kix, enrichment should be attempted."""
    mock_pull.return_value = (_make_mock_doc(), {})
    mock_kix.return_value = None

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "pull",
                _FAKE_DOC_ID,
                "--out",
                "test.md",
            ],
        )
        assert result.exit_code == 0, result.output
        mock_kix.assert_called_once()


class TestSkipDiagnostics:
    """--verbose should report *why* enrichment was skipped, accurately."""

    def test_no_browser_cookie3_is_distinct(self, capsys):
        with patch("google_doc_diff.cli._have_browser_cookie3", return_value=False):
            result = _try_kix_enrichment(_make_mock_doc(), "doc1", verbose=True)
        assert result is None
        assert "browser-cookie3" in capsys.readouterr().err

    def test_no_cookies_found(self, capsys):
        with (
            patch("google_doc_diff.cli._have_browser_cookie3", return_value=True),
            patch("google_doc_diff.kix.auth.resolve_cookie_path", return_value=None),
        ):
            result = _try_kix_enrichment(_make_mock_doc(), "doc1", verbose=True)
        assert result is None
        assert "no Chrome cookies found" in capsys.readouterr().err

    def test_cookies_present_but_unauthorized(self, capsys):
        with (
            patch("google_doc_diff.cli._have_browser_cookie3", return_value=True),
            patch(
                "google_doc_diff.kix.auth.resolve_cookie_path",
                return_value=Path("/some/Cookies"),
            ),
            patch("google_doc_diff.kix.load_kix_session", return_value=None),
        ):
            result = _try_kix_enrichment(_make_mock_doc(), "doc1", verbose=True)
        assert result is None
        err = capsys.readouterr().err
        assert "not authorized" in err
        assert "account" in err

    def test_silent_when_not_verbose(self, capsys):
        with patch("google_doc_diff.cli._have_browser_cookie3", return_value=False):
            _try_kix_enrichment(_make_mock_doc(), "doc1", verbose=False)
        assert capsys.readouterr().err == ""
