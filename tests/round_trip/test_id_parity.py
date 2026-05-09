"""Cross-emitter parity: every stable ID in the AST appears in BOTH outputs.

Catches the failure mode "we emitted the comment in MD but not HTML" (or
vice versa). The set of IDs found in the markdown text must equal the set
found in the html text.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from google_doc_diff.ast.nodes import (
    BookmarkAnchor,
    Cell,
    Comment,
    CommentAnchor,
    Document,
    Footnote,
    FootnoteRef,
    Heading,
    Image,
    ListItem,
    Paragraph,
    Row,
    Run,
    Suggestion,
    SuggestionDel,
    SuggestionIns,
    Tab,
    Table,
)
from google_doc_diff.emit import emit_document_html, emit_document_md


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def _ids_in_text(text: str) -> set[str]:
    """Extract every gd-* stable ID present in text."""
    pat = re.compile(r"\b((?:c-|s-|fn-|t-|bm-|nr-|i-|h-)[A-Za-z0-9_-]+)")
    return set(pat.findall(text))


def _make_richly_attributed_doc() -> Document:
    cmt = Comment(
        comment_id="c-COMMENT1",
        author="alice@example.com",
        created_time=utc(2026, 5, 1, 12, 0),
        modified_time=utc(2026, 5, 1, 12, 0),
        content="needs work\nand more lines so it's not the inline form",
        quoted_text="phrase",
    )
    sug_ins = Suggestion(
        suggestion_id="SUG1", author="alice@example.com",
        created_time=utc(2026, 5, 2, 9, 0), kind="insertion",
    )
    sug_del = Suggestion(
        suggestion_id="SUG2", author="alice@example.com",
        created_time=utc(2026, 5, 2, 9, 0), kind="deletion",
    )
    fn = Footnote(footnote_id="fn-NOTE1",
                  blocks=[Paragraph(runs=[Run(text="footnote body")])])
    tabs = [
        Tab(
            tab_id="t-MAIN",
            title="Main",
            level=0,
            blocks=[
                Heading(level=1, runs=[Run(text="Top")], anchor_id="h-HEAD1"),
                Paragraph(runs=[
                    CommentAnchor(comment_id="c-COMMENT1",
                                  runs=[Run(text="phrase")]),
                    Run(text=" with "),
                    SuggestionIns(suggestion_id="SUG1", runs=[Run(text="ins")]),
                    Run(text=" and "),
                    SuggestionDel(suggestion_id="SUG2", runs=[Run(text="del")]),
                    Run(text=" "),
                    FootnoteRef(footnote_id="fn-NOTE1"),
                    BookmarkAnchor(bookmark_id="bm-MARK1"),
                ]),
                ListItem(level=0, kind="bulleted", list_id="L1",
                         runs=[Run(text="bullet")]),
                Image(image_id="IMAGE1", src="https://x.png", alt="cat"),
                Table(rows=[
                    Row(cells=[Cell(blocks=[Paragraph(runs=[Run(text="t1")])])]),
                ]),
            ],
        )
    ]
    return Document(
        doc_id="DOCID", title="Rich", revision_id="rev",
        drive_url="https://docs.google.com/document/d/DOCID/edit",
        captured_at=utc(2026, 5, 9, 14, 0), schema_version=1,
        last_modifying_user="alice@example.com", source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=tabs,
        comments={"c-COMMENT1": cmt},
        suggestions={"SUG1": sug_ins, "SUG2": sug_del},
        footnotes={"fn-NOTE1": fn},
    )


def test_id_sets_match_across_emitters():
    doc = _make_richly_attributed_doc()
    md = emit_document_md(doc)
    html = emit_document_html(doc)
    md_ids = _ids_in_text(md)
    html_ids = _ids_in_text(html)
    only_in_md = md_ids - html_ids
    only_in_html = html_ids - md_ids
    assert not only_in_md, f"IDs only in markdown: {sorted(only_in_md)}"
    assert not only_in_html, f"IDs only in HTML: {sorted(only_in_html)}"


def test_required_ids_appear_in_both_outputs():
    doc = _make_richly_attributed_doc()
    md = emit_document_md(doc)
    html = emit_document_html(doc)
    expected = {
        "c-COMMENT1",
        "s-SUG1", "s-SUG2",
        "fn-NOTE1",
        "t-MAIN",
        "bm-MARK1",
        "i-IMAGE1",
        "h-HEAD1",
    }
    md_ids = _ids_in_text(md)
    html_ids = _ids_in_text(html)
    missing_md = expected - md_ids
    missing_html = expected - html_ids
    assert not missing_md, f"Markdown missing: {sorted(missing_md)}"
    assert not missing_html, f"HTML missing: {sorted(missing_html)}"
