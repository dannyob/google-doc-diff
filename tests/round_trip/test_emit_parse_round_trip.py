"""Round-trip property: emit -> parse -> emit yields byte-identical output.

We start from a hand-built Document AST (not from real Doc data) so this
test fully owns its inputs. The property asserted is:

    emit(parse(emit(ast))) == emit(ast)

i.e. our emitter and parser agree on a canonical form, even if the parser
is lossy in ways the emitter can't observe. (True `parse(emit(ast)) == ast`
is the stronger goal; we approach it incrementally.)
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    ListItem,
    Paragraph,
    Run,
    StyleDescriptor,
    Tab,
)
from google_doc_diff.emit.markdown import emit_document_md
from google_doc_diff.parse.markdown import parse_document_md


def _wrap(blocks, gdoc_state=None) -> Document:
    return Document(
        doc_id="d1", title="t", revision_id="r1",
        drive_url="https://docs.example/d/d1/edit",
        captured_at=datetime(2026, 5, 14, tzinfo=UTC),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="(default)", level=0, blocks=blocks)],
        gdoc_state=gdoc_state or {},
    )


_PLAIN = [
    Paragraph(runs=[Run(text="Hello world.")]),
]

_HEADINGS = [
    Heading(level=1, runs=[Run(text="Top")]),
    Paragraph(runs=[Run(text="Body of top.")]),
    Heading(level=2, runs=[Run(text="Sub")]),
    Paragraph(runs=[Run(text="Body of sub.")]),
]

_LIST = [
    ListItem(level=0, kind="bulleted", list_id="l-1", runs=[Run(text="one")]),
    ListItem(level=0, kind="bulleted", list_id="l-1", runs=[Run(text="two")]),
    ListItem(level=0, kind="bulleted", list_id="l-1", runs=[Run(text="three")]),
]

_INLINE_FORMATTING = [
    Paragraph(runs=[
        Run(text="bold", formatting=StyleDescriptor(bold=True)),
        Run(text=" "),
        Run(text="italic", formatting=StyleDescriptor(italic=True)),
        Run(text=" "),
        Run(text="strike", formatting=StyleDescriptor(strikethrough=True)),
        Run(text=" plain."),
    ]),
]

_IDENTIFIED_PARAGRAPH = [
    Paragraph(runs=[Run(text="With an id.")], paragraph_id="p-abcdef"),
]

_GDOC_STATE = {
    "base_revision": 71,
    "model_version": 142,
    "signatures": {"kix.x": "AastPo9..."},
}


CASES = [
    ("plain_paragraphs", _PLAIN, {}),
    ("with_headings", _HEADINGS, {}),
    ("bulleted_list", _LIST, {}),
    ("inline_formatting", _INLINE_FORMATTING, {}),
    ("identified_paragraph", _IDENTIFIED_PARAGRAPH, {}),
    ("populated_gdoc_state", _PLAIN, _GDOC_STATE),
]


@pytest.mark.parametrize("name,blocks,gdoc_state", CASES, ids=[c[0] for c in CASES])
def test_emit_parse_emit_byte_identical(name, blocks, gdoc_state):
    doc = _wrap(blocks, gdoc_state)
    md1 = emit_document_md(doc)
    doc2 = parse_document_md(md1)
    md2 = emit_document_md(doc2)
    assert md1 == md2, (
        f"second-pass emit diverged for {name!r}\n"
        f"--- first ---\n{md1}\n--- second ---\n{md2}"
    )
