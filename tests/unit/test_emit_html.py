"""Tests for emit/html.py."""

from datetime import UTC, datetime

from google_doc_diff.ast.nodes import (
    Cell,
    CodeBlock,
    Comment,
    CommentAnchor,
    Document,
    Heading,
    Image,
    ListItem,
    Paragraph,
    Row,
    Run,
    SmartChip,
    StyleDescriptor,
    Suggestion,
    SuggestionDel,
    SuggestionIns,
    Tab,
    Table,
)
from google_doc_diff.emit import emit_document_html


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def make_doc(*, tabs=None, comments=None, suggestions=None, footnotes=None,
             named_styles=None, css_classes=None, title="Test"):
    return Document(
        doc_id="DOCID",
        title=title,
        revision_id="rev1",
        drive_url="https://docs.google.com/document/d/DOCID/edit",
        captured_at=utc(2026, 5, 9, 14, 0),
        schema_version=1,
        last_modifying_user="alice@example.com",
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=True,
        tabs=tabs or [],
        comments=comments or {},
        suggestions=suggestions or {},
        footnotes=footnotes or {},
        named_styles=named_styles or {},
        css_classes=css_classes or {},
    )


def single_default_tab(blocks):
    return [Tab(tab_id="t-default", title="(default)", level=0, blocks=blocks)]


def test_html_doctype_and_meta():
    doc = make_doc(tabs=single_default_tab([Paragraph(runs=[Run(text="hi")])]))
    html = emit_document_html(doc)
    assert html.startswith("<!doctype html>")
    assert '<meta name="gd-doc-id" content="DOCID">' in html
    assert '<meta name="gd-source-mode" content="pull">' in html
    assert "<title>Test</title>" in html


def test_heading_emits_bare_h1():
    doc = make_doc(tabs=single_default_tab([Heading(level=2, runs=[Run(text="Hello")])]))
    html = emit_document_html(doc)
    assert "<h2>Hello</h2>" in html


def test_run_bold_italic_link_chain():
    doc = make_doc(tabs=single_default_tab([
        Paragraph(runs=[
            Run(text="bold", formatting=StyleDescriptor(bold=True)),
            Run(text="italic", formatting=StyleDescriptor(italic=True)),
            Run(text="link", formatting=StyleDescriptor(link_url="https://example.com")),
        ])
    ]))
    html = emit_document_html(doc)
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert '<a href="https://example.com">link</a>' in html


def test_subtitle_paragraph_class():
    doc = make_doc(tabs=single_default_tab([
        Paragraph(runs=[Run(text="A subtitle")], classes=["gd-subtitle"])
    ]))
    html = emit_document_html(doc)
    assert '<p class="gd-subtitle">A subtitle</p>' in html


def test_simple_table():
    def cell(t):
        return Cell(blocks=[Paragraph(runs=[Run(text=t)])])

    doc = make_doc(tabs=single_default_tab([
        Table(rows=[
            Row(cells=[cell("A"), cell("B")]),
            Row(cells=[cell("a1"), cell("b1")]),
        ])
    ]))
    html = emit_document_html(doc)
    assert "<table>" in html
    assert "<td><p>A</p></td>" in html
    assert "<td><p>a1</p></td>" in html


def test_table_colspan():
    doc = make_doc(tabs=single_default_tab([
        Table(rows=[
            Row(cells=[Cell(blocks=[Paragraph(runs=[Run(text="x")])], colspan=2)]),
        ])
    ]))
    html = emit_document_html(doc)
    assert 'colspan="2"' in html


def test_lists_bulleted_and_nested():
    doc = make_doc(tabs=single_default_tab([
        ListItem(level=0, kind="bulleted", list_id="L1", runs=[Run(text="top")]),
        ListItem(level=1, kind="bulleted", list_id="L1", runs=[Run(text="nested")]),
        ListItem(level=0, kind="bulleted", list_id="L1", runs=[Run(text="next")]),
    ]))
    html = emit_document_html(doc)
    assert "<ul><li>top<ul><li>nested</li></ul></li><li>next</li></ul>" in html


def test_ordered_list():
    doc = make_doc(tabs=single_default_tab([
        ListItem(level=0, kind="ordered", list_id="L2", runs=[Run(text="one")]),
        ListItem(level=0, kind="ordered", list_id="L2", runs=[Run(text="two")]),
    ]))
    html = emit_document_html(doc)
    assert "<ol><li>one</li><li>two</li></ol>" in html


def test_comment_anchor_and_aside():
    cmt = Comment(
        comment_id="c-AAA1",
        author="alice@example.com",
        created_time=utc(2026, 5, 1),
        modified_time=utc(2026, 5, 1),
        content="needs work",
        quoted_text="phrase",
    )
    doc = make_doc(
        tabs=single_default_tab([
            Paragraph(runs=[CommentAnchor(comment_id="c-AAA1",
                                          runs=[Run(text="phrase")])])
        ]),
        comments={"c-AAA1": cmt},
    )
    html = emit_document_html(doc)
    assert '<span class="gd-cmt-anchor" data-comment-id="c-AAA1">phrase</span>' in html
    assert '<aside class="gd-comment" id="c-AAA1"' in html
    assert "alice@example.com" in html


def test_suggestion_ins_with_metadata_attrs():
    sug = Suggestion(suggestion_id="X1", author="alice@",
                     created_time=utc(2026, 5, 2, 9, 0), kind="insertion")
    doc = make_doc(
        tabs=single_default_tab([
            Paragraph(runs=[
                SuggestionIns(suggestion_id="X1", runs=[Run(text="new")]),
            ])
        ]),
        suggestions={"X1": sug},
    )
    html = emit_document_html(doc)
    assert '<ins data-suggestion-id="s-X1"' in html
    assert 'data-author="alice@"' in html
    assert 'data-kind="insertion"' in html


def test_image_with_dims():
    doc = make_doc(tabs=single_default_tab([
        Image(image_id="IMG1", src="https://x.png", alt="cat", width_px=100, height_px=50)
    ]))
    html = emit_document_html(doc)
    assert '<img id="i-IMG1" src="https://x.png" alt="cat" width="100" height="50">' in html


def test_smart_chip_person():
    doc = make_doc(tabs=single_default_tab([
        Paragraph(runs=[SmartChip(kind="person", data={"email": "alice@example.com"})])
    ]))
    html = emit_document_html(doc)
    assert 'class="gd-chip gd-chip-person"' in html
    assert 'data-email="alice@example.com"' in html
    assert "@alice@example.com" in html


def test_two_tabs_emit_section():
    doc = make_doc(tabs=[
        Tab(tab_id="t-1", title="One", level=0, blocks=[Heading(level=1, runs=[Run(text="A")])]),
        Tab(tab_id="t-2", title="Two", level=0, blocks=[Heading(level=1, runs=[Run(text="B")])]),
    ])
    html = emit_document_html(doc)
    assert '<section class="gd-tab" data-tab-id="t-1"' in html
    assert '<section class="gd-tab" data-tab-id="t-2"' in html


def test_html_is_deterministic():
    doc = make_doc(tabs=single_default_tab([
        Heading(level=1, runs=[Run(text="X")]),
        Paragraph(runs=[
            Run(text="bold ", formatting=StyleDescriptor(bold=True)),
            Run(text="end"),
        ]),
    ]))
    a = emit_document_html(doc)
    b = emit_document_html(doc)
    assert a == b


def test_code_block_class_language():
    doc = make_doc(tabs=single_default_tab([
        CodeBlock(text="print('hi')", language="python")
    ]))
    html = emit_document_html(doc)
    assert '<pre><code class="language-python">' in html
    assert "print('hi')" in html


def test_replacement_suggestion_pair_shares_id():
    """Both <del> and <ins> for a replacement carry the same data-suggestion-id."""
    sug = Suggestion(suggestion_id="REPL", author="a@x", created_time=utc(2026, 5, 2),
                     kind="replacement")
    doc = make_doc(
        tabs=single_default_tab([
            Paragraph(runs=[
                SuggestionDel(suggestion_id="REPL", runs=[Run(text="old")]),
                SuggestionIns(suggestion_id="REPL", runs=[Run(text="new")]),
            ])
        ]),
        suggestions={"REPL": sug},
    )
    html = emit_document_html(doc)
    assert html.count('data-suggestion-id="s-REPL"') == 2
    assert "<del " in html and "<ins " in html
