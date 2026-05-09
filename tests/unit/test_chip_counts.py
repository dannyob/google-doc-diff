"""Tests for ast/chip_counts.py — context-aware widget rendering recovery."""

from datetime import UTC, datetime

from google_doc_diff.ast.chip_counts import (
    _classify,
    _parse_chip_rendering,
    attach_widget_renderings,
)
from google_doc_diff.ast.nodes import (
    Document,
    Paragraph,
    Run,
    SmartChip,
    Tab,
)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def make_doc(blocks):
    return Document(
        doc_id="X", title="T", revision_id="r", drive_url="u",
        captured_at=utc(2026, 1, 1), schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t", title="(default)", level=0, blocks=blocks)],
    )


def widget(glyph_hex="U+E907"):
    return SmartChip(kind="reaction", data={"glyph": glyph_hex}, display_text="?")


def test_parse_chip_rendering_thumbsup():
    assert _parse_chip_rendering("(➕ 3)") == ("➕", 3)
    assert _parse_chip_rendering("(➕ 5\\)") == ("➕", 5)
    assert _parse_chip_rendering("not a chip") == (None, None)


def test_classify_vote_kinds():
    assert _classify("(➕ 1)", "➕") == "vote-thumbsup"
    assert _classify("(❤️ 0)", "❤️") == "reaction-heart"
    assert _classify("(🚀 5)", "🚀") == "reaction-rocket"


def test_classify_non_vote_widgets():
    assert _classify("Standard White (#FFFFFF)", None) == "dropdown-color"
    assert _classify("widget", None) == "widget"


def test_attach_pairs_chip_using_preceding_text():
    """Widget after 'idea ' in markdown should be paired by surrounding text,
    not by document-order position."""
    chip = widget()
    doc = make_doc([
        Paragraph(runs=[Run(text="An interesting idea "), chip]),
    ])
    md = "An interesting idea (➕ 3)\n"
    n = attach_widget_renderings(doc, md)
    assert n == 1
    assert chip.data["count"] == 3
    assert chip.data["emoji"] == "➕"
    assert chip.kind == "vote-thumbsup"
    assert chip.display_text == "(➕ 3)"


def test_attach_handles_dropdown_widgets():
    """Widget that renders as dropdown text (not a vote pattern) is captured
    too — kind is set heuristically and display_text is the rendered string."""
    chip = widget()
    doc = make_doc([
        Paragraph(runs=[Run(text="Background: "), chip]),
    ])
    md = "Background: Standard White (#FFFFFF)\n"
    n = attach_widget_renderings(doc, md)
    assert n == 1
    assert chip.display_text == "Standard White (#FFFFFF)"
    assert chip.kind == "dropdown-color"
    assert "count" not in chip.data
    assert "emoji" not in chip.data


def test_attach_skips_when_preceding_text_not_in_markdown():
    chip = widget()
    doc = make_doc([
        Paragraph(runs=[Run(text="Some text "), chip]),
    ])
    md = "Completely different content"
    n = attach_widget_renderings(doc, md)
    assert n == 0
    assert chip.kind == "reaction"   # unchanged
    assert chip.display_text == "?"


def test_attach_paginates_through_repeated_preceding_text():
    """Two chips after the same preceding word get paired with two distinct
    renderings in the markdown (the per-call _seen_positions cache prevents
    both from latching onto the first match)."""
    c1 = widget()
    c2 = widget()
    doc = make_doc([
        Paragraph(runs=[Run(text="Vote: "), c1]),
        Paragraph(runs=[Run(text="Vote: "), c2]),
    ])
    md = "Vote: (➕ 3)\nVote: (❤️ 1)\n"
    n = attach_widget_renderings(doc, md)
    assert n == 2
    assert c1.data["emoji"] == "➕" and c1.data["count"] == 3
    assert c2.data["emoji"] == "❤️" and c2.data["count"] == 1
