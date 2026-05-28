"""Tests for apply/docs_api — translate and apply."""
from __future__ import annotations

from google_doc_diff.apply.docs_api import (
    apply,
    build_block_index_from_docs_document,
    translate,
)
from google_doc_diff.ast.nodes import (
    Heading,
    ListItem,
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


def test_chained_anonymous_inserts_advance_index():
    """Two InsertBlocks with after_id=None must NOT both insert at index 1.

    Docs API processes batchUpdate requests sequentially, so two inserts at
    index 1 would stack the second one BEFORE the first. The translate
    function must thread a running cursor for inserts at the same anchor.
    """
    p1 = Paragraph(runs=[Run(text="First")])
    p2 = Paragraph(runs=[Run(text="Second")])
    ops = [
        InsertBlock(after_id=None, block=p1),
        InsertBlock(after_id=None, block=p2),
    ]
    reqs = translate(ops, block_index={}, end_of_body=1)
    inserts = [r for r in reqs if "insertText" in r]
    assert inserts[0]["insertText"]["location"]["index"] == 1
    assert inserts[0]["insertText"]["text"] == "First\n"
    # "First\n" is 6 chars, so the next paragraph must land at 1 + 6 = 7.
    assert inserts[1]["insertText"]["location"]["index"] == 7
    assert inserts[1]["insertText"]["text"] == "Second\n"


def test_paragraph_insert_emits_normal_text_named_style():
    """Plain Paragraph inserts must reset namedStyleType to NORMAL_TEXT.

    Otherwise the inserted paragraph inherits whatever named style the
    insert point happens to have (e.g. HEADING_1 from a preceding heading
    insert), which is how the live smoke test ended up with every block
    styled as a heading.
    """
    p = Paragraph(runs=[Run(text="just text")])
    op = InsertBlock(after_id=None, block=p)
    reqs = translate([op], block_index={}, end_of_body=1)
    style_reqs = [r for r in reqs if "updateParagraphStyle" in r]
    assert len(style_reqs) == 1
    assert style_reqs[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"


def test_list_item_insert_emits_bullets_request():
    """ListItem inserts must add createParagraphBullets so they render as bullets."""
    li = ListItem(level=0, kind="bulleted", list_id="L1", runs=[Run(text="item")])
    op = InsertBlock(after_id=None, block=li)
    reqs = translate([op], block_index={}, end_of_body=1)
    bullet_reqs = [r for r in reqs if "createParagraphBullets" in r]
    assert len(bullet_reqs) == 1
    cb = bullet_reqs[0]["createParagraphBullets"]
    # Range must cover the inserted text (5 chars: "item\n").
    assert cb["range"]["startIndex"] == 1
    assert cb["range"]["endIndex"] == 1 + len("item\n")
    assert cb["bulletPreset"].startswith("BULLET_")


def test_ordered_list_item_uses_numbered_preset():
    li = ListItem(level=0, kind="ordered", list_id="L1", runs=[Run(text="step")])
    op = InsertBlock(after_id=None, block=li)
    reqs = translate([op], block_index={}, end_of_body=1)
    bullet_req = next(r for r in reqs if "createParagraphBullets" in r)
    assert bullet_req["createParagraphBullets"]["bulletPreset"].startswith("NUMBERED_")


def test_paragraph_style_emitted_before_text_style():
    """updateParagraphStyle must come before updateTextStyle for the same insert.

    Docs API resets character formatting to the named style's defaults when
    namedStyleType changes. If we emit text styles first, the subsequent
    paragraph style change wipes them out. Witnessed live: bold/italic
    survived translate() but vanished from the rendered doc.
    """
    p = Paragraph(runs=[
        Run(text="hi "),
        Run(text="bold", formatting=StyleDescriptor(bold=True)),
    ])
    op = InsertBlock(after_id=None, block=p)
    reqs = translate([op], block_index={}, end_of_body=1)
    # Strip insertText (always first) and look at the styling order.
    style_keys = [next(iter(r.keys())) for r in reqs if "insertText" not in r]
    para_idx = style_keys.index("updateParagraphStyle")
    text_idx = style_keys.index("updateTextStyle")
    assert para_idx < text_idx, (
        f"updateParagraphStyle (idx {para_idx}) must precede "
        f"updateTextStyle (idx {text_idx}); order was {style_keys}"
    )


def test_list_item_insert_emits_normal_text_named_style():
    """List items, like paragraphs, must reset namedStyleType to NORMAL_TEXT."""
    li = ListItem(level=0, kind="bulleted", list_id="L1", runs=[Run(text="x")])
    op = InsertBlock(after_id=None, block=li)
    reqs = translate([op], block_index={}, end_of_body=1)
    style_reqs = [r for r in reqs if "updateParagraphStyle" in r]
    assert any(
        r["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"
        for r in style_reqs
    )


def test_delete_block_translates_to_delete_content_range():
    op = DeleteBlock(block_id="p-1")
    reqs = translate([op], block_index={"p-1": (100, 110)})
    assert reqs == [{"deleteContentRange": {"range": {"startIndex": 100, "endIndex": 110}}}]


# --- block_index ---------------------------------------------------------


def test_build_block_index_uses_ast_paragraph_ids():
    """block_index keys must MATCH the paragraph_ids stamped by from_docs_json.

    Otherwise translate() looks up `p-0-3` (the AST id) in an index that only
    has `p-3`, silently drops every op, and apply() returns success with no
    requests sent — the failure mode that masked the create-from-scratch
    smoke test's first surgical-edit attempt.
    """
    doc = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [
            {"startIndex": 1, "endIndex": 7,
             "paragraph": {"elements": [{"textRun": {"content": "first\n"}}]}},
            {"startIndex": 7, "endIndex": 13,
             "paragraph": {"elements": [{"textRun": {"content": "second\n"}}]}},
        ]},
    }
    idx, end = build_block_index_from_docs_document(doc)
    assert idx == {"p-0-0": (1, 7), "p-0-1": (7, 13)}
    assert end == 13


def test_build_block_index_ignores_section_breaks_etc():
    doc = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [
            {"startIndex": 1, "endIndex": 7,
             "paragraph": {"elements": [{"textRun": {"content": "first\n"}}]}},
            {"startIndex": 7, "endIndex": 8, "sectionBreak": {}},
            {"startIndex": 8, "endIndex": 13,
             "paragraph": {"elements": [{"textRun": {"content": "second\n"}}]}},
        ]},
    }
    idx, end = build_block_index_from_docs_document(doc)
    # SectionBreak occupies AST index 1, so the second paragraph is p-0-2,
    # not p-0-1 — matching what _stamp_paragraph_ids produces.
    assert "p-0-0" in idx and "p-0-2" in idx
    assert end == 13


def test_build_block_index_includes_list_items_with_ids():
    doc = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "lists": {"L1": {"listProperties": {"nestingLevels": [
            {"glyphType": "BULLET"},
        ]}}},
        "body": {"content": [
            {"startIndex": 1, "endIndex": 12,
             "paragraph": {
                 "bullet": {"listId": "L1"},
                 "elements": [{"textRun": {"content": "first item\n"}}],
             }},
            {"startIndex": 12, "endIndex": 24,
             "paragraph": {
                 "bullet": {"listId": "L1"},
                 "elements": [{"textRun": {"content": "second item\n"}}],
             }},
        ]},
    }
    idx, end = build_block_index_from_docs_document(doc)
    assert len(idx) == 2
    assert all(k.startswith("p-") for k in idx)
    assert end == 24


def test_build_block_index_skips_empty_paragraphs():
    """Empty paragraphs have no paragraph_id (per _stamp_paragraph_ids), so
    they shouldn't appear in the block_index either."""
    doc = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [
            {"startIndex": 1, "endIndex": 7,
             "paragraph": {"elements": [{"textRun": {"content": "real\n"}}]}},
            {"startIndex": 7, "endIndex": 8,
             "paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
        ]},
    }
    idx, _ = build_block_index_from_docs_document(doc)
    assert idx == {"p-0-0": (1, 7)}


# --- apply runner --------------------------------------------------------


class _FakeService:
    """Minimal docs.documents().get() / batchUpdate() stand-in."""

    def __init__(self, doc):
        self._doc = doc
        self.last_requests: list[dict] = []

    def documents(self):  # noqa: D401  (mimics google client shape)
        return self

    def get(self, documentId=None, includeTabsContent=None):  # noqa
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
