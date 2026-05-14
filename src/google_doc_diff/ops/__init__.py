"""Operation IR for round-trip writes.

`primitives` holds channel-agnostic mutation dataclasses (`InsertText`,
`DeleteRange`, `ApplyStyle`, `InsertBlock`, `DeleteBlock`, `MoveBlock`,
plus the OpPlan container). `diff` produces an OpPlan from two ASTs.

The OpPlan is consumed by `apply/` which selects a channel per primitive
and emits Docs `batchUpdate` requests or `/save` bundles.
"""
from google_doc_diff.ops.primitives import (
    ApplyStyle,
    DeleteBlock,
    DeleteRange,
    InsertBlock,
    InsertText,
    MoveBlock,
    Op,
    OpPlan,
)
from google_doc_diff.ops.diff import diff

__all__ = [
    "ApplyStyle",
    "DeleteBlock",
    "DeleteRange",
    "InsertBlock",
    "InsertText",
    "MoveBlock",
    "Op",
    "OpPlan",
    "diff",
]
