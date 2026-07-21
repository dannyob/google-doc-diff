import pytest

from google_doc_diff.per_tab import PerTabError, validate_tab_exports
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
