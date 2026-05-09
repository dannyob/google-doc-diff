"""Tests for ast/anchor_comments.py."""

from datetime import UTC, datetime

from google_doc_diff.ast.anchor_comments import anchor_comments
from google_doc_diff.ast.nodes import (
    Comment,
    CommentAnchor,
    Document,
    Heading,
    Paragraph,
    Run,
    Tab,
)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def make_doc(blocks, comments):
    return Document(
        doc_id="X", title="T", revision_id="r", drive_url="u",
        captured_at=utc(2026, 1, 1), schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t-d", title="(default)", level=0, blocks=blocks)],
        comments={c.comment_id: c for c in comments},
    )


def test_anchor_finds_snippet_in_single_run():
    cmt = Comment(
        comment_id="c-A1", author="alice@", created_time=utc(2026, 5, 1),
        modified_time=utc(2026, 5, 1), content="x", quoted_text="middle",
    )
    doc = make_doc([Paragraph(runs=[Run(text="prefix middle suffix")])], [cmt])
    anchor_comments(doc)
    runs = doc.tabs[0].blocks[0].runs
    assert len(runs) == 3
    assert runs[0].text == "prefix "
    assert isinstance(runs[1], CommentAnchor)
    assert runs[1].comment_id == "c-A1"
    assert runs[1].runs[0].text == "middle"
    assert runs[2].text == " suffix"
    assert cmt.orphaned is False


def test_anchor_spans_multiple_runs():
    cmt = Comment(
        comment_id="c-A1", author="alice@", created_time=utc(2026, 5, 1),
        modified_time=utc(2026, 5, 1), content="x", quoted_text="bold and more",
    )
    doc = make_doc(
        [Paragraph(runs=[
            Run(text="start "),
            Run(text="bold and"),
            Run(text=" more text"),
        ])],
        [cmt],
    )
    anchor_comments(doc)
    runs = doc.tabs[0].blocks[0].runs
    anchor = next(r for r in runs if isinstance(r, CommentAnchor))
    joined = "".join(r.text for r in anchor.runs)
    assert joined == "bold and more"


def test_anchor_in_heading():
    cmt = Comment(
        comment_id="c-A1", author="alice@", created_time=utc(2026, 5, 1),
        modified_time=utc(2026, 5, 1), content="x", quoted_text="Section",
    )
    doc = make_doc([Heading(level=1, runs=[Run(text="My Section Title")])], [cmt])
    anchor_comments(doc)
    heading = doc.tabs[0].blocks[0]
    assert any(isinstance(r, CommentAnchor) for r in heading.runs)


def test_orphaned_when_snippet_not_found():
    cmt = Comment(
        comment_id="c-A1", author="alice@", created_time=utc(2026, 5, 1),
        modified_time=utc(2026, 5, 1), content="x", quoted_text="nonexistent",
    )
    doc = make_doc([Paragraph(runs=[Run(text="totally different text")])], [cmt])
    anchor_comments(doc)
    assert cmt.orphaned is True


def test_deleted_comments_skipped():
    cmt = Comment(
        comment_id="c-A1", author="alice@", created_time=utc(2026, 5, 1),
        modified_time=utc(2026, 5, 1), content="x", quoted_text="middle",
        deleted=True,
    )
    doc = make_doc([Paragraph(runs=[Run(text="prefix middle suffix")])], [cmt])
    anchor_comments(doc)
    # Deleted comments don't get anchored, but also don't get marked orphaned.
    assert cmt.orphaned is False
    assert all(not isinstance(r, CommentAnchor) for r in doc.tabs[0].blocks[0].runs)


def test_anchor_in_nested_tab():
    cmt = Comment(
        comment_id="c-A1", author="alice@", created_time=utc(2026, 5, 1),
        modified_time=utc(2026, 5, 1), content="x", quoted_text="hidden",
    )
    inner = Tab(tab_id="t-i", title="i", level=1, parent_tab_id="t-o",
                blocks=[Paragraph(runs=[Run(text="hidden text")])])
    outer = Tab(tab_id="t-o", title="o", level=0, children=[inner],
                blocks=[Paragraph(runs=[Run(text="outer")])])
    doc = Document(
        doc_id="X", title="T", revision_id="r", drive_url="u",
        captured_at=utc(2026, 1, 1), schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[outer], comments={"c-A1": cmt},
    )
    anchor_comments(doc)
    assert cmt.orphaned is False
    assert any(isinstance(r, CommentAnchor) for r in inner.blocks[0].runs)
