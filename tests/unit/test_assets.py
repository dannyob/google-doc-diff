"""Tests for the shared assets module."""

from datetime import UTC, datetime

from google_doc_diff.assets import count_images, has_pua_widgets
from google_doc_diff.ast.nodes import (
    Cell,
    Document,
    Image,
    Paragraph,
    Row,
    Run,
    SmartChip,
    Tab,
    Table,
)


def _doc(blocks):
    return Document(
        doc_id="X", title="T", revision_id="r", drive_url="u",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t", title="(default)", level=0, blocks=blocks)],
    )


def test_count_images_zero_when_no_images():
    assert count_images(_doc([Paragraph(runs=[Run(text="hi")])])) == 0


def test_count_images_finds_inline_images():
    img = Image(image_id="i1", src="https://x/a.png")
    doc = _doc([Paragraph(runs=[Run(text="hi"), img])])
    assert count_images(doc) == 1


def test_count_images_finds_images_in_tables():
    img = Image(image_id="i1", src="https://x/a.png")
    cell = Cell(blocks=[Paragraph(runs=[img])])
    table = Table(rows=[Row(cells=[cell])])
    assert count_images(_doc([table])) == 1


def test_has_pua_widgets_false_when_no_chips():
    assert has_pua_widgets(_doc([Paragraph(runs=[Run(text="hi")])])) is False


def test_has_pua_widgets_true_for_pua_chip():
    chip = SmartChip(kind="reaction", data={"glyph": "U+E907"}, display_text="?")
    doc = _doc([Paragraph(runs=[chip])])
    assert has_pua_widgets(doc) is True


def test_has_pua_widgets_false_for_non_pua_chip():
    chip = SmartChip(kind="person", data={"email": "a@x"}, display_text="A")
    doc = _doc([Paragraph(runs=[chip])])
    assert has_pua_widgets(doc) is False


def test_has_pua_widgets_finds_chip_in_nested_tab():
    chip = SmartChip(kind="reaction", data={"glyph": "U+E907"}, display_text="?")
    inner = Tab(tab_id="t-i", title="i", level=1,
                blocks=[Paragraph(runs=[chip])])
    outer = Tab(tab_id="t-o", title="o", level=0,
                children=[inner], blocks=[Paragraph(runs=[Run(text="x")])])
    doc = Document(
        doc_id="X", title="T", revision_id="r", drive_url="u",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[outer],
    )
    assert has_pua_widgets(doc) is True
