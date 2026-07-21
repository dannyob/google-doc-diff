from google_doc_diff.tabs import TabRef, parse_tab_refs


def _ac(tab_id, title, index):
    return '{"ty":"ac","d":["%s",[1,"%s"],[%d]]}' % (tab_id, title, index)  # noqa: UP031


def test_parses_id_title_and_order():
    html = "junk" + _ac("t.bbb", "2026-05-06", 1) + "junk" + _ac("t.aaa", "Overview", 0)
    assert parse_tab_refs(html) == [
        TabRef(tab_id="t.aaa", title="Overview", index=0),
        TabRef(tab_id="t.bbb", title="2026-05-06", index=1),
    ]


def test_unescapes_json_in_titles():
    html = _ac("t.aaa", "WBR \\u0026 ORR", 0)
    assert parse_tab_refs(html)[0].title == "WBR & ORR"


def test_title_containing_bracket_brace_is_not_truncated():
    html = _ac("t.aaa", "weird]} title", 0)
    assert parse_tab_refs(html)[0].title == "weird]} title"


def test_duplicate_ops_for_one_tab_collapse():
    html = _ac("t.aaa", "Old", 0) + _ac("t.aaa", "New", 0)
    refs = parse_tab_refs(html)
    assert len(refs) == 1
    assert refs[0].title == "New"


def test_malformed_ops_are_skipped():
    html = '{"ty":"ac","d":["t.aaa"]}' + '{"ty":"ac","d":[' + _ac("t.bbb", "Good", 3)
    assert parse_tab_refs(html) == [TabRef(tab_id="t.bbb", title="Good", index=3)]


def test_no_tabs_returns_empty_list():
    assert parse_tab_refs("<html>nothing here</html>") == []


def test_non_numeric_index_is_skipped_not_raised():
    """int(index_field[0]) used to sit outside the try -- a non-numeric index
    would raise ValueError out of the whole parse instead of just being
    skipped, since this format is reverse-engineered and unconfirmed."""
    bad = '{"ty":"ac","d":["t.aaa",[1,"Bad"],["x"]]}'
    html = bad + _ac("t.bbb", "Good", 3)
    assert parse_tab_refs(html) == [TabRef(tab_id="t.bbb", title="Good", index=3)]
