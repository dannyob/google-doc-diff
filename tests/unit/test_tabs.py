"""Tab enumeration from the Docs API `tabs(tabProperties,childTabs(...))` mask.

The tab list used to be scraped out of the `/edit` payload's reverse-engineered
`{"ty":"ac",...}` ops. `documents.get` with a field mask returns the same thing
through the public API, and unlike the scraper it carries child tabs.
"""

from google_doc_diff.tabs import TabRef, tab_refs_from_json, walk_tab_refs


def _tab(tab_id, title, index, children=()):
    return {
        "tabProperties": {"tabId": tab_id, "title": title, "index": index},
        "childTabs": list(children),
    }


def test_returns_top_level_tabs_in_index_order():
    tabs = [_tab("t.bbb", "2026-05-06", 1), _tab("t.aaa", "Overview", 0)]
    assert tab_refs_from_json(tabs) == [
        TabRef(tab_id="t.aaa", title="Overview", index=0),
        TabRef(tab_id="t.bbb", title="2026-05-06", index=1),
    ]


def test_child_tabs_carry_level_and_parent():
    tabs = [_tab("t.aaa", "Track", 0, [_tab("t.bbb", "Session", 0)])]
    (parent,) = tab_refs_from_json(tabs)

    assert parent.level == 0
    assert parent.parent_tab_id is None
    (child,) = parent.children
    assert child == TabRef(
        tab_id="t.bbb", title="Session", index=0, level=1, parent_tab_id="t.aaa"
    )


def test_nesting_is_not_depth_limited():
    tabs = [_tab("t.a", "A", 0, [_tab("t.b", "B", 0, [_tab("t.c", "C", 0)])])]
    (a,) = tab_refs_from_json(tabs)
    (b,) = a.children
    (c,) = b.children
    assert (a.level, b.level, c.level) == (0, 1, 2)
    assert c.parent_tab_id == "t.b"


def test_child_tabs_are_ordered_within_their_parent():
    tabs = [_tab("t.a", "A", 0, [_tab("t.c", "C", 1), _tab("t.b", "B", 0)])]
    (a,) = tab_refs_from_json(tabs)
    assert [c.title for c in a.children] == ["B", "C"]


def test_walk_yields_depth_first_document_order():
    tabs = [
        _tab("t.a", "A", 0, [_tab("t.a1", "A1", 0), _tab("t.a2", "A2", 1)]),
        _tab("t.b", "B", 1),
    ]
    refs = tab_refs_from_json(tabs)
    assert [r.tab_id for r in walk_tab_refs(refs)] == [
        "t.a", "t.a1", "t.a2", "t.b",
    ]


def test_missing_index_falls_back_to_document_order():
    """`tabProperties.index` is omitted for index 0 by some callers; a tab
    without one must keep its position rather than sorting to the front."""
    tabs = [
        {"tabProperties": {"tabId": "t.a", "title": "A"}},
        {"tabProperties": {"tabId": "t.b", "title": "B", "index": 1}},
    ]
    assert [r.tab_id for r in tab_refs_from_json(tabs)] == ["t.a", "t.b"]


def test_no_tabs_returns_empty_list():
    assert tab_refs_from_json([]) == []
    assert tab_refs_from_json(None) == []


def test_tab_without_an_id_is_skipped():
    """A tab we cannot address is useless to the per-tab export path -- it
    must not become a ref with an empty id that exports the default tab."""
    tabs = [{"tabProperties": {"title": "No id"}}, _tab("t.a", "A", 0)]
    assert [r.tab_id for r in tab_refs_from_json(tabs)] == ["t.a"]
