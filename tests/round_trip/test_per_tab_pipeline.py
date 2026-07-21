from google_doc_diff.cli import _strip_volatile_frontmatter
from google_doc_diff.emit.markdown import emit_document_md
from google_doc_diff.per_tab import build_per_tab_document

EDIT_HTML = (
    '{"ty":"ac","d":["t.aaa",[1,"Overview"],[0]]}'
    '{"ty":"ac","d":["t.bbb",[1,"2026-05-06"],[1]]}'
)

EXPORTS = {
    "t.aaa": "# Overview\n\nIntro prose.\n",
    "t.bbb": "# Week\n\nWeek prose worth quoting.\n",
}

COMMENTS = [{
    "id": "cmt1",
    "author": {"emailAddress": "a@example.com"},
    "createdTime": "2026-05-06T10:00:00.000Z",
    "modifiedTime": "2026-05-06T10:00:00.000Z",
    "content": "a note",
    "quotedFileContent": {"value": "Week prose worth quoting."},
}]


class _FakeAPI:
    def fetch_edit_html(self, doc_id):
        return EDIT_HTML

    def export_tab_markdown(self, doc_id, tab_id):
        return EXPORTS[tab_id]

    def get_document_metadata(self, doc_id):
        return {"title": "Big Doc", "revisionId": "rev9"}

    def list_comments(self, doc_id):
        return COMMENTS


def test_per_tab_pull_emits_one_fenced_div_per_tab_in_order():
    doc = build_per_tab_document(_FakeAPI(), "DOC123", sleep=lambda _s: None)
    md = emit_document_md(doc)

    assert md.index('data-title="Overview"') < md.index('data-title="2026-05-06"')
    assert md.count("::: {.gd-tab") == 2
    assert "Intro prose." in md and "Week prose worth quoting." in md


def test_emission_is_deterministic():
    """The merge layer's emit->parse normalisation depends on this."""
    first = emit_document_md(
        build_per_tab_document(_FakeAPI(), "DOC123", sleep=lambda _s: None)
    )
    second = emit_document_md(
        build_per_tab_document(_FakeAPI(), "DOC123", sleep=lambda _s: None)
    )
    assert _strip_volatile_frontmatter(first) == _strip_volatile_frontmatter(second)
