"""Tests for ops/primitives — the OpPlan IR."""
from __future__ import annotations

from google_doc_diff.ast.nodes import StyleDescriptor
from google_doc_diff.ops import (
    ApplyStyle,
    DeleteBlock,
    DeleteRange,
    InsertBlock,
    InsertText,
    MoveBlock,
    OpPlan,
)


def test_insert_text_minimal_construction():
    op = InsertText(block_id="p-1", offset=0, text="Hello")
    assert op.block_id == "p-1"
    assert op.offset == 0
    assert op.text == "Hello"
    assert op.run_style is None


def test_insert_text_with_run_style():
    op = InsertText(
        block_id="p-1", offset=5, text="!",
        run_style=StyleDescriptor(bold=True),
    )
    assert op.run_style.bold is True


def test_delete_range_construction():
    op = DeleteRange(block_id="p-1", start=0, end=5)
    assert op.start == 0
    assert op.end == 5


def test_apply_style_text_scope():
    op = ApplyStyle(scope="text", block_id="p-1", start=0, end=4, style=StyleDescriptor(bold=True))
    assert op.scope == "text"
    assert op.style.bold is True


def test_insert_block_carries_arbitrary_payload():
    payload = {"hi": "block-like"}
    op = InsertBlock(after_id="p-prev", block=payload)
    assert op.after_id == "p-prev"
    assert op.block is payload


def test_insert_block_at_top():
    op = InsertBlock(after_id=None, block="x")
    assert op.after_id is None


def test_delete_block():
    assert DeleteBlock(block_id="p-1").block_id == "p-1"


def test_move_block_to_top():
    op = MoveBlock(block_id="p-1", after_id=None)
    assert op.after_id is None


# OpPlan ------------------------------------------------------------------


def test_empty_plan_is_falsy_and_len_zero():
    plan = OpPlan()
    assert not plan
    assert len(plan) == 0
    assert list(plan) == []


def test_plan_append_and_iter():
    plan = OpPlan()
    a = InsertText(block_id="p-1", offset=0, text="A")
    b = DeleteRange(block_id="p-1", start=0, end=1)
    plan.append(a)
    plan.append(b)
    assert len(plan) == 2
    assert list(plan) == [a, b]
    assert bool(plan)


def test_plan_summary_counts_by_kind():
    plan = OpPlan()
    plan.append(InsertText(block_id="p", offset=0, text="x"))
    plan.append(InsertText(block_id="p", offset=1, text="y"))
    plan.append(DeleteRange(block_id="p", start=2, end=3))
    s = plan.summary()
    assert s == {"InsertText": 2, "DeleteRange": 1}


def test_plan_extend():
    plan = OpPlan()
    plan.extend([
        InsertText(block_id="p", offset=0, text="x"),
        DeleteRange(block_id="p", start=2, end=3),
    ])
    assert len(plan) == 2
