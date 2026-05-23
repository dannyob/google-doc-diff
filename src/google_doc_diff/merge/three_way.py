"""Three-way AST merge: base + local + remote -> merged AST + conflicts.

Block-level merge keyed by paragraph_id. The matrix from the design spec:

    local   | remote  | result
    --------|---------|------------------------
    none    | none    | (skip)
    change  | none    | apply local
    none    | change  | apply remote
    same    | same    | apply once
    diff    | diff    | conflict (Conflict node)

Anonymous blocks (no paragraph_id — e.g. ListItem in current scope) are
carried through from the local side unchanged; sub-block content
merging for lists is a follow-up.

The merged Document inherits its metadata from `local` since that's the
user's draft. The `Conflict` AST nodes the merger emits embed local +
remote views of the same block_id so emit can render them as
`.gd-conflict` git-style divs the user resolves manually.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from google_doc_diff.ast.nodes import (
    Conflict,
    Document,
    Heading,
    ListItem,
    Paragraph,
    Tab,
)


def merge(
    base: Document, local: Document, remote: Document,
) -> tuple[Document, list[Conflict]]:
    """Three-way merge base/local/remote.

    Returns ``(merged_document, conflicts)``. The merged document
    inherits its frontmatter from `local`. Any conflict the merger
    couldn't resolve appears both in `conflicts` and inline at the
    block's position inside the merged document.
    """
    base_blocks = _blocks(base)
    local_blocks = _blocks(local)
    remote_blocks = _blocks(remote)

    base_idx = _index_by_id(base_blocks)
    remote_idx = _index_by_id(remote_blocks)

    merged_blocks: list = []
    conflicts: list[Conflict] = []
    seen_ids: set[str] = set()

    # Walk local in document order — local order is the user's canonical
    # arrangement, so the merge preserves it.
    for block in local_blocks:
        bid = _block_id(block)
        if bid is None:
            merged_blocks.append(block)
            continue
        seen_ids.add(bid)
        base_b = base_idx.get(bid)
        remote_b = remote_idx.get(bid)
        merged, conflict = _reconcile_block(bid, base_b, block, remote_b)
        if not isinstance(merged, _Deleted):
            merged_blocks.append(merged)
        if conflict is not None:
            conflicts.append(conflict)

    # Pick up blocks present in remote (or base) but not in local. These
    # are remote-only inserts (and possibly local-side deletions).
    for block in remote_blocks:
        bid = _block_id(block)
        if bid is None or bid in seen_ids:
            continue
        seen_ids.add(bid)
        base_b = base_idx.get(bid)
        if base_b is None:
            # Remote-only insert that local doesn't have either — keep.
            merged_blocks.append(block)
        elif _block_eq(block, base_b):
            # Local deleted, remote unchanged — drop.
            continue
        else:
            # Local deleted, remote changed — conflict.
            c = Conflict(
                conflict_id=f"c-{bid}",
                local_blocks=[],
                remote_blocks=[block],
                base_blocks=[base_b],
            )
            conflicts.append(c)
            merged_blocks.append(c)

    merged_tabs = [Tab(
        tab_id=local.tabs[0].tab_id if local.tabs else "t-default",
        title=local.tabs[0].title if local.tabs else "(default)",
        level=local.tabs[0].level if local.tabs else 0,
        blocks=merged_blocks,
    )] if local.tabs else []

    merged = replace(local, tabs=merged_tabs)
    return merged, conflicts


# --- per-block reconciliation -------------------------------------------


def _reconcile_block(
    bid: str, base_b, local_b, remote_b,
) -> tuple[object, Conflict | None]:
    """Return ``(merged_block, conflict_or_None)`` for one block id.

    Handles every cell of the local×remote matrix.
    """
    if remote_b is None:
        if base_b is None:
            # Local-only insert; keep.
            return local_b, None
        if _block_eq(local_b, base_b):
            # Local unchanged, remote deleted — drop. We represent "dropped"
            # as None and the caller filters it out... but to keep this
            # function pure, we instead emit a no-op block placeholder.
            return _Deleted(), None
        # Remote deleted, local changed — conflict.
        c = Conflict(
            conflict_id=f"c-{bid}",
            local_blocks=[local_b],
            remote_blocks=[],
            base_blocks=[base_b],
        )
        return c, c

    # remote_b is present.
    if base_b is None:
        # No base — both sides added this block independently.
        if _block_eq(local_b, remote_b):
            return local_b, None
        c = Conflict(
            conflict_id=f"c-{bid}",
            local_blocks=[local_b],
            remote_blocks=[remote_b],
            base_blocks=[],
        )
        return c, c

    if _block_eq(local_b, remote_b):
        return local_b, None
    if _block_eq(local_b, base_b):
        # Local unchanged, remote changed — take remote.
        return remote_b, None
    if _block_eq(remote_b, base_b):
        # Local changed, remote unchanged — take local.
        return local_b, None

    # Both sides diverged.
    c = Conflict(
        conflict_id=f"c-{bid}",
        local_blocks=[local_b],
        remote_blocks=[remote_b],
        base_blocks=[base_b],
    )
    return c, c


class _Deleted:
    """Sentinel for a block both sides agree should be deleted."""


# --- helpers -------------------------------------------------------------


def _blocks(doc: Document) -> list:
    if not doc.tabs:
        return []
    return list(doc.tabs[0].blocks)


def _block_id(block) -> str | None:
    if isinstance(block, (Paragraph, Heading)):
        return block.paragraph_id or (
            getattr(block, "anchor_id", None) if isinstance(block, Heading) else None
        )
    if isinstance(block, Conflict):
        return block.conflict_id
    if isinstance(block, ListItem):
        return block.paragraph_id
    return None


def _index_by_id(blocks: Iterable) -> dict:
    out: dict = {}
    for b in blocks:
        bid = _block_id(b)
        if bid is not None:
            out[bid] = b
    return out


def _block_eq(a, b) -> bool:
    """Equality at the granularity of "would the diff find any change."

    For Paragraph / Heading: text and run formatting must match. We compare
    the runs tuple (which is what diff.py uses to detect content/style
    changes). paragraph_id / anchor_id are NOT part of the comparison
    because the merge has already matched by id.
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, (Paragraph, Heading)):
        if isinstance(a, Heading) and a.level != b.level:
            return False
        return list(a.runs or []) == list(b.runs or [])
    if isinstance(a, ListItem):
        if a.level != b.level or a.kind != b.kind:
            return False
        return list(a.runs or []) == list(b.runs or [])
    return a == b
