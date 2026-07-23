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


EDIT_HTML = (
    '{"ty":"ac","d":["t.aaa",[1,"Overview"],[0]]}'
    '{"ty":"ac","d":["t.bbb",[1,"2026-05-06"],[1]]}'
)


class _FakeAPI:
    def __init__(self, exports, comments=()):
        self._exports = exports
        self._comments = list(comments)
        self.export_calls = []

    def fetch_edit_html(self, doc_id):
        return EDIT_HTML

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
    api = _FakeAPI(_exports())
    api.fetch_edit_html = lambda doc_id: "<html>no ops here</html>"
    with pytest.raises(PerTabError, match="no tabs"):
        build_per_tab_document(api, "DOC123", sleep=lambda _s: None)


def test_duplicate_exports_abort_the_whole_pull():
    api = _FakeAPI({"t.aaa": "# Same\n", "t.bbb": "# Same\n"})
    with pytest.raises(PerTabError):
        build_per_tab_document(api, "DOC123", sleep=lambda _s: None)


def test_on_notice_fires_for_a_dropped_tab_op():
    api = _FakeAPI(_exports())
    api.fetch_edit_html = lambda doc_id: (
        EDIT_HTML + '{"ty":"ac","d":["t.ccc",[1,"Bad"],["x"]]}'
    )
    notices = []
    doc = build_per_tab_document(
        api, "DOC123", sleep=lambda _s: None, on_notice=notices.append
    )
    assert [t.title for t in doc.tabs] == ["Overview", "2026-05-06"]
    assert len(notices) == 1
    assert "skipped 1 unparseable tab op" in notices[0]
    assert "t.ccc" in notices[0]
