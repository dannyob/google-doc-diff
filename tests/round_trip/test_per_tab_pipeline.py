from google_doc_diff.cli import _strip_volatile_frontmatter
from google_doc_diff.emit.markdown import emit_document_md
from google_doc_diff.per_tab import build_per_tab_document


def _tab_json(tab_id, title, index, children=()):
    return {
        "tabProperties": {"tabId": tab_id, "title": title, "index": index},
        "childTabs": list(children),
    }


TABS = [_tab_json("t.aaa", "Overview", 0), _tab_json("t.bbb", "2026-05-06", 1)]

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
    def __init__(self, tabs=None):
        self._tabs = TABS if tabs is None else tabs

    def list_tabs(self, doc_id):
        return self._tabs

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


def test_child_tabs_emit_nested_under_their_parent():
    """The /edit scraper flattened nesting, so a sub-tab emitted as a sibling.
    The masked API call knows better, and emit renders the tree."""
    nested = [_tab_json("t.aaa", "Overview", 0, [_tab_json("t.bbb", "2026-05-06", 0)])]
    md = emit_document_md(
        build_per_tab_document(_FakeAPI(nested), "DOC123", sleep=lambda _s: None)
    )

    parent = md.index('data-title="Overview"')
    child = md.index('data-title="2026-05-06"')
    assert parent < child
    assert md.count("::: {.gd-tab") == 2
    # the child's fence must close before the parent's
    assert md.rindex(":::") > child


def test_emission_is_deterministic():
    """The merge layer's emit->parse normalisation depends on this."""
    first = emit_document_md(
        build_per_tab_document(_FakeAPI(), "DOC123", sleep=lambda _s: None)
    )
    second = emit_document_md(
        build_per_tab_document(_FakeAPI(), "DOC123", sleep=lambda _s: None)
    )
    assert _strip_volatile_frontmatter(first) == _strip_volatile_frontmatter(second)
