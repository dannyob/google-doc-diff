"""Tests for parse/markdown.py — the v2 round-trip parser."""
from __future__ import annotations

from google_doc_diff.ast.nodes import (
    CodeBlock,
    Heading,
    ListItem,
    Paragraph,
)
from google_doc_diff.parse.markdown import (
    parse_body,
    parse_document_md,
    parse_frontmatter,
)

# --- frontmatter ----------------------------------------------------------


def test_parses_minimal_frontmatter():
    md = (
        "---\n"
        "title: T\n"
        "doc_id: d\n"
        "---\n"
        "\n# Hello\n"
    )
    fm, body = parse_frontmatter(md)
    assert fm["title"] == "T"
    assert fm["doc_id"] == "d"
    assert body.startswith("\n# Hello")


def test_frontmatter_missing_returns_empty_dict():
    fm, body = parse_frontmatter("# Hello\n")
    assert fm == {}
    assert body == "# Hello\n"


def test_frontmatter_gdoc_namespace_round_trips():
    md = (
        "---\n"
        "title: T\n"
        "gdoc:\n"
        "  base_revision: 71\n"
        "  signatures:\n"
        "    kix.abc: deadbeef\n"
        "---\n"
        "\nbody\n"
    )
    fm, _ = parse_frontmatter(md)
    assert fm["gdoc"]["base_revision"] == 71
    assert fm["gdoc"]["signatures"]["kix.abc"] == "deadbeef"


# --- block parsing --------------------------------------------------------


def test_parses_h1_followed_by_paragraph():
    blocks = parse_body("# Title\n\nHello world.\n")
    assert isinstance(blocks[0], Heading) and blocks[0].level == 1
    assert blocks[0].runs[0].text == "Title"
    assert isinstance(blocks[1], Paragraph)
    assert blocks[1].runs[0].text == "Hello world."


def test_parses_heading_levels_1_through_6():
    md = "\n".join(f"{'#' * lvl} H{lvl}" for lvl in range(1, 7)) + "\n"
    blocks = parse_body(md)
    levels = [b.level for b in blocks if isinstance(b, Heading)]
    assert levels == [1, 2, 3, 4, 5, 6]


def test_heading_with_anchor_id_round_trips():
    blocks = parse_body("# Title {#h-abc}\n")
    h = blocks[0]
    assert isinstance(h, Heading)
    assert h.anchor_id == "h-abc"


def test_paragraph_with_pandoc_div_attribute_block():
    md = (
        "::: {#p-x .gd-r-deadbeef}\n"
        "Some text.\n"
        ":::\n"
    )
    blocks = parse_body(md)
    [p] = [b for b in blocks if isinstance(b, Paragraph)]
    assert p.paragraph_id == "p-x"
    assert "gd-r-deadbeef" in p.classes
    assert p.runs[0].text == "Some text."


def test_paragraph_id_alone_as_extra_id_in_attrs():
    md = "::: {.gd-r-aa #p-id}\nbody\n:::\n"
    blocks = parse_body(md)
    [p] = [b for b in blocks if isinstance(b, Paragraph)]
    # The attribute parser puts only the first '#id' into `id`; extras into
    # extra_ids. When wrapping a paragraph, paragraph_id wins from either slot.
    assert p.paragraph_id == "p-id"
    assert "gd-r-aa" in p.classes


# --- inline formatting ---------------------------------------------------


def test_bold_italic_strike_inline_runs():
    [p] = [
        b for b in parse_body("**bold** *ital* ~~strk~~ plain\n")
        if isinstance(b, Paragraph)
    ]
    texts = [(r.text, r.formatting) for r in p.runs]
    # Just assert at least one run has each toggle on
    assert any(f.bold for _, f in texts)
    assert any(f.italic for _, f in texts)
    assert any(f.strikethrough for _, f in texts)
    # Combined text matches what a human reads
    assert "".join(t for t, _ in texts) == "bold ital strk plain"


def test_inline_link_attaches_link_url_to_run():
    [p] = [
        b for b in parse_body("Visit [docs](https://example.org/) please.\n")
        if isinstance(b, Paragraph)
    ]
    linked = [r for r in p.runs if r.formatting.link_url]
    assert linked, p.runs
    assert linked[0].text == "docs"
    assert linked[0].formatting.link_url == "https://example.org/"


# --- lists ----------------------------------------------------------------


def test_parses_bulleted_list():
    md = "- one\n- two\n- three\n"
    blocks = parse_body(md)
    items = [b for b in blocks if isinstance(b, ListItem)]
    assert len(items) == 3
    assert items[0].kind == "bulleted"
    assert items[0].runs[0].text == "one"
    assert all(item.list_id == items[0].list_id for item in items)


def test_parses_ordered_list():
    blocks = parse_body("1. first\n1. second\n")
    items = [b for b in blocks if isinstance(b, ListItem)]
    assert len(items) == 2
    assert items[0].kind == "ordered"


# --- code -----------------------------------------------------------------


def test_parses_fenced_code_block():
    md = "```python\nprint('hi')\n```\n"
    [cb] = [b for b in parse_body(md) if isinstance(b, CodeBlock)]
    assert cb.language == "python"
    assert "print('hi')" in cb.text


# --- end-to-end -----------------------------------------------------------


def test_parse_document_md_round_trip_minimal():
    md = (
        "---\n"
        "title: Test\n"
        "doc_id: d1\n"
        "revision_id: r1\n"
        "drive_url: https://docs.example/d/d1/edit\n"
        "captured_at: '2026-05-14T00:00:00+00:00'\n"
        "schema_version: 1\n"
        "last_modifying_user: null\n"
        "source_mode: pull\n"
        "comments_preserved: true\n"
        "suggestions_preserved: true\n"
        "gdoc:\n"
        "  base_revision: 71\n"
        "---\n"
        "\n# Heading\n\nFirst paragraph.\n"
    )
    doc = parse_document_md(md)
    assert doc.doc_id == "d1"
    assert doc.title == "Test"
    assert doc.gdoc_state == {"base_revision": 71}
    [tab] = doc.tabs
    assert len(tab.blocks) == 2
    assert isinstance(tab.blocks[0], Heading)
    assert isinstance(tab.blocks[1], Paragraph)
