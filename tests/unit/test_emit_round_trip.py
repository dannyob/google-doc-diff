"""Tests for v2 round-trippability extensions to emit/markdown.py.

Three properties under test:

- The frontmatter carries a `gdoc:` namespace when `Document.gdoc_state`
  is non-empty (and omits it cleanly when empty).
- `Paragraph.paragraph_id` / `Heading.paragraph_id` round-trip as pandoc
  attributes on the emitted block.
- `ParagraphProperties` survive emit via `--ot-*` custom properties in the
  inline `<style>` block, attached by class name.
"""
from __future__ import annotations

from datetime import datetime, timezone

import yaml

from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    Paragraph,
    ParagraphProperties,
    Run,
    Tab,
)
from google_doc_diff.emit.markdown import emit_document_md


def _doc(blocks=None, gdoc_state=None) -> Document:
    return Document(
        doc_id="d1", title="t", revision_id="r1", drive_url="u",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="(default)", level=0, blocks=blocks or [])],
        gdoc_state=gdoc_state or {},
    )


def _frontmatter(md: str) -> dict:
    assert md.startswith("---\n"), md[:80]
    end = md.find("\n---\n", 4)
    return yaml.safe_load(md[4:end])


# --- frontmatter gdoc: namespace ------------------------------------------


def test_frontmatter_omits_gdoc_when_state_empty():
    md = emit_document_md(_doc())
    fm = _frontmatter(md)
    assert "gdoc" not in fm


def test_frontmatter_includes_gdoc_namespace_when_state_populated():
    md = emit_document_md(_doc(gdoc_state={"base_revision": 71, "model_version": 142}))
    fm = _frontmatter(md)
    assert fm["gdoc"] == {"base_revision": 71, "model_version": 142}


def test_frontmatter_gdoc_namespace_round_trips_signatures():
    sig = "AastPo9fpBGWDoGREyxqSHrnjtJHj0Goa7iuNRwmDU6dZX+uJg=="
    md = emit_document_md(_doc(gdoc_state={
        "signatures": {"kix.escg9h9fzc85": sig},
    }))
    fm = _frontmatter(md)
    assert fm["gdoc"]["signatures"]["kix.escg9h9fzc85"] == sig


# --- paragraph_id attribute -----------------------------------------------


def test_paragraph_id_emitted_when_set():
    p = Paragraph(runs=[Run(text="Hello world")], paragraph_id="p-abc123")
    md = emit_document_md(_doc(blocks=[p]))
    assert "p-abc123" in md  # appears somewhere


def test_paragraph_id_absent_when_unset():
    p = Paragraph(runs=[Run(text="Hello world")])
    md = emit_document_md(_doc(blocks=[p]))
    # No spurious paragraph_id attribute
    assert "p-" not in md.split("---\n", 2)[-1]  # ignore frontmatter


def test_heading_paragraph_id_emitted_when_set():
    h = Heading(level=1, runs=[Run(text="Title")], paragraph_id="h-xyz")
    md = emit_document_md(_doc(blocks=[h]))
    assert "h-xyz" in md


def test_paragraph_id_with_classes_combines_in_attribute_block():
    p = Paragraph(
        runs=[Run(text="Body")],
        classes=["gd-r-deadbeef"],
        paragraph_id="p-xy",
    )
    md = emit_document_md(_doc(blocks=[p]))
    assert "p-xy" in md
    assert "gd-r-deadbeef" in md


# --- ParagraphProperties -> --ot-* custom properties ----------------------


def test_paragraph_properties_emit_to_css_namespace():
    from google_doc_diff.styles.css import paragraph_props_to_css

    css = paragraph_props_to_css(
        ParagraphProperties(line_height=1.15, keep_with_next=True)
    )
    assert "--ot-line-height: 1.15" in css
    assert "--ot-keep-with-next: true" in css


def test_paragraph_props_to_css_skips_none_fields():
    from google_doc_diff.styles.css import paragraph_props_to_css

    css = paragraph_props_to_css(ParagraphProperties(line_height=1.15))
    assert "--ot-line-height" in css
    assert "--ot-keep-with-next" not in css


def test_paragraph_props_to_css_emits_full_field_set():
    from google_doc_diff.styles.css import paragraph_props_to_css

    pp = ParagraphProperties(
        line_height=1.0,
        space_before_pt=12.0,
        space_after_pt=12.0,
        indent_left_pt=18.0,
        indent_right_pt=0.0,
        indent_first_line_pt=36.0,
        alignment="justify",
        heading_depth=1,
        keep_with_next=True,
        keep_lines_together=False,
        page_break_before=False,
        direction="ltr",
    )
    css = paragraph_props_to_css(pp)
    # spot check that all fields appear (deterministic order is asserted elsewhere)
    for name in (
        "--ot-line-height", "--ot-space-before", "--ot-space-after",
        "--ot-indent-left", "--ot-indent-right", "--ot-indent-first-line",
        "--ot-alignment", "--ot-heading-depth", "--ot-keep-with-next",
        "--ot-keep-lines-together", "--ot-page-break-before", "--ot-direction",
    ):
        assert name in css, f"missing {name} in {css}"


def test_paragraph_props_to_css_deterministic_order():
    from google_doc_diff.styles.css import paragraph_props_to_css

    a = paragraph_props_to_css(
        ParagraphProperties(line_height=1.15, keep_with_next=True)
    )
    b = paragraph_props_to_css(
        ParagraphProperties(keep_with_next=True, line_height=1.15)
    )
    assert a == b


def test_paragraph_props_to_css_handles_bool_float_int_str():
    from google_doc_diff.styles.css import paragraph_props_to_css

    css = paragraph_props_to_css(ParagraphProperties(
        line_height=1.0,        # float
        heading_depth=2,        # int
        keep_with_next=True,    # bool
        alignment="center",     # str
    ))
    assert "--ot-line-height: 1" in css
    assert "--ot-heading-depth: 2" in css
    assert "--ot-keep-with-next: true" in css
    assert "--ot-alignment: center" in css
