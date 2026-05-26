"""Post-processing enrichment: decorate an existing AST with kix OT details."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google_doc_diff.ast.anchor_comments import anchor_comments
from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    ListItem,
    Paragraph,
    Run,
)
from google_doc_diff.kix.model import KixModel

logger = logging.getLogger(__name__)


@dataclass
class EnrichResult:
    """Summary of what the enrichment pass did."""

    suggestion_colors_applied: int = 0
    comment_anchors_resolved: int = 0
    voting_chips_enriched: int = 0


def enrich_from_kix(doc: Document, model: KixModel) -> EnrichResult:
    """Mutate doc in place with details from the OT stream."""
    result = EnrichResult()
    result.suggestion_colors_applied = _enrich_suggestion_colors(doc, model)
    result.comment_anchors_resolved = _enrich_comment_anchors(doc, model)
    return result


def build_kix_anchor_map(ops: list[dict]) -> dict[str, int]:
    """Build a mapping from kix anchor IDs to their byte offsets (spi) from OT ops."""
    out: dict[str, int] = {}
    for op in ops:
        if op.get("ty") == "te" and "id" in op and "spi" in op:
            out[op["id"]] = op["spi"]
    return out


def _spi_to_block_index(doc: Document, spi: int | None) -> int | None:
    """Convert a byte offset (spi) to a block index in the first tab."""
    if spi is None or not doc.tabs:
        return None
    offset = 0
    for i, block in enumerate(doc.tabs[0].blocks):
        if not isinstance(block, (Paragraph, Heading, ListItem)):
            continue
        block_len = sum(len(r.text) for r in block.runs if isinstance(r, Run))
        block_len += 1  # newline separator
        if offset <= spi < offset + block_len:
            return i
        offset += block_len
    return None


def _enrich_comment_anchors(doc: Document, model: KixModel) -> int:
    """Re-run comment anchoring using kix-derived exact positions."""
    anchor_map = build_kix_anchor_map(model.ops)
    if not anchor_map:
        return 0

    active_comments = [
        c for c in doc.comments.values()
        if not c.deleted and c.quoted_text and c.anchor
    ]
    if not active_comments:
        return 0

    def resolver(kix_anchor: str) -> int | None:
        return _spi_to_block_index(doc, anchor_map.get(kix_anchor))

    anchor_comments(doc, kix_resolver=resolver)
    resolved = sum(1 for c in active_comments if not c.orphaned)
    return resolved


def _enrich_suggestion_colors(doc: Document, model: KixModel) -> int:
    """Patch suggestion colors from the kix model onto matching suggestions."""
    count = 0
    for sug_id, color in model.suggestion_colors.items():
        if sug_id in doc.suggestions:
            doc.suggestions[sug_id].color = color
            count += 1
    return count
