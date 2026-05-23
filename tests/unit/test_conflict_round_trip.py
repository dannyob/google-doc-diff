"""Tests for Conflict + ListItem id emit/parse round-trip."""
from __future__ import annotations

from datetime import UTC, datetime

from google_doc_diff.ast.nodes import (
    Conflict,
    Document,
    ListItem,
    Paragraph,
    Run,
    Tab,
)
from google_doc_diff.emit.markdown import emit_document_md
from google_doc_diff.parse.markdown import parse_document_md


def _doc(blocks) -> Document:
    return Document(
        doc_id="d", title="t", revision_id="r", drive_url="u",
        captured_at=datetime(2026, 5, 14, tzinfo=UTC),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="(default)", level=0, blocks=blocks)],
    )


def test_conflict_emits_as_gd_conflict_div():
    """Conflict block renders to a .gd-conflict pandoc div with local/remote sides."""
    c = Conflict(
        conflict_id="c-1",
        local_blocks=[Paragraph(runs=[Run(text="local side")], paragraph_id="p-A")],
        remote_blocks=[Paragraph(runs=[Run(text="remote side")], paragraph_id="p-A")],
    )
    md = emit_document_md(_doc([c]))
    assert ".gd-conflict" in md
    assert "c-1" in md
    assert "local side" in md
    assert "remote side" in md


def test_conflict_round_trips_through_parse():
    """parse(emit(Conflict)) yields a Conflict with the same blocks."""
    c = Conflict(
        conflict_id="c-2",
        local_blocks=[Paragraph(runs=[Run(text="local text")], paragraph_id="p-X")],
        remote_blocks=[Paragraph(runs=[Run(text="remote text")], paragraph_id="p-X")],
    )
    md = emit_document_md(_doc([c]))
    parsed = parse_document_md(md)
    blocks = parsed.tabs[0].blocks
    confs = [b for b in blocks if isinstance(b, Conflict)]
    assert len(confs) == 1
    parsed_c = confs[0]
    assert parsed_c.conflict_id == "c-2"
    assert any("local text" in "".join(r.text for r in b.runs) for b in parsed_c.local_blocks)
    assert any("remote text" in "".join(r.text for r in b.runs) for b in parsed_c.remote_blocks)


# --- ListItem paragraph_id round-trip ------------------------------------


def test_list_item_paragraph_id_emitted_as_inline_span():
    li = ListItem(level=0, kind="bulleted", list_id="L1",
                  runs=[Run(text="hello")], paragraph_id="p-0-3")
    md = emit_document_md(_doc([li]))
    assert "[]{#p-0-3}" in md
    assert "hello" in md


def test_list_item_paragraph_id_round_trips():
    li = ListItem(level=0, kind="bulleted", list_id="L1",
                  runs=[Run(text="hello")], paragraph_id="p-0-3")
    md = emit_document_md(_doc([li]))
    parsed = parse_document_md(md)
    items = [b for b in parsed.tabs[0].blocks if isinstance(b, ListItem)]
    assert len(items) == 1
    assert items[0].paragraph_id == "p-0-3"
    assert items[0].runs[0].text == "hello"


def test_list_item_without_id_has_no_prefix():
    li = ListItem(level=0, kind="bulleted", list_id="L1",
                  runs=[Run(text="plain")])
    md = emit_document_md(_doc([li]))
    assert "[]{#" not in md
    assert "plain" in md
    parsed = parse_document_md(md)
    items = [b for b in parsed.tabs[0].blocks if isinstance(b, ListItem)]
    assert items[0].paragraph_id is None
