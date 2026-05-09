"""Tests for AST node dataclasses."""

import dataclasses
from datetime import datetime, timezone

import pytest

from google_doc_diff.ast.nodes import (
    BookmarkAnchor,
    Cell,
    CodeBlock,
    Comment,
    CommentAnchor,
    CommentReply,
    Document,
    EquationBlock,
    Footnote,
    FootnoteRef,
    Heading,
    HorizontalRule,
    Image,
    InlineEquation,
    LineBreak,
    ListItem,
    NamedRangeAnchor,
    PageBreak,
    Paragraph,
    Row,
    Run,
    SectionBreak,
    SmartChip,
    StyleDescriptor,
    Suggestion,
    SuggestionDel,
    SuggestionIns,
    Tab,
    Table,
    TableOfContents,
    Unsupported,
)


# --- Run + StyleDescriptor --------------------------------------------------


def test_styledescriptor_is_hashable_and_frozen():
    s1 = StyleDescriptor(bold=True, italic=False, font_family="Arial", font_size_pt=11.0)
    s2 = StyleDescriptor(bold=True, italic=False, font_family="Arial", font_size_pt=11.0)
    assert s1 == s2
    assert hash(s1) == hash(s2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s1.bold = False


def test_run_carries_text_and_formatting():
    s = StyleDescriptor(bold=True)
    r = Run(text="hello", formatting=s)
    assert r.text == "hello"
    assert r.formatting.bold is True


def test_run_default_formatting_is_empty_descriptor():
    r = Run(text="plain")
    assert r.formatting == StyleDescriptor()


def test_run_equality():
    s = StyleDescriptor(bold=True)
    assert Run(text="x", formatting=s) == Run(text="x", formatting=s)
    assert Run(text="x", formatting=s) != Run(text="y", formatting=s)


# --- Inline wrappers --------------------------------------------------------


def test_comment_anchor_wraps_runs_and_carries_id():
    a = CommentAnchor(comment_id="c-AAA1", runs=[Run(text="phrase")])
    assert a.comment_id == "c-AAA1"
    assert a.runs[0].text == "phrase"


def test_suggestion_ins_and_del_share_id_field_name():
    ins = SuggestionIns(suggestion_id="s-X", runs=[Run(text="new")])
    delete = SuggestionDel(suggestion_id="s-X", runs=[Run(text="old")])
    assert ins.suggestion_id == delete.suggestion_id == "s-X"


def test_footnote_ref_only_carries_id():
    assert FootnoteRef(footnote_id="fn-1").footnote_id == "fn-1"


def test_bookmark_and_named_range_anchors():
    assert BookmarkAnchor(bookmark_id="bm-1").bookmark_id == "bm-1"
    assert NamedRangeAnchor(named_range_id="nr-2").named_range_id == "nr-2"


def test_smart_chip_carries_kind_and_data():
    c = SmartChip(kind="person", data={"email": "alice@example.com"}, display_text="Alice")
    assert c.kind == "person"
    assert c.data["email"] == "alice@example.com"
    assert c.display_text == "Alice"


def test_inline_equation_holds_latex():
    assert InlineEquation(latex="E = mc^2").latex == "E = mc^2"


def test_line_break_singleton_equality():
    assert LineBreak() == LineBreak()


def test_unsupported_inline_carries_kind_and_raw():
    u = Unsupported(kind="weirdElement", raw={"foo": "bar"})
    assert u.kind == "weirdElement"
    assert u.raw == {"foo": "bar"}


# --- Block-level nodes ------------------------------------------------------


def test_heading_carries_level_runs_and_optional_anchor():
    h = Heading(level=2, runs=[Run(text="My H2")], anchor_id="h-AB")
    assert h.level == 2
    assert h.runs[0].text == "My H2"
    assert h.anchor_id == "h-AB"


def test_paragraph_default_classes_empty():
    p = Paragraph(runs=[Run(text="hi")])
    assert p.classes == []


def test_list_item_kind_and_level():
    li = ListItem(level=0, kind="bulleted", list_id="L1", runs=[Run(text="x")])
    assert li.level == 0
    assert li.kind == "bulleted"


def test_table_structure_uses_row_and_cell_wrappers():
    cell = Cell(blocks=[Paragraph(runs=[Run(text="a")])], colspan=1, rowspan=1)
    row = Row(cells=[cell])
    t = Table(rows=[row])
    assert t.rows[0].cells[0].blocks[0].runs[0].text == "a"


def test_image_carries_id_and_dimensions():
    img = Image(image_id="i-IMG1", src="https://...", alt="", width_px=400, height_px=300)
    assert img.image_id == "i-IMG1"


def test_code_block_default_language_none():
    assert CodeBlock(text="print('hi')\n").language is None


def test_singleton_block_types_are_equal_to_themselves():
    assert HorizontalRule() == HorizontalRule()
    assert PageBreak() == PageBreak()
    assert SectionBreak() == SectionBreak()
    assert TableOfContents() == TableOfContents()


def test_equation_block_holds_latex():
    assert EquationBlock(latex="E = mc^2").latex == "E = mc^2"


# --- Cross-cutting collections ---------------------------------------------


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_comment_holds_thread_and_quoted_text():
    c = Comment(
        comment_id="c-AAA1",
        author="alice@example.com",
        created_time=utc(2026, 5, 1, 12, 0),
        modified_time=utc(2026, 5, 1, 12, 0),
        content="needs the auth section",
        quoted_text="unfinished",
        resolved=False,
        deleted=False,
        replies=[
            CommentReply(
                reply_id="r-1",
                author="bob@example.com",
                created_time=utc(2026, 5, 2),
                modified_time=utc(2026, 5, 2),
                content="agreed",
                action=None,
            ),
            CommentReply(
                reply_id="r-2",
                author="alice@example.com",
                created_time=utc(2026, 5, 3),
                modified_time=utc(2026, 5, 3),
                content="done",
                action="resolve",
            ),
        ],
    )
    assert c.comment_id == "c-AAA1"
    assert c.quoted_text == "unfinished"
    assert len(c.replies) == 2
    assert c.replies[1].action == "resolve"


def test_suggestion_kinds():
    s_ins = Suggestion(
        suggestion_id="s-1", author="a@x", created_time=utc(2026, 5, 2),
        kind="insertion", attached_comment_id=None,
    )
    s_del = Suggestion(
        suggestion_id="s-2", author="a@x", created_time=utc(2026, 5, 2),
        kind="deletion", attached_comment_id=None,
    )
    s_repl = Suggestion(
        suggestion_id="s-3", author="a@x", created_time=utc(2026, 5, 2),
        kind="replacement", attached_comment_id="c-AAA1",
    )
    assert s_ins.kind == "insertion"
    assert s_del.kind == "deletion"
    assert s_repl.kind == "replacement"
    assert s_repl.attached_comment_id == "c-AAA1"


def test_footnote_holds_blocks():
    fn = Footnote(footnote_id="fn-1", blocks=[Paragraph(runs=[Run(text="see also")])])
    assert fn.footnote_id == "fn-1"
    assert len(fn.blocks) == 1


# --- Tab + Document --------------------------------------------------------


def test_tab_has_id_title_level_blocks_and_optional_children():
    inner = Tab(tab_id="t-c1", title="child", level=1, parent_tab_id="t-p", blocks=[])
    outer = Tab(tab_id="t-p", title="parent", level=0, parent_tab_id=None,
                children=[inner], blocks=[Heading(level=1, runs=[Run(text="X")])])
    assert outer.children[0].tab_id == "t-c1"
    assert outer.blocks[0].level == 1


def test_document_minimal_construction():
    d = Document(
        doc_id="1aBc",
        title="My Doc",
        revision_id="rev-1",
        drive_url="https://docs.google.com/document/d/1aBc/edit",
        captured_at=utc(2026, 5, 9, 14, 0),
        schema_version=1,
        last_modifying_user="alice@example.com",
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=True,
        tabs=[Tab(tab_id="t-1", title="(default)", level=0, blocks=[
            Paragraph(runs=[Run(text="hi")])
        ])],
    )
    assert d.doc_id == "1aBc"
    assert d.tabs[0].blocks[0].runs[0].text == "hi"
    assert d.comments == {}
    assert d.suggestions == {}
    assert d.footnotes == {}
    assert d.css_classes == {}


def test_document_collections_can_be_populated():
    d = Document(
        doc_id="x", title="t", revision_id="r", drive_url="u",
        captured_at=utc(2026, 1, 1),
        schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[],
    )
    d.comments["c-1"] = Comment(
        comment_id="c-1", author="a@x",
        created_time=utc(2026, 1, 1),
        modified_time=utc(2026, 1, 1),
        content="x",
    )
    assert "c-1" in d.comments


# --- Public import surface --------------------------------------------------


@pytest.mark.parametrize("name", [
    "Document", "Tab",
    "Heading", "Paragraph", "ListItem", "Table", "Row", "Cell",
    "Image", "CodeBlock", "EquationBlock",
    "HorizontalRule", "PageBreak", "SectionBreak", "TableOfContents",
    "Run", "StyleDescriptor",
    "CommentAnchor", "SuggestionIns", "SuggestionDel",
    "FootnoteRef", "BookmarkAnchor", "NamedRangeAnchor",
    "SmartChip", "InlineEquation", "LineBreak",
    "Unsupported",
    "Comment", "CommentReply", "Suggestion", "Footnote",
])
def test_public_export(name):
    import google_doc_diff.ast as ast
    assert hasattr(ast, name), f"google_doc_diff.ast must export {name}"
