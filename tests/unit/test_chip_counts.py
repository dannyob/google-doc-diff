"""Tests for ast/chip_counts.py."""

from datetime import UTC, datetime

from google_doc_diff.ast.chip_counts import (
    attach_counts_to_chips,
    extract_counts_from_markdown,
)
from google_doc_diff.ast.nodes import (
    Document,
    Paragraph,
    Run,
    SmartChip,
    Tab,
)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_extract_counts_finds_thumbsup():
    md = "First idea(➕ 3) was good\nSecond(➕ 1)"
    pairs = extract_counts_from_markdown(md)
    assert pairs == [("vote-thumbsup", 3), ("vote-thumbsup", 1)]


def test_extract_counts_handles_pandoc_escaped_paren():
    md = "Idea(➕ 5\\)\n"
    pairs = extract_counts_from_markdown(md)
    assert pairs == [("vote-thumbsup", 5)]


def test_extract_counts_thumbsdown_too():
    md = "Up(➕ 2) and down(➖ 1)"
    pairs = extract_counts_from_markdown(md)
    assert pairs == [("vote-thumbsup", 2), ("vote-thumbsdown", 1)]


def test_attach_counts_in_document_order():
    chip1 = SmartChip(kind="vote-thumbsup", data={"glyph": "U+E907"}, display_text="➕")
    chip2 = SmartChip(kind="vote-thumbsup", data={"glyph": "U+E907"}, display_text="➕")
    doc = Document(
        doc_id="X", title="T", revision_id="r", drive_url="u",
        captured_at=utc(2026, 1, 1), schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t", title="(default)", level=0, blocks=[
            Paragraph(runs=[Run(text="A "), chip1]),
            Paragraph(runs=[Run(text="B "), chip2]),
        ])],
    )
    md = "A (➕ 3)\nB (➕ 7)"
    n = attach_counts_to_chips(doc, md)
    assert n == 2
    assert chip1.data["count"] == 3
    assert chip2.data["count"] == 7
    assert chip1.display_text == "➕ 3"


def test_attach_counts_with_mismatched_lengths_attaches_what_it_can():
    chip1 = SmartChip(kind="vote-thumbsup", data={"glyph": "U+E907"}, display_text="➕")
    chip2 = SmartChip(kind="vote-thumbsup", data={"glyph": "U+E907"}, display_text="➕")
    doc = Document(
        doc_id="X", title="T", revision_id="r", drive_url="u",
        captured_at=utc(2026, 1, 1), schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t", title="(default)", level=0, blocks=[
            Paragraph(runs=[chip1, chip2]),
        ])],
    )
    md = "(➕ 9)"
    n = attach_counts_to_chips(doc, md)
    assert n == 1
    assert chip1.data["count"] == 9
    assert "count" not in chip2.data
