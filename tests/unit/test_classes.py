"""Tests for styles/classes.py."""

from google_doc_diff.ast.nodes import StyleDescriptor
from google_doc_diff.styles.classes import (
    NAMED_STYLE_CLASSES,
    is_default_for_named_style,
    list_class_for,
    named_paragraph_class,
    synthesize_inline_class,
)


def test_named_paragraph_class_covers_all_nine_types():
    expected = {
        "NORMAL_TEXT", "TITLE", "SUBTITLE",
        "HEADING_1", "HEADING_2", "HEADING_3",
        "HEADING_4", "HEADING_5", "HEADING_6",
    }
    assert set(NAMED_STYLE_CLASSES.keys()) == expected


def test_named_paragraph_class_returns_known_names():
    assert named_paragraph_class("HEADING_1") == "gd-heading-1"
    assert named_paragraph_class("TITLE") == "gd-title"
    assert named_paragraph_class("SUBTITLE") == "gd-subtitle"
    assert named_paragraph_class("NORMAL_TEXT") == "gd-normal"


def test_named_paragraph_class_returns_none_for_unknown():
    assert named_paragraph_class("WHATEVER") is None


def test_is_default_for_named_style_distinguishes_title_subtitle():
    assert is_default_for_named_style("HEADING_1") is True
    assert is_default_for_named_style("NORMAL_TEXT") is True
    assert is_default_for_named_style("TITLE") is False
    assert is_default_for_named_style("SUBTITLE") is False


def test_synthesize_inline_class_empty_descriptor_returns_none():
    assert synthesize_inline_class(StyleDescriptor()) is None


def test_synthesize_inline_class_is_deterministic():
    s = StyleDescriptor(bold=True, foreground_color="#FF0000")
    a = synthesize_inline_class(s)
    b = synthesize_inline_class(s)
    assert a == b
    assert a is not None
    assert a.startswith("gd-style-")
    assert len(a) == len("gd-style-") + 8


def test_synthesize_inline_class_distinguishes_descriptors():
    a = synthesize_inline_class(StyleDescriptor(bold=True))
    b = synthesize_inline_class(StyleDescriptor(italic=True))
    assert a != b


def test_synthesize_inline_class_collapses_identical_descriptors():
    a = synthesize_inline_class(StyleDescriptor(bold=True, font_size_pt=14.0))
    b = synthesize_inline_class(StyleDescriptor(bold=True, font_size_pt=14.0))
    assert a == b


def test_list_class_for_is_deterministic_and_short():
    a = list_class_for("kix.abc123")
    b = list_class_for("kix.abc123")
    assert a == b
    assert a.startswith("gd-list-")
    assert len(a) == len("gd-list-") + 6


def test_list_class_for_distinguishes_different_ids():
    assert list_class_for("kix.A") != list_class_for("kix.B")
