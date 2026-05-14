"""Tests for ops/diff — AST -> OpPlan diff."""
from __future__ import annotations

from datetime import datetime, timezone

from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    Paragraph,
    Run,
    StyleDescriptor,
    Tab,
)
from google_doc_diff.ops import (
    ApplyStyle,
    DeleteBlock,
    DeleteRange,
    InsertBlock,
    InsertText,
    MoveBlock,
    diff,
)


def _doc(blocks) -> Document:
    return Document(
        doc_id="d", title="t", revision_id="r", drive_url="u",
        captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="(default)", level=0, blocks=blocks)],
    )


# --- structural ----------------------------------------------------------


def test_no_change_yields_empty_plan():
    a = Paragraph(runs=[Run(text="Hello")], paragraph_id="p-1")
    b = Paragraph(runs=[Run(text="Hello")], paragraph_id="p-1")
    plan = diff(_doc([a]), _doc([b]))
    assert list(plan) == []


def test_paragraph_inserted_at_top():
    base = _doc([])
    target = _doc([Paragraph(runs=[Run(text="new")], paragraph_id="p-new")])
    plan = diff(base, target)
    [op] = list(plan)
    assert isinstance(op, InsertBlock)
    assert op.after_id is None


def test_paragraph_inserted_after_existing():
    p1 = Paragraph(runs=[Run(text="existing")], paragraph_id="p-1")
    base = _doc([p1])
    target = _doc([
        p1,
        Paragraph(runs=[Run(text="new")], paragraph_id="p-new"),
    ])
    plan = diff(base, target)
    inserts = [o for o in plan if isinstance(o, InsertBlock)]
    assert len(inserts) == 1
    assert inserts[0].after_id == "p-1"


def test_paragraph_deleted_emits_delete_block():
    base = _doc([Paragraph(runs=[Run(text="gone")], paragraph_id="p-gone")])
    target = _doc([])
    plan = diff(base, target)
    [op] = list(plan)
    assert isinstance(op, DeleteBlock)
    assert op.block_id == "p-gone"


def test_paragraph_moved_emits_move():
    p1 = Paragraph(runs=[Run(text="a")], paragraph_id="p-1")
    p2 = Paragraph(runs=[Run(text="b")], paragraph_id="p-2")
    base = _doc([p1, p2])
    target = _doc([p2, p1])
    plan = diff(base, target)
    moves = [o for o in plan if isinstance(o, MoveBlock)]
    assert moves, list(plan)


def test_deletes_emitted_before_inserts():
    p1 = Paragraph(runs=[Run(text="gone")], paragraph_id="p-gone")
    p2 = Paragraph(runs=[Run(text="new")], paragraph_id="p-new")
    plan = diff(_doc([p1]), _doc([p2]))
    kinds = [type(o).__name__ for o in plan]
    assert kinds.index("DeleteBlock") < kinds.index("InsertBlock")


# --- content -------------------------------------------------------------


def test_text_change_emits_insert_or_delete():
    base = _doc([Paragraph(runs=[Run(text="Hello world")], paragraph_id="p-1")])
    target = _doc([Paragraph(runs=[Run(text="Hello brave new world")], paragraph_id="p-1")])
    plan = diff(base, target)
    inserts = [o for o in plan if isinstance(o, InsertText)]
    assert inserts
    # The inserted chunk should mention the new substring.
    joined = "".join(o.text for o in inserts)
    assert "brave new" in joined


def test_text_deletion_emits_delete_range():
    base = _doc([Paragraph(runs=[Run(text="Hello there")], paragraph_id="p-1")])
    target = _doc([Paragraph(runs=[Run(text="Hello")], paragraph_id="p-1")])
    plan = diff(base, target)
    deletes = [o for o in plan if isinstance(o, DeleteRange)]
    assert deletes


def test_style_only_change_emits_apply_style():
    base = _doc([Paragraph(
        runs=[Run(text="Hello world")],
        paragraph_id="p-1",
    )])
    target = _doc([Paragraph(
        runs=[Run(text="Hello world", formatting=StyleDescriptor(bold=True))],
        paragraph_id="p-1",
    )])
    plan = diff(base, target)
    styles = [o for o in plan if isinstance(o, ApplyStyle)]
    assert styles
    assert styles[0].style.bold is True


def test_text_change_does_not_emit_apply_style_for_changed_region():
    # When text *and* style change, the InsertText already carries the new
    # style in run_style (set elsewhere). The style-pass is only invoked
    # when text is identical.
    base = _doc([Paragraph(runs=[Run(text="old")], paragraph_id="p-1")])
    target = _doc([Paragraph(
        runs=[Run(text="new", formatting=StyleDescriptor(bold=True))],
        paragraph_id="p-1",
    )])
    plan = diff(base, target)
    styles = [o for o in plan if isinstance(o, ApplyStyle)]
    assert styles == []


# --- composite ----------------------------------------------------------


def test_heading_text_change_emits_text_ops():
    h_base = Heading(level=1, runs=[Run(text="Old")], paragraph_id="h-1")
    h_target = Heading(level=1, runs=[Run(text="New")], paragraph_id="h-1")
    plan = diff(_doc([h_base]), _doc([h_target]))
    assert any(isinstance(o, (InsertText, DeleteRange)) for o in plan)


def test_empty_base_treats_target_as_all_inserts():
    target = _doc([
        Heading(level=1, runs=[Run(text="H")], paragraph_id="h-1"),
        Paragraph(runs=[Run(text="p")], paragraph_id="p-1"),
    ])
    plan = diff(_doc([]), target)
    inserts = [o for o in plan if isinstance(o, InsertBlock)]
    assert len(inserts) == 2
