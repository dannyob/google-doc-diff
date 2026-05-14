"""AST-level diff: produces an OpPlan from base→target ASTs.

Two-phase:
  1. Structural — walk the top-level block list of each tab, keyed by
     stable id (paragraph_id / anchor_id / list_id for runs of items).
     Emit InsertBlock / DeleteBlock / MoveBlock as needed.
  2. Content — for each `BlockModified` pair, run a character-level diff
     over the concatenated run text. Emit InsertText / DeleteRange.

Style-only deltas (same text, different formatting) are emitted as
`ApplyStyle` ops.

Overnight scope:
  - Single-tab docs only. Multi-tab support lands later.
  - Block identity by `paragraph_id` (Paragraph / Heading) or `list_id`
    (a list-run is identified by its first item's list_id).
  - Tables / images / chips are diffed at the block level only (insert /
    delete / move). Cell-level mutation is deferred.
"""
from __future__ import annotations

import difflib
from typing import Iterable

from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    ListItem,
    Paragraph,
    Run,
)
from google_doc_diff.ops.primitives import (
    ApplyStyle,
    DeleteBlock,
    DeleteRange,
    InsertBlock,
    InsertText,
    MoveBlock,
    OpPlan,
)


def diff(base: Document, target: Document) -> OpPlan:
    """Produce an OpPlan that transforms `base` into `target`.

    `base` is the "current state of the live doc" view; `target` is the
    desired state. Empty `base.tabs` (a freshly-created doc) is supported.
    """
    plan = OpPlan()
    base_blocks = _flatten(base)
    target_blocks = _flatten(target)
    _structural_diff(base_blocks, target_blocks, plan)
    _content_diff(base_blocks, target_blocks, plan)
    return plan


# --- helpers --------------------------------------------------------------


def _flatten(doc: Document) -> list:
    """Flatten the first tab's top-level blocks into a list."""
    if not doc.tabs:
        return []
    return list(doc.tabs[0].blocks)


def _block_id(block) -> str | None:
    """Stable identifier for a block, or None if none can be derived."""
    if isinstance(block, Paragraph | Heading):
        return block.paragraph_id or (
            getattr(block, "anchor_id", None) if isinstance(block, Heading) else None
        )
    if isinstance(block, ListItem):
        return None  # list items are coalesced under their parent list
    return None


def _index_by_id(blocks: list) -> dict[str, tuple[int, object]]:
    """Return ``{id: (index, block)}`` for blocks that have ids."""
    out: dict[str, tuple[int, object]] = {}
    for i, b in enumerate(blocks):
        bid = _block_id(b)
        if bid is not None:
            out[bid] = (i, b)
    return out


def _block_text(block) -> str:
    """Concatenated plain text of a block's runs."""
    runs = getattr(block, "runs", None)
    if not runs:
        return ""
    return "".join(r.text for r in runs)


# --- structural diff -----------------------------------------------------


def _structural_diff(base_blocks, target_blocks, plan: OpPlan) -> None:
    base_idx = _index_by_id(base_blocks)
    target_idx = _index_by_id(target_blocks)

    base_ids = set(base_idx)
    target_ids = set(target_idx)

    # Deletes first (apply-order rule: deletes before inserts).
    for deleted_id in sorted(base_ids - target_ids):
        plan.append(DeleteBlock(block_id=deleted_id))

    # Inserts second.
    # New blocks need an "after_id" anchor in the *target* sequence.
    for i, block in enumerate(target_blocks):
        bid = _block_id(block)
        if bid is None:
            # Anonymous block (no id) — treat as insert relative to the
            # previous identified block. List items fall here; v1 emits
            # them coalesced and we round-trip the whole run via the
            # surrounding paragraph_ids.
            continue
        if bid in base_ids:
            continue
        # Find the nearest preceding id in target_blocks that also exists in base.
        after = _previous_shared_id(target_blocks, i, base_ids)
        plan.append(InsertBlock(after_id=after, block=block))

    # Moves third (id exists in both, position differs).
    for bid in sorted(base_ids & target_ids):
        bi, _ = base_idx[bid]
        ti, _ = target_idx[bid]
        if bi == ti:
            continue
        # Position changed. Compute the after_id from target neighbours.
        after = _previous_shared_id(target_blocks, ti, base_ids & target_ids - {bid})
        plan.append(MoveBlock(block_id=bid, after_id=after))


def _previous_shared_id(blocks: list, idx: int, allowed: set[str]) -> str | None:
    for j in range(idx - 1, -1, -1):
        bid = _block_id(blocks[j])
        if bid is not None and bid in allowed:
            return bid
    return None


# --- content diff --------------------------------------------------------


def _content_diff(base_blocks, target_blocks, plan: OpPlan) -> None:
    base_idx = _index_by_id(base_blocks)
    target_idx = _index_by_id(target_blocks)
    for bid in sorted(set(base_idx) & set(target_idx)):
        _, base_b = base_idx[bid]
        _, target_b = target_idx[bid]
        _diff_block(bid, base_b, target_b, plan)


def _diff_block(bid: str, base_b, target_b, plan: OpPlan) -> None:
    base_text = _block_text(base_b)
    target_text = _block_text(target_b)

    if base_text != target_text:
        _emit_text_ops(bid, base_text, target_text, plan)

    # Style diff: compare flattened (text, formatting) runs run-by-run when
    # the text matches. When text differs the InsertText already carries the
    # target formatting, so we only re-apply for style-only deltas here.
    if base_text == target_text:
        _emit_style_ops(bid, base_b, target_b, plan)


def _emit_text_ops(bid: str, base_text: str, target_text: str, plan: OpPlan) -> None:
    """Convert a textual diff into a minimal sequence of InsertText / DeleteRange."""
    matcher = difflib.SequenceMatcher(a=base_text, b=target_text, autojunk=False)
    # Walk opcodes in reverse so earlier indices stay valid as we emit
    # forward-applied ops. Each apply-time op references the *base* doc
    # offsets; the apply backend takes care of running indices.
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("delete", "replace"):
            plan.append(DeleteRange(block_id=bid, start=i1, end=i2))
        if tag in ("insert", "replace"):
            plan.append(InsertText(
                block_id=bid, offset=i1, text=target_text[j1:j2],
            ))


def _emit_style_ops(bid: str, base_b, target_b, plan: OpPlan) -> None:
    base_runs: list[Run] = list(getattr(base_b, "runs", []) or [])
    target_runs: list[Run] = list(getattr(target_b, "runs", []) or [])
    # The earlier text equality check guarantees flattened text matches, but
    # run boundaries can differ. Map both to (offset, formatting) ranges and
    # emit ApplyStyle for any range whose formatting differs.
    base_ranges = _runs_to_ranges(base_runs)
    target_ranges = _runs_to_ranges(target_runs)
    # Simple impl: for every target range, if a base range fully containing
    # it has different formatting, emit an ApplyStyle for the target range.
    for start, end, fmt in target_ranges:
        base_fmt = _formatting_at(base_ranges, start, end)
        if base_fmt != fmt:
            plan.append(ApplyStyle(
                scope="text", block_id=bid, start=start, end=end, style=fmt,
            ))


def _runs_to_ranges(runs: Iterable[Run]) -> list[tuple[int, int, object]]:
    out: list[tuple[int, int, object]] = []
    off = 0
    for r in runs:
        if not r.text:
            continue
        out.append((off, off + len(r.text), r.formatting))
        off += len(r.text)
    return out


def _formatting_at(ranges, start: int, end: int):
    for s, e, fmt in ranges:
        if s <= start and end <= e:
            return fmt
    return None
