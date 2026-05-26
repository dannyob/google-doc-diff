"""Post-processing enrichment: decorate an existing AST with kix OT details."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google_doc_diff.ast.nodes import Document
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
    return result


def _enrich_suggestion_colors(doc: Document, model: KixModel) -> int:
    """Patch suggestion colors from the kix model onto matching suggestions."""
    count = 0
    for sug_id, color in model.suggestion_colors.items():
        if sug_id in doc.suggestions:
            doc.suggestions[sug_id].color = color
            count += 1
    return count
