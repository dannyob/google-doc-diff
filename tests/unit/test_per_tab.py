import pytest

from google_doc_diff.per_tab import PerTabError, build_per_tab_document, validate_tab_exports
from google_doc_diff.tabs import TabRef

REFS = [
    TabRef(tab_id="t.aaa", title="Overview", index=0),
    TabRef(tab_id="t.bbb", title="2026-05-06", index=1),
]


def test_distinct_exports_pass():
    validate_tab_exports({"t.aaa": "# One\n", "t.bbb": "# Two\n"}, REFS)


def test_identical_exports_abort_naming_both_titles():
    """A bad tab id silently returns another tab's content; the hashes collide."""
    with pytest.raises(PerTabError) as exc:
        validate_tab_exports({"t.aaa": "# Same\n", "t.bbb": "# Same\n"}, REFS)
    assert "Overview" in str(exc.value)
    assert "2026-05-06" in str(exc.value)


def test_html_error_page_aborts_naming_the_tab():
    html = '<!DOCTYPE html><html lang="en"><head><script nonce="x">'
    with pytest.raises(PerTabError) as exc:
        validate_tab_exports({"t.aaa": "# One\n", "t.bbb": html}, REFS)
    assert "2026-05-06" in str(exc.value)


def test_markdown_containing_inline_html_is_not_mistaken_for_an_error_page():
    validate_tab_exports(
        {"t.aaa": "# One\n\n<div>inline</div>\n", "t.bbb": "# Two\n"}, REFS
    )


def test_several_empty_tabs_do_not_collide():
    """Empty tabs are ordinary -- a parent tab that only contains child tabs
    has no body of its own. The collision check is looking for exports that
    silently fell back to the *default tab's* content, which is never empty,
    so empty exports are not evidence of that and must not abort the pull."""
    validate_tab_exports({"t.aaa": "", "t.bbb": ""}, REFS)


def test_whitespace_only_exports_do_not_collide():
    validate_tab_exports({"t.aaa": "\n", "t.bbb": "  \n"}, REFS)


def _tab_json(tab_id, title, index, children=()):
    return {
        "tabProperties": {"tabId": tab_id, "title": title, "index": index},
        "childTabs": list(children),
    }


TABS_JSON = [_tab_json("t.aaa", "Overview", 0), _tab_json("t.bbb", "2026-05-06", 1)]


class _FakeAPI:
    def __init__(self, exports, comments=(), tabs=None):
        self._exports = exports
        self._comments = list(comments)
        self._tabs = TABS_JSON if tabs is None else tabs
        self.export_calls = []

    def list_tabs(self, doc_id):
        return self._tabs

    def export_tab_markdown(self, doc_id, tab_id):
        self.export_calls.append(tab_id)
        return self._exports[tab_id]

    def get_document_metadata(self, doc_id):
        return {"title": "Big Doc", "revisionId": "rev9"}

    def list_comments(self, doc_id):
        return self._comments


def _exports():
    return {"t.aaa": "# Overview\n\nIntro prose.\n", "t.bbb": "# Week\n\nWeek prose.\n"}


def test_builds_one_tab_per_ref_in_order_with_prefixed_ids():
    api = _FakeAPI(_exports())
    doc = build_per_tab_document(api, "DOC123", sleep=lambda _s: None)

    assert [t.title for t in doc.tabs] == ["Overview", "2026-05-06"]
    assert [t.tab_id for t in doc.tabs] == ["t-t.aaa", "t-t.bbb"]
    assert api.export_calls == ["t.aaa", "t.bbb"]


def test_child_tabs_are_nested_not_flattened():
    """The /edit scraper could not see child tabs, so every tab came back at
    level 0. The masked API call carries the tree, and emit walks it."""
    tabs = [_tab_json("t.aaa", "Track", 0, [_tab_json("t.bbb", "Session", 0)])]
    api = _FakeAPI(_exports(), tabs=tabs)
    doc = build_per_tab_document(api, "DOC123", sleep=lambda _s: None)

    assert len(doc.tabs) == 1
    parent = doc.tabs[0]
    assert parent.level == 0 and parent.parent_tab_id is None
    (child,) = parent.children
    assert child.title == "Session"
    assert child.level == 1
    assert child.parent_tab_id == "t-t.aaa"
    assert child.tab_id == "t-t.bbb"


def test_child_tabs_are_exported_too():
    tabs = [_tab_json("t.aaa", "Track", 0, [_tab_json("t.bbb", "Session", 0)])]
    api = _FakeAPI(_exports(), tabs=tabs)
    build_per_tab_document(api, "DOC123", sleep=lambda _s: None)
    assert api.export_calls == ["t.aaa", "t.bbb"]


def test_carries_metadata_and_records_lost_fidelity():
    doc = build_per_tab_document(_FakeAPI(_exports()), "DOC123", sleep=lambda _s: None)
    assert doc.title == "Big Doc"
    assert doc.revision_id == "rev9"
    assert doc.source_mode == "pull"
    assert doc.suggestions_preserved is False
    assert doc.comments_preserved is True


def test_attaches_drive_comments():
    comments = [{
        "id": "cmt1",
        "author": {"emailAddress": "a@example.com"},
        "createdTime": "2026-05-06T10:00:00.000Z",
        "modifiedTime": "2026-05-06T10:00:00.000Z",
        "content": "a note",
        "quotedFileContent": {"value": "Intro prose."},
    }]
    doc = build_per_tab_document(
        _FakeAPI(_exports(), comments), "DOC123", sleep=lambda _s: None
    )
    assert "c-cmt1" in doc.comments
    assert doc.comments["c-cmt1"].content == "a note"


def test_reports_progress_per_tab():
    seen = []
    build_per_tab_document(
        _FakeAPI(_exports()), "DOC123", sleep=lambda _s: None,
        on_progress=lambda ref, n, total: seen.append((ref.title, n, total)),
    )
    assert seen == [("Overview", 1, 2), ("2026-05-06", 2, 2)]


def test_delays_between_exports_but_not_before_the_first():
    slept = []
    build_per_tab_document(
        _FakeAPI(_exports()), "DOC123", delay=2.5, sleep=slept.append
    )
    assert slept == [2.5]


def test_no_tabs_found_is_an_error():
    api = _FakeAPI(_exports(), tabs=[])
    with pytest.raises(PerTabError, match="no tabs"):
        build_per_tab_document(api, "DOC123", sleep=lambda _s: None)


def test_duplicate_exports_abort_the_whole_pull():
    api = _FakeAPI({"t.aaa": "# Same\n", "t.bbb": "# Same\n"})
    with pytest.raises(PerTabError):
        build_per_tab_document(api, "DOC123", sleep=lambda _s: None)


def test_on_notice_fires_for_a_tab_with_no_id():
    """A tab the API returned without a tabId cannot be exported. It is left
    out rather than exported as an empty id (which the export endpoint would
    answer with the default tab's content), and the loss is reported."""
    tabs = [*TABS_JSON, {"tabProperties": {"title": "Nameless"}}]
    api = _FakeAPI(_exports(), tabs=tabs)
    notices = []
    doc = build_per_tab_document(
        api, "DOC123", sleep=lambda _s: None, on_notice=notices.append
    )
    assert [t.title for t in doc.tabs] == ["Overview", "2026-05-06"]
    assert len(notices) == 1
    assert "skipped 1 tab" in notices[0]
    assert "Nameless" in notices[0]
