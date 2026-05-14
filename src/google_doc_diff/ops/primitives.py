"""OpPlan primitives.

Each primitive is a channel-agnostic mutation expressed at the granularity
of a single Docs `Request` or a single OT command. The apply backends
decompose composites (`InsertBlock`, `MoveBlock`) into channel-specific
calls.

Primitives are frozen dataclasses so a plan is trivially hashable / equatable
in tests. Field naming mirrors the Docs API where possible (e.g. `text` not
`s`) to make the docs_api translation in `apply/` direct.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from google_doc_diff.ast.nodes import (
    ParagraphProperties,
    StyleDescriptor,
)


@dataclass(frozen=True)
class InsertText:
    """Insert a string at the given offset inside the named block.

    `offset` is the character offset within `block_id`'s text content (the
    flattened concatenation of its runs). The apply backend resolves
    `(block_id, offset)` to a doc-wide index just before sending.
    """

    block_id: str
    offset: int
    text: str
    run_style: StyleDescriptor | None = None


@dataclass(frozen=True)
class DeleteRange:
    """Delete a half-open `[start, end)` range from `block_id`'s text."""

    block_id: str
    start: int
    end: int


@dataclass(frozen=True)
class ApplyStyle:
    """Apply a style to a `[start, end)` range inside `block_id`.

    `scope` is one of:
      - `"text"`         — character run formatting
      - `"paragraph"`    — paragraph-level (alignment, spacing, ...)
      - `"heading"`      — heading-level definition (named style)
    """

    scope: str
    block_id: str
    start: int
    end: int
    style: StyleDescriptor | ParagraphProperties | dict


@dataclass(frozen=True)
class InsertBlock:
    """Insert a full block AFTER `after_id` (None = at the top).

    The `block` field carries the AST node itself; the apply backend turns it
    into the right combination of inserts + styles for the chosen channel.
    """

    after_id: str | None
    block: object


@dataclass(frozen=True)
class DeleteBlock:
    block_id: str


@dataclass(frozen=True)
class MoveBlock:
    """Move `block_id` to immediately AFTER `after_id` (None = top).

    Decomposed by the apply backend into a delete + insert pair on Docs API.
    """

    block_id: str
    after_id: str | None


# Sum type for static checkers
Op = (
    InsertText | DeleteRange | ApplyStyle
    | InsertBlock | DeleteBlock | MoveBlock
)


@dataclass
class OpPlan:
    """Ordered list of `Op` primitives.

    Apply order matters: deletes before inserts before styles before chips
    (so character indices stay stable). `diff()` emits ops in canonical apply
    order; downstream code can rely on that.
    """

    ops: list[Op] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.ops)

    def __iter__(self):
        return iter(self.ops)

    def __bool__(self) -> bool:
        return bool(self.ops)

    def append(self, op: Op) -> None:
        self.ops.append(op)

    def extend(self, ops) -> None:
        self.ops.extend(ops)

    def summary(self) -> dict[str, int]:
        """Count primitives by kind for human-readable plan summaries."""
        counts: dict[str, int] = {}
        for op in self.ops:
            k = type(op).__name__
            counts[k] = counts.get(k, 0) + 1
        return counts
