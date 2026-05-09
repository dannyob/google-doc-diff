"""Tests for ast/from_google_md.py."""

from datetime import UTC, datetime

from google_doc_diff.ast.from_google_md import build_from_google_md
from google_doc_diff.ast.nodes import (
    Heading,
    ListItem,
    Paragraph,
    Run,
    Table,
)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_simple_heading_and_paragraph():
    md = "# Title\n\nA paragraph.\n"
    doc = build_from_google_md(md, doc_id="X")
    blocks = doc.tabs[0].blocks
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 1
    assert blocks[0].runs[0].text == "Title"
    assert isinstance(blocks[1], Paragraph)
    assert blocks[1].runs[0].text == "A paragraph."


def test_bold_and_italic_in_paragraph():
    md = "Plain **bold** and *italic* text.\n"
    doc = build_from_google_md(md, doc_id="X")
    runs = doc.tabs[0].blocks[0].runs
    bold_run = next(r for r in runs if isinstance(r, Run) and r.formatting.bold)
    italic_run = next(r for r in runs if isinstance(r, Run) and r.formatting.italic)
    assert bold_run.text == "bold"
    assert italic_run.text == "italic"


def test_link_url_attached_to_run():
    md = "See [the docs](https://example.com) for details.\n"
    doc = build_from_google_md(md, doc_id="X")
    runs = doc.tabs[0].blocks[0].runs
    linked = next(r for r in runs if isinstance(r, Run) and r.formatting.link_url)
    assert linked.formatting.link_url == "https://example.com"
    assert linked.text == "the docs"


def test_bulleted_list():
    md = "- one\n- two\n- three\n"
    doc = build_from_google_md(md, doc_id="X")
    items = doc.tabs[0].blocks
    assert all(isinstance(i, ListItem) for i in items)
    assert [i.runs[0].text for i in items] == ["one", "two", "three"]
    assert all(i.kind == "bulleted" for i in items)


def test_ordered_list():
    md = "1. first\n2. second\n"
    doc = build_from_google_md(md, doc_id="X")
    items = doc.tabs[0].blocks
    assert all(i.kind == "ordered" for i in items)


def test_nested_list_items_preserve_levels():
    md = "- top\n  - nested\n  - nested2\n- back\n"
    doc = build_from_google_md(md, doc_id="X")
    items = doc.tabs[0].blocks
    assert items[0].level == 0 and items[0].runs[0].text == "top"
    assert items[1].level == 1 and items[1].runs[0].text == "nested"
    assert items[3].level == 0 and items[3].runs[0].text == "back"


def test_pipe_table_two_rows():
    md = (
        "| A | B |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n"
    )
    doc = build_from_google_md(md, doc_id="X")
    t = doc.tabs[0].blocks[0]
    assert isinstance(t, Table)
    assert len(t.rows) == 2
    assert t.rows[0].cells[0].blocks[0].runs[0].text == "A"
    assert t.rows[1].cells[1].blocks[0].runs[0].text == "2"


def test_horizontal_rule():
    from google_doc_diff.ast.nodes import HorizontalRule
    md = "above\n\n---\n\nbelow\n"
    doc = build_from_google_md(md, doc_id="X")
    assert any(isinstance(b, HorizontalRule) for b in doc.tabs[0].blocks)


def test_metadata_passed_through():
    when = utc(2026, 5, 9, 12, 0)
    doc = build_from_google_md(
        "# X\n",
        doc_id="DOC1",
        revision_id="rev42",
        captured_at=when,
        last_modifying_user="alice@example.com",
        source_mode="replay",
    )
    assert doc.doc_id == "DOC1"
    assert doc.revision_id == "rev42"
    assert doc.captured_at == when
    assert doc.last_modifying_user == "alice@example.com"
    assert doc.source_mode == "replay"
    assert doc.comments_preserved is False
    assert doc.suggestions_preserved is False


def test_default_title_falls_back_to_first_heading():
    doc = build_from_google_md("# Top heading\n\npara\n", doc_id="X")
    assert doc.title == "Top heading"


def test_default_title_untitled_when_no_heading():
    doc = build_from_google_md("just text\n", doc_id="X")
    assert doc.title == "(untitled)"


def test_drive_url_computed_when_missing():
    doc = build_from_google_md("hi\n", doc_id="DOCID1")
    assert doc.drive_url == "https://docs.google.com/document/d/DOCID1/edit"
