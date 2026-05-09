"""Tests for styles/css.py."""

from datetime import UTC, datetime

from google_doc_diff.ast.nodes import Document, StyleDescriptor
from google_doc_diff.styles.css import (
    build_css,
    class_only_rule,
    descriptor_to_css,
    paired_named_rule,
)


def test_descriptor_empty_yields_no_css():
    assert descriptor_to_css(StyleDescriptor()) == ""


def test_descriptor_bold_emits_font_weight():
    assert descriptor_to_css(StyleDescriptor(bold=True)) == "font-weight: 700"
    assert descriptor_to_css(StyleDescriptor(bold=False)) == "font-weight: 400"


def test_descriptor_italic_strike_underline():
    d = StyleDescriptor(italic=True, underline=True, strikethrough=True)
    css = descriptor_to_css(d)
    assert "font-style: italic" in css
    assert "text-decoration: underline line-through" in css


def test_descriptor_font_and_size_int_vs_float():
    css1 = descriptor_to_css(StyleDescriptor(font_family="Arial", font_size_pt=11.0))
    assert 'font-family: "Arial"' in css1
    assert "font-size: 11pt" in css1  # int, no .0
    css2 = descriptor_to_css(StyleDescriptor(font_size_pt=11.5))
    assert "font-size: 11.5pt" in css2


def test_descriptor_colors_hex_emit_directly():
    css = descriptor_to_css(StyleDescriptor(foreground_color="#FF0000", background_color="#FFFF00"))
    assert "color: #FF0000" in css
    assert "background-color: #FFFF00" in css


def test_paired_named_rule_format():
    body = "font-size: 20pt"
    rule = paired_named_rule("h1", "gd-heading-1", body)
    assert rule == "h1, .gd-heading-1 { font-size: 20pt; }"


def test_paired_named_rule_empty_body_returns_empty():
    assert paired_named_rule("h1", "gd-heading-1", "") == ""


def test_class_only_rule_format():
    assert class_only_rule("gd-style-abcd1234", "color: #FF0000") == \
        ".gd-style-abcd1234 { color: #FF0000; }"


def _make_doc(**overrides) -> Document:
    base = dict(
        doc_id="x", title="t", revision_id="r", drive_url="u",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[],
    )
    base.update(overrides)
    return Document(**base)


def test_build_css_emits_paired_rules_for_present_named_styles():
    doc = _make_doc(
        named_styles={
            "HEADING_1": {"font_size_pt": 20.0, "foreground_color": "#1155cc", "bold": True},
            "NORMAL_TEXT": {"font_size_pt": 11.0},
        }
    )
    css = build_css(doc)
    assert "h1, .gd-heading-1 {" in css
    assert "font-size: 20pt" in css
    assert "color: #1155cc" in css
    assert "p, .gd-normal" in css
    assert "font-size: 11pt" in css


def test_build_css_uses_compound_selector_for_title_and_subtitle():
    doc = _make_doc(
        named_styles={
            "TITLE": {"font_size_pt": 28.0, "foreground_color": "#000000"},
            "SUBTITLE": {"font_size_pt": 14.0, "foreground_color": "#666666"},
        }
    )
    css = build_css(doc)
    assert "h1.gd-title { " in css      # not paired comma form
    assert "p.gd-subtitle { " in css


def test_build_css_emits_synthesized_classes_sorted():
    doc = _make_doc(
        css_classes={
            "gd-style-zzzzzzzz": "color: #FF0000",
            "gd-style-aaaaaaaa": "color: #0000FF",
        }
    )
    css = build_css(doc)
    a_idx = css.index("gd-style-aaaaaaaa")
    z_idx = css.index("gd-style-zzzzzzzz")
    assert a_idx < z_idx, "synthesized classes must emit in sorted order"


def test_build_css_skips_named_styles_with_no_descriptor():
    doc = _make_doc(named_styles={})
    assert build_css(doc) == ""
