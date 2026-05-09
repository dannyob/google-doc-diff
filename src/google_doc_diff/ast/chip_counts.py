"""Recover chip counts (e.g. vote totals) by cross-referencing Google's
markdown export with the JSON-derived AST.

The Docs API JSON encodes voting chips as PUA codepoints with no count
information. Google's markdown export renders them inline as "(➕ N)".
This module walks the markdown text, extracts the (chip, count) sequence
in document order, then walks the AST in the same order and attaches each
count to the matching SmartChip.

Cost: one extra Drive v2 export call per pull.
"""

from __future__ import annotations

import re

from google_doc_diff.ast.nodes import Document, SmartChip

# Patterns for the rendered chips Google emits in markdown export. Add new
# entries as we discover them. Each entry is (chip-kind, regex). The regex
# must have one capture group containing the count.
_CHIP_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("vote-thumbsup", re.compile(r"\(➕\s*(\d+)\\?\)")),
    ("vote-thumbsdown", re.compile(r"\(➖\s*(\d+)\\?\)")),
]


def extract_counts_from_markdown(md: str) -> list[tuple[str, int]]:
    """Walk the markdown text in order; return (chip-kind, count) pairs."""
    matches: list[tuple[int, str, int]] = []
    for kind, pat in _CHIP_PATTERNS:
        for m in pat.finditer(md):
            matches.append((m.start(), kind, int(m.group(1))))
    matches.sort(key=lambda x: x[0])
    return [(k, n) for _, k, n in matches]


def attach_counts_to_chips(doc: Document, md: str) -> int:
    """Annotate every voting/reaction SmartChip in the AST with its count.

    Returns the number of chips that received a count.
    """
    counts = extract_counts_from_markdown(md)
    chips = list(_walk_chips(doc))
    annotated = 0
    # Pair in document order; if lengths mismatch, attach what we can and
    # leave the remainder uncounted (the chip kind from JSON should still
    # match the kind from markdown).
    for chip, (md_kind, count) in zip(chips, counts, strict=False):
        if chip.kind != md_kind:
            # Skip mismatch; safer than attaching the wrong count.
            continue
        chip.data["count"] = count
        chip.display_text = f"{chip.display_text} {count}"
        annotated += 1
    return annotated


def _walk_chips(doc: Document):
    def walk(node):
        if isinstance(node, SmartChip) and node.kind.startswith("vote-"):
            yield node
        for attr in ("runs", "blocks", "rows", "cells", "children", "tabs"):
            children = getattr(node, attr, None)
            if children:
                for c in children:
                    yield from walk(c)

    for tab in doc.tabs:
        yield from walk(tab)
