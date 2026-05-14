"""Tests for apply/policy — channel-selection dispatcher."""
from __future__ import annotations

from google_doc_diff.apply import DOCS_API, Channel, channel_for, group_by_channel
from google_doc_diff.ops import (
    ApplyStyle,
    DeleteBlock,
    DeleteRange,
    InsertBlock,
    InsertText,
    MoveBlock,
    OpPlan,
)
from google_doc_diff.ast.nodes import StyleDescriptor


def test_every_primitive_routes_to_docs_api_for_overnight_scope():
    ops = [
        InsertText(block_id="p-1", offset=0, text="x"),
        DeleteRange(block_id="p-1", start=0, end=1),
        ApplyStyle(scope="text", block_id="p-1", start=0, end=1,
                   style=StyleDescriptor(bold=True)),
        InsertBlock(after_id=None, block="any"),
        DeleteBlock(block_id="p-1"),
        MoveBlock(block_id="p-1", after_id="p-2"),
    ]
    for op in ops:
        assert channel_for(op) == DOCS_API


def test_group_by_channel_buckets_by_choice():
    plan = OpPlan()
    plan.append(InsertText(block_id="p", offset=0, text="x"))
    plan.append(DeleteRange(block_id="p", start=0, end=1))
    grouped = group_by_channel(plan)
    assert set(grouped.keys()) == {Channel.DOCS_API}
    assert len(grouped[Channel.DOCS_API]) == 2


def test_group_by_channel_preserves_in_channel_order():
    plan = OpPlan()
    a = InsertText(block_id="p", offset=0, text="a")
    b = InsertText(block_id="p", offset=1, text="b")
    c = DeleteRange(block_id="p", start=2, end=3)
    plan.append(a)
    plan.append(b)
    plan.append(c)
    grouped = group_by_channel(plan)
    assert grouped[Channel.DOCS_API] == [a, b, c]
