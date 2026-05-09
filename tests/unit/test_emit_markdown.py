"""Tests for emit/markdown.py."""

from datetime import UTC, datetime

from google_doc_diff.ast.nodes import (
    Cell,
    CodeBlock,
    Comment,
    CommentAnchor,
    CommentReply,
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
from google_doc_diff.emit import emit_document_md


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def make_doc(
    *,
    tabs=None,
    comments=None,
    suggestions=None,
    footnotes=None,
    named_styles=None,
    css_classes=None,
    title="Test",
):
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


# --- Frontmatter -----------------------------------------------------------


def test_frontmatter_present_with_required_fields():
    doc = make_doc(tabs=single_default_tab([Paragraph(runs=[Run(text="hi")])]))
    md = emit_document_md(doc)
    assert md.startswith("---\n")
    assert "doc_id: DOCID" in md
    assert "title: Test" in md
    assert "source_mode: pull" in md
    assert "comments_preserved: true" in md
    assert "schema_version: 1" in md


# --- Single default tab degrades cleanly ----------------------------------


def test_single_default_tab_no_fenced_div():
    doc = make_doc(tabs=single_default_tab([Heading(level=1, runs=[Run(text="X")])]))
    md = emit_document_md(doc)
    assert ":::" not in md  # no tab fence
    assert "# X" in md


# --- Headings + paragraphs --------------------------------------------------


def test_heading_emits_bare_when_no_overrides():
    doc = make_doc(tabs=single_default_tab([Heading(level=2, runs=[Run(text="Hello")])]))
    md = emit_document_md(doc)
    assert "## Hello" in md


def test_heading_with_anchor_emits_attr():
    doc = make_doc(tabs=single_default_tab([
        Heading(level=1, runs=[Run(text="Hello")], anchor_id="h-XX")
    ]))
    md = emit_document_md(doc)
    assert "# Hello {#h-XX}" in md


def test_paragraph_subtitle_uses_fenced_div():
    doc = make_doc(tabs=single_default_tab([
        Paragraph(runs=[Run(text="A subtitle")], classes=["gd-subtitle"])
    ]))
    md = emit_document_md(doc)
    assert "::: gd-subtitle" in md
    assert "A subtitle" in md


def test_run_bold_italic_strike_link():
    doc = make_doc(tabs=single_default_tab([
        Paragraph(runs=[
            Run(text="bold ", formatting=StyleDescriptor(bold=True)),
            Run(text="italic ", formatting=StyleDescriptor(italic=True)),
            Run(text="strike", formatting=StyleDescriptor(strikethrough=True)),
            Run(text=" ", formatting=StyleDescriptor()),
            Run(text="link", formatting=StyleDescriptor(link_url="https://example.com")),
        ])
    ]))
    md = emit_document_md(doc)
    assert "**bold **" in md
    assert "*italic *" in md
    assert "~~strike~~" in md
    assert "[link](https://example.com)" in md


def test_run_with_inline_override_class():
    doc = make_doc(tabs=single_default_tab([
        Paragraph(runs=[
            Run(text="fancy", formatting=StyleDescriptor(font_family="Source Code Pro"))
        ])
    ]))
    md = emit_document_md(doc)
    assert "[fancy]{.gd-style-" in md


# --- Lists ----------------------------------------------------------------


def test_bulleted_list_indentation_per_level():
    doc = make_doc(tabs=single_default_tab([
        ListItem(level=0, kind="bulleted", list_id="L1", runs=[Run(text="top")]),
        ListItem(level=1, kind="bulleted", list_id="L1", runs=[Run(text="nested")]),
    ]))
    md = emit_document_md(doc)
    assert "- top" in md
    assert "  - nested" in md


def test_ordered_list():
    doc = make_doc(tabs=single_default_tab([
        ListItem(level=0, kind="ordered", list_id="L2", runs=[Run(text="one")]),
        ListItem(level=0, kind="ordered", list_id="L2", runs=[Run(text="two")]),
    ]))
    md = emit_document_md(doc)
    assert "1. one" in md
    assert "1. two" in md


# --- Tables ---------------------------------------------------------------


def test_simple_pipe_table():
    def cell(txt):
        return Cell(blocks=[Paragraph(runs=[Run(text=txt)])])
    doc = make_doc(tabs=single_default_tab([
        Table(rows=[
            Row(cells=[cell("Header A"), cell("Header B")]),
            Row(cells=[cell("a1"), cell("b1")]),
            Row(cells=[cell("a2"), cell("b2")]),
        ])
    ]))
    md = emit_document_md(doc)
    assert "| Header A | Header B |" in md
    assert "| --- | --- |" in md
    assert "| a1 | b1 |" in md


def test_table_with_colspan_falls_back_to_html():
    doc = make_doc(tabs=single_default_tab([
        Table(rows=[
            Row(cells=[
                Cell(blocks=[Paragraph(runs=[Run(text="merged")])], colspan=2),
            ]),
            Row(cells=[
                Cell(blocks=[Paragraph(runs=[Run(text="a")])]),
                Cell(blocks=[Paragraph(runs=[Run(text="b")])]),
            ]),
        ])
    ]))
    md = emit_document_md(doc)
    assert "<table>" in md
    assert 'colspan="2"' in md


# --- Comments -------------------------------------------------------------


def test_short_comment_emits_inline_note():
    cmt = Comment(
        comment_id="c-AAA1",
        author="alice@example.com",
        created_time=utc(2026, 5, 1, 12, 0),
        modified_time=utc(2026, 5, 1, 12, 0),
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
    md = emit_document_md(doc)
    assert "[phrase]{.gd-cmt-anchor #c-AAA1}^[" in md
    assert "alice@example.com" in md
    # No reference-style footnote since this was inline.
    assert "[^c-AAA1]" not in md


def test_long_comment_emits_reference_footnote():
    cmt = Comment(
        comment_id="c-AAA1",
        author="alice@example.com",
        created_time=utc(2026, 5, 1, 12, 0),
        modified_time=utc(2026, 5, 1, 12, 0),
        content="needs the auth section",
        quoted_text="unfinished",
        replies=[CommentReply(reply_id="r-1", author="bob@example.com",
                              created_time=utc(2026, 5, 2),
                              modified_time=utc(2026, 5, 2),
                              content="agreed")],
    )
    doc = make_doc(
        tabs=single_default_tab([
            Paragraph(runs=[CommentAnchor(comment_id="c-AAA1",
                                          runs=[Run(text="unfinished")])])
        ]),
        comments={"c-AAA1": cmt},
    )
    md = emit_document_md(doc)
    assert "[unfinished]{.gd-cmt-anchor #c-AAA1}[^c-AAA1]" in md
    assert "[^c-AAA1]: ::: {.gd-comment" in md
    assert "**bob@example.com**" in md
    assert "agreed" in md


# --- Suggestions ----------------------------------------------------------


def test_insertion_suggestion_emits_critic_markup():
    sug = Suggestion(
        suggestion_id="XYZ1", author="alice@",
        created_time=utc(2026, 5, 2, 9, 0),
        kind="insertion",
    )
    doc = make_doc(
        tabs=single_default_tab([
            Paragraph(runs=[
                Run(text="prefix "),
                SuggestionIns(suggestion_id="XYZ1", runs=[Run(text="new text")]),
            ])
        ]),
        suggestions={"XYZ1": sug},
    )
    md = emit_document_md(doc)
    assert "{++new text++}[^s-XYZ1]" in md
    assert "[^s-XYZ1]: ::: {.gd-suggestion" in md
    assert 'data-kind="insertion"' in md


def test_replacement_suggestion_emits_substitution():
    sug = Suggestion(
        suggestion_id="REPL1", author="alice@",
        created_time=utc(2026, 5, 2, 9, 0),
        kind="replacement",
    )
    doc = make_doc(
        tabs=single_default_tab([
            Paragraph(runs=[
                Run(text="ship "),
                SuggestionDel(suggestion_id="REPL1", runs=[Run(text="yesterday")]),
                SuggestionIns(suggestion_id="REPL1", runs=[Run(text="last week")]),
                Run(text=" features"),
            ])
        ]),
        suggestions={"REPL1": sug},
    )
    md = emit_document_md(doc)
    assert "{~~yesterday~>last week~~}[^s-REPL1]" in md


# --- Smart chips ----------------------------------------------------------


def test_smart_chip_person_default_visible_text():
    doc = make_doc(tabs=single_default_tab([
        Paragraph(runs=[
            SmartChip(kind="person", data={"email": "alice@example.com"}),
        ])
    ]))
    md = emit_document_md(doc)
    assert "[@alice@example.com]{.gd-chip" in md
    assert 'data-email="alice@example.com"' in md
    assert 'data-kind="person"' in md


# --- Images -------------------------------------------------------------


def test_image_block_attrs():
    doc = make_doc(tabs=single_default_tab([
        Image(image_id="IMG1", src="https://...png", alt="cat", width_px=400, height_px=200)
    ]))
    md = emit_document_md(doc)
    assert "![cat](https://...png){#i-IMG1 width=400 height=200}" in md


# --- Code blocks --------------------------------------------------------


def test_code_block_with_language():
    doc = make_doc(tabs=single_default_tab([
        CodeBlock(text="print('hi')\n", language="python")
    ]))
    md = emit_document_md(doc)
    assert "```python" in md
    assert "print('hi')" in md


# --- Tabs --------------------------------------------------------------


def test_two_tabs_emit_fenced_divs():
    doc = make_doc(tabs=[
        Tab(tab_id="t-1", title="One", level=0,
            blocks=[Heading(level=1, runs=[Run(text="A")])]),
        Tab(tab_id="t-2", title="Two", level=0,
            blocks=[Heading(level=1, runs=[Run(text="B")])]),
    ])
    md = emit_document_md(doc)
    assert "::: {.gd-tab" in md
    assert 'data-tab-id="t-1"' in md
    assert 'data-title="One"' in md
    assert 'data-tab-id="t-2"' in md


def test_nested_tabs_use_more_colons():
    doc = make_doc(tabs=[
        Tab(tab_id="t-p", title="Parent", level=0,
            children=[Tab(tab_id="t-c", title="Child", level=1, parent_tab_id="t-p",
                          blocks=[Paragraph(runs=[Run(text="inner")])])],
            blocks=[Paragraph(runs=[Run(text="outer")])])
    ])
    md = emit_document_md(doc)
    assert "::: {.gd-tab" in md       # 3-colon outer
    assert ":::: {.gd-tab" in md      # 4-colon inner
    assert "outer" in md
    assert "inner" in md


# --- Determinism ---------------------------------------------------------


def test_emit_is_deterministic():
    doc = make_doc(tabs=single_default_tab([
        Heading(level=1, runs=[Run(text="X")]),
        Paragraph(runs=[
            Run(text="bold ", formatting=StyleDescriptor(bold=True)),
            Run(text="end"),
        ]),
    ]))
    a = emit_document_md(doc)
    b = emit_document_md(doc)
    assert a == b


def test_css_classes_emit_inside_style_block():
    doc = make_doc(
        tabs=single_default_tab([Paragraph(runs=[Run(text="hi")])]),
        css_classes={"gd-style-aaaaaaaa": "color: #FF0000"},
    )
    md = emit_document_md(doc)
    assert "<style>" in md
    assert ".gd-style-aaaaaaaa" in md


def test_no_runs_does_not_crash():
    doc = make_doc(tabs=single_default_tab([Paragraph(runs=[])]))
    md = emit_document_md(doc)
    assert "DOCID" in md
