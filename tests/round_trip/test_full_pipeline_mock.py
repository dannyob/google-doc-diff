"""End-to-end pipeline property test against a FakeDocsService.

`emit → parse → diff → apply` against a model docs.documents() backed by
a tiny in-memory list of paragraphs. We don't assert *exact* AST equality
back (the apply layer doesn't yet handle every property), but we do assert
that the FakeDocsService received batchUpdate requests of the expected
shape for each fixture's content.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from google_doc_diff.apply.docs_api import apply as apply_docs_api
from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    Paragraph,
    Run,
    StyleDescriptor,
    Tab,
)
from google_doc_diff.emit.markdown import emit_document_md
from google_doc_diff.ops import diff
from google_doc_diff.parse.markdown import parse_document_md

# --- FakeDocsService -----------------------------------------------------


class FakeDocsService:
    """An in-memory model of a Docs document that obeys batchUpdate.

    Supports the subset of Docs API requests our `translate` emits:
      - insertText { location, text }
      - deleteContentRange { range }
      - updateTextStyle { range, textStyle, fields }
      - updateParagraphStyle { range, paragraphStyle, fields }

    The model is a single string + list of (start, end, kind, style) for
    paragraph boundaries. `materialize_text()` returns the flat doc text
    and `materialize_paragraph_styles()` returns the list of namedStyleTypes.
    """

    def __init__(self):
        # Docs documents start with an implicit empty first paragraph at
        # index 1; index 0 is reserved.
        self._text = "\n"
        self._batches: list[list[dict]] = []
        self._paragraph_styles: list[tuple[int, int, str]] = []

    # google-api shim
    def documents(self):
        return self

    def get(self, *, documentId=None, includeTabsContent=None):
        return _Wrap(self._document_payload())

    def batchUpdate(self, *, documentId, body):
        for req in body["requests"]:
            self._apply_request(req)
        self._batches.append(body["requests"])
        return _Wrap({"documentId": documentId, "replies": []})

    # internal apply

    def _apply_request(self, req: dict) -> None:
        if "insertText" in req:
            r = req["insertText"]
            idx = r["location"]["index"]
            text = r["text"]
            self._text = self._text[:idx] + text + self._text[idx:]
            return
        if "deleteContentRange" in req:
            rg = req["deleteContentRange"]["range"]
            self._text = self._text[: rg["startIndex"]] + self._text[rg["endIndex"]:]
            return
        if "updateParagraphStyle" in req:
            r = req["updateParagraphStyle"]
            ns = r["paragraphStyle"].get("namedStyleType")
            if ns:
                self._paragraph_styles.append(
                    (r["range"]["startIndex"], r["range"]["endIndex"], ns)
                )
            return
        if "updateTextStyle" in req:
            return  # we don't model per-character style, just record receipt

    def _document_payload(self) -> dict:
        # Compute paragraph runs from self._text by splitting on '\n'.
        content = []
        idx = 1
        # Add an opening "section break" element matching Docs' typical shape.
        for chunk in self._text.split("\n"):
            if chunk == "" and idx == len(self._text):
                # Trailing empty after the last newline; skip.
                continue
            length = len(chunk) + 1  # +1 for the newline
            content.append({
                "startIndex": idx,
                "endIndex": idx + length,
                "paragraph": {"elements": []},
            })
            idx += length
        return {
            "documentId": "fake",
            "body": {"content": content},
        }

    # public materializers

    def materialize_text(self) -> str:
        return self._text

    def materialize_paragraph_styles(self) -> list[str]:
        return [ns for *_, ns in self._paragraph_styles]


class _Wrap:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


# --- Pipeline test cases -------------------------------------------------


def _wrap(blocks) -> Document:
    return Document(
        doc_id="d", title="t", revision_id="r",
        drive_url="https://docs.example/d/d/edit",
        captured_at=datetime(2026, 5, 14, tzinfo=UTC),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="(default)", level=0, blocks=blocks)],
    )


_FIXTURES = {
    "plain_paragraphs": [
        Paragraph(runs=[Run(text="One.")], paragraph_id="p-1"),
        Paragraph(runs=[Run(text="Two.")], paragraph_id="p-2"),
    ],
    "headings_and_paragraphs": [
        Heading(level=1, runs=[Run(text="H1 Title")], paragraph_id="h-1"),
        Paragraph(runs=[Run(text="Body of H1.")], paragraph_id="p-1"),
        Heading(level=2, runs=[Run(text="Subsection")], paragraph_id="h-2"),
        Paragraph(runs=[Run(text="Body of H2.")], paragraph_id="p-2"),
    ],
    "inline_formatting": [
        Paragraph(runs=[
            Run(text="bold", formatting=StyleDescriptor(bold=True)),
            Run(text=" and "),
            Run(text="italic", formatting=StyleDescriptor(italic=True)),
        ], paragraph_id="p-1"),
    ],
}


@pytest.mark.parametrize("name", sorted(_FIXTURES))
def test_pipeline_emit_parse_diff_apply(name):
    doc = _wrap(_FIXTURES[name])
    md = emit_document_md(doc)
    parsed = parse_document_md(md)
    plan = diff(_wrap([]), parsed)
    assert plan, f"empty plan for {name}"
    svc = FakeDocsService()
    apply_docs_api(plan, doc_id="fake", service=svc)
    assert svc._batches, f"no batchUpdate calls for {name}"
    # Materialised text should contain every visible text run from the AST.
    text = svc.materialize_text()
    for block in _FIXTURES[name]:
        for r in getattr(block, "runs", []):
            assert r.text in text, f"missing text {r.text!r} in:\n{text!r}"


def test_pipeline_emits_heading_paragraph_styles():
    doc = _wrap(_FIXTURES["headings_and_paragraphs"])
    md = emit_document_md(doc)
    parsed = parse_document_md(md)
    plan = diff(_wrap([]), parsed)
    svc = FakeDocsService()
    apply_docs_api(plan, doc_id="fake", service=svc)
    styles = svc.materialize_paragraph_styles()
    assert "HEADING_1" in styles
    assert "HEADING_2" in styles
