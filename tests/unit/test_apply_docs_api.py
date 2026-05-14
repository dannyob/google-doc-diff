"""Tests for apply/docs_api — translate and apply."""
from __future__ import annotations

from google_doc_diff.apply.docs_api import (
    apply,
    build_block_index_from_docs_document,
    translate,
)
from google_doc_diff.ast.nodes import (
    Heading,
    Paragraph,
    Run,
    StyleDescriptor,
)
from google_doc_diff.ops import (
    ApplyStyle,
    DeleteBlock,
    DeleteRange,
    InsertBlock,
    InsertText,
    OpPlan,
)

# --- translate ------------------------------------------------------------


def test_insert_text_translates_with_block_offset():
    op = InsertText(block_id="p-1", offset=0, text="Hello")
    reqs = translate([op], block_index={"p-1": (100, 105)})
    assert reqs == [{"insertText": {"location": {"index": 100}, "text": "Hello"}}]


def test_insert_text_with_style_emits_update_text_style():
    op = InsertText(
        block_id="p-1", offset=0, text="hi",
        run_style=StyleDescriptor(bold=True),
    )
    reqs = translate([op], block_index={"p-1": (100, 102)})
    assert reqs[0]["insertText"]["text"] == "hi"
    assert reqs[1]["updateTextStyle"]["range"] == {"startIndex": 100, "endIndex": 102}
    assert reqs[1]["updateTextStyle"]["textStyle"]["bold"] is True


def test_delete_range_translates_to_delete_content_range():
    op = DeleteRange(block_id="p-1", start=2, end=7)
    reqs = translate([op], block_index={"p-1": (100, 110)})
    assert reqs == [{"deleteContentRange": {"range": {"startIndex": 102, "endIndex": 107}}}]


def test_unresolvable_block_id_yields_no_request():
    op = InsertText(block_id="missing", offset=0, text="x")
    reqs = translate([op], block_index={})
    assert reqs == []


def test_apply_style_text_scope_emits_update_text_style():
    op = ApplyStyle(
        scope="text", block_id="p-1", start=0, end=4,
        style=StyleDescriptor(italic=True),
    )
    reqs = translate([op], block_index={"p-1": (10, 15)})
    assert reqs[0]["updateTextStyle"]["range"] == {"startIndex": 10, "endIndex": 14}
    assert reqs[0]["updateTextStyle"]["textStyle"]["italic"] is True


def test_insert_block_paragraph_emits_insert_text_with_newline():
    p = Paragraph(runs=[Run(text="Hello")])
    op = InsertBlock(after_id=None, block=p)
    reqs = translate([op], block_index={}, end_of_body=1)
    [insert] = [r for r in reqs if "insertText" in r]
    assert insert["insertText"]["location"]["index"] == 1
    assert insert["insertText"]["text"] == "Hello\n"


def test_insert_block_paragraph_after_existing():
    p = Paragraph(runs=[Run(text="World")])
    op = InsertBlock(after_id="p-prev", block=p)
    reqs = translate([op], block_index={"p-prev": (1, 7)}, end_of_body=7)
    [insert] = [r for r in reqs if "insertText" in r]
    assert insert["insertText"]["location"]["index"] == 7  # right after prev's end


def test_insert_block_heading_emits_paragraph_style():
    h = Heading(level=2, runs=[Run(text="Section")])
    op = InsertBlock(after_id=None, block=h)
    reqs = translate([op], block_index={})
    style_req = next(r for r in reqs if "updateParagraphStyle" in r)
    assert style_req["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_2"
    assert style_req["updateParagraphStyle"]["fields"] == "namedStyleType"


def test_insert_block_paragraph_with_run_styles_emits_update_text_style():
    p = Paragraph(runs=[
        Run(text="bold", formatting=StyleDescriptor(bold=True)),
        Run(text=" plain"),
    ])
    op = InsertBlock(after_id=None, block=p)
    reqs = translate([op], block_index={})
    update_reqs = [r for r in reqs if "updateTextStyle" in r]
    assert update_reqs
    # The bold range covers "bold" (first 4 chars after insert at 1).
    bold_req = next(r for r in update_reqs if r["updateTextStyle"]["textStyle"].get("bold"))
    assert bold_req["updateTextStyle"]["range"] == {"startIndex": 1, "endIndex": 5}


def test_delete_block_translates_to_delete_content_range():
    op = DeleteBlock(block_id="p-1")
    reqs = translate([op], block_index={"p-1": (100, 110)})
    assert reqs == [{"deleteContentRange": {"range": {"startIndex": 100, "endIndex": 110}}}]


# --- block_index ---------------------------------------------------------


def test_build_block_index_from_docs_document_basic():
    doc = {
        "body": {"content": [
            {"startIndex": 1, "endIndex": 7, "paragraph": {}},
            {"startIndex": 7, "endIndex": 13, "paragraph": {}},
        ]},
    }
    idx, end = build_block_index_from_docs_document(doc)
    assert idx == {"p-0": (1, 7), "p-1": (7, 13)}
    assert end == 13


def test_build_block_index_ignores_section_breaks_etc():
    doc = {
        "body": {"content": [
            {"startIndex": 1, "endIndex": 7, "paragraph": {}},
            {"startIndex": 7, "endIndex": 8, "sectionBreak": {}},
            {"startIndex": 8, "endIndex": 13, "paragraph": {}},
        ]},
    }
    idx, end = build_block_index_from_docs_document(doc)
    assert "p-0" in idx and "p-1" in idx
    assert end == 13


# --- apply runner --------------------------------------------------------


class _FakeService:
    """Minimal docs.documents().get() / batchUpdate() stand-in."""

    def __init__(self, doc):
        self._doc = doc
        self.last_requests: list[dict] = []

    def documents(self):  # noqa: D401  (mimics google client shape)
        return self

    def get(self, documentId):  # noqa
        self._last_doc_id = documentId
        return _ExecWrapper(self._doc)

    def batchUpdate(self, *, documentId, body):
        self._last_doc_id = documentId
        self.last_requests = body["requests"]
        return _ExecWrapper({"replies": [], "documentId": documentId})


class _ExecWrapper:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


def test_apply_empty_plan_does_nothing():
    svc = _FakeService(doc={"body": {"content": []}})
    apply(OpPlan(), doc_id="abc", service=svc)
    assert svc.last_requests == []


def test_apply_translates_and_calls_batch_update():
    plan = OpPlan()
    plan.append(InsertBlock(after_id=None, block=Paragraph(runs=[Run(text="Hi")])))
    svc = _FakeService(doc={
        "body": {"content": [{"startIndex": 1, "endIndex": 2, "paragraph": {}}]},
    })
    apply(plan, doc_id="abc", service=svc)
    assert svc.last_requests
    assert svc.last_requests[0]["insertText"]["text"] == "Hi\n"
