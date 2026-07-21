"""Shared test fixtures."""

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for tests."""
    return tmp_path


@pytest.fixture
def cli_runner():
    """Create a CLI test runner (alias of `runner` for newer tests)."""
    return CliRunner()


@pytest.fixture
def minimal_document():
    from datetime import UTC, datetime

    from google_doc_diff.ast.nodes import Document, Tab

    return Document(
        doc_id="DOC123",
        title="Big Doc",
        revision_id="rev9",
        drive_url="https://docs.google.com/document/d/DOC123/edit",
        captured_at=datetime(2026, 7, 21, tzinfo=UTC),
        schema_version=1,
        last_modifying_user=None,
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=False,
        tabs=[Tab(tab_id="t-t.aaa", title="Overview", level=0, blocks=[])],
    )
