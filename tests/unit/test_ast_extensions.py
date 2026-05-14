"""Tests for v2 AST extensions (round-trip support).

ParagraphProperties, paragraph_id, expanded StyleDescriptor, typed
VotingChip / Voter, plus the Document.gdoc_state field.
"""
from __future__ import annotations

from google_doc_diff.ast.nodes import (
    Heading,
    Paragraph,
    ParagraphProperties,
    Run,
    StyleDescriptor,
    Voter,
    VotingChip,
)


# --- ParagraphProperties --------------------------------------------------


def test_paragraph_properties_default_all_inherit():
    p = ParagraphProperties()
    assert p.line_height is None
    assert p.space_before_pt is None
    assert p.space_after_pt is None
    assert p.indent_left_pt is None
    assert p.indent_right_pt is None
    assert p.indent_first_line_pt is None
    assert p.alignment is None
    assert p.heading_depth is None
    assert p.keep_with_next is None
    assert p.keep_lines_together is None
    assert p.page_break_before is None
    assert p.direction is None


def test_paragraph_properties_frozen_and_hashable():
    a = ParagraphProperties(line_height=1.15, keep_with_next=True)
    b = ParagraphProperties(line_height=1.15, keep_with_next=True)
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_paragraph_properties_distinct_when_fields_differ():
    a = ParagraphProperties(line_height=1.0)
    b = ParagraphProperties(line_height=1.15)
    assert a != b
    assert hash(a) != hash(b)


# --- paragraph_id field ---------------------------------------------------


def test_paragraph_has_optional_id():
    p = Paragraph(runs=[Run(text="hi")], paragraph_id="p-abc123")
    assert p.paragraph_id == "p-abc123"


def test_paragraph_id_defaults_to_none():
    p = Paragraph(runs=[Run(text="hi")])
    assert p.paragraph_id is None


def test_heading_has_optional_paragraph_id():
    h = Heading(level=1, runs=[Run(text="t")], paragraph_id="h-1")
    assert h.paragraph_id == "h-1"


def test_paragraph_carries_paragraph_properties():
    pp = ParagraphProperties(line_height=1.15)
    p = Paragraph(runs=[Run(text="hi")], paragraph_properties=pp)
    assert p.paragraph_properties.line_height == 1.15


def test_paragraph_properties_default_none_on_block():
    p = Paragraph(runs=[Run(text="hi")])
    assert p.paragraph_properties is None


# --- StyleDescriptor extensions -------------------------------------------


def test_style_descriptor_new_fields_default_none():
    s = StyleDescriptor()
    assert s.vertical_alignment is None
    assert s.small_caps is None
    assert s.weight is None
    assert s.language is None


def test_style_descriptor_existing_fields_still_present():
    s = StyleDescriptor(bold=True, font_family="Arial")
    assert s.bold is True
    assert s.font_family == "Arial"


def test_style_descriptor_with_all_new_fields():
    s = StyleDescriptor(
        vertical_alignment="super",
        small_caps=True,
        weight=700,
        language="en-US",
    )
    assert s.vertical_alignment == "super"
    assert s.small_caps is True
    assert s.weight == 700
    assert s.language == "en-US"
    # frozen / hashable still
    assert {s, StyleDescriptor(
        vertical_alignment="super", small_caps=True, weight=700, language="en-US",
    )} == {s}


# --- VotingChip + Voter ---------------------------------------------------


def test_voter_is_frozen_dataclass():
    v = Voter(obfuscated_id="111128778940913280838")
    assert v.obfuscated_id == "111128778940913280838"
    # immutable
    a = Voter(obfuscated_id="abc")
    b = Voter(obfuscated_id="abc")
    assert a == b and hash(a) == hash(b)


def test_voting_chip_construction():
    chip = VotingChip(
        chip_id="kix.escg9h9fzc85",
        emoji="➕",
        voters=[Voter(obfuscated_id="abc")],
        current_user_voted=True,
        signature="AastPo9...",
    )
    assert chip.chip_id == "kix.escg9h9fzc85"
    assert chip.emoji == "➕"
    assert chip.voters[0].obfuscated_id == "abc"
    assert chip.current_user_voted is True
    assert chip.signature == "AastPo9..."


def test_voting_chip_defaults():
    chip = VotingChip(chip_id="kix.x", emoji="\U0001f680")
    assert chip.voters == []
    assert chip.current_user_voted is False
    assert chip.signature == ""
