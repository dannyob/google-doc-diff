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
    SmartChip,
    Voter,
    VotingChip,
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
    result.voting_chips_enriched = _enrich_voting_chips(doc, model)
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
        c for c in doc.comments.values() if not c.deleted and c.quoted_text and c.anchor
    ]
    if not active_comments:
        return 0

    def resolver(kix_anchor: str) -> int | None:
        return _spi_to_block_index(doc, anchor_map.get(kix_anchor))

    anchor_comments(doc, kix_resolver=resolver)
    resolved = sum(1 for c in active_comments if not c.orphaned)
    return resolved


def _parse_voting_chips(ops: list[dict]) -> dict[str, dict]:
    """Scan OT ops for emoji-voting ae+nm pairs; return dict keyed by chip_id."""
    # Collect ae ops with et=="emoji-voting"
    voting_ids: set[str] = set()
    for op in ops:
        if op.get("ty") == "ae" and op.get("et") == "emoji-voting" and "id" in op:
            voting_ids.add(op["id"])

    # Collect nm ops that populate voting chips
    chips: dict[str, dict] = {}
    for op in ops:
        if op.get("ty") != "nm":
            continue
        nmr = op.get("nmr", [])
        nmc = op.get("nmc", [])
        if not (len(nmr) >= 2 and nmr[0] == "dtvc"):
            continue
        chip_id = nmr[1]
        if chip_id not in voting_ids:
            continue
        if not (len(nmc) >= 1 and nmc[0] == "voting-chip-populate"):
            continue
        emoji = nmc[1] if len(nmc) > 1 else ""
        raw_voters = nmc[2] if len(nmc) > 2 else []
        current_user_voted = bool(nmc[3]) if len(nmc) > 3 else False
        signature = nmc[4] if len(nmc) > 4 else ""
        voter_ids = [
            v["ui"]["ui_oi"]
            for v in raw_voters
            if isinstance(v, dict) and "ui" in v and "ui_oi" in v["ui"]
        ]
        chips[chip_id] = {
            "chip_id": chip_id,
            "emoji": emoji,
            "voters": voter_ids,
            "current_user_voted": current_user_voted,
            "signature": signature,
        }
    return chips


def _enrich_voting_chips(doc: Document, model: KixModel) -> int:
    """Replace SmartChip nodes with VotingChip nodes using OT op data."""
    chips = _parse_voting_chips(model.ops)
    if not chips:
        return 0

    # Walk chips in document order (by spi from te ops)
    te_order: list[tuple[int, str]] = []
    for op in model.ops:
        if op.get("ty") == "te" and op.get("id") in chips:
            te_order.append((op["spi"], op["id"]))
    te_order.sort(key=lambda x: x[0])
    ordered_chip_ids = [chip_id for _, chip_id in te_order]

    # Walk SmartChip nodes in document order
    smart_chips_in_order: list[tuple[object, list, int]] = []
    for tab in doc.tabs:
        for block in tab.blocks:
            if not isinstance(block, (Paragraph, Heading, ListItem)):
                continue
            for i, run in enumerate(block.runs):
                if isinstance(run, SmartChip):
                    smart_chips_in_order.append((block, block.runs, i))

    # Pair positionally
    count = 0
    for (_block, runs, idx), chip_id in zip(smart_chips_in_order, ordered_chip_ids, strict=False):
        data = chips[chip_id]
        voters = [Voter(obfuscated_id=v) for v in data["voters"]]
        replacement = VotingChip(
            chip_id=chip_id,
            emoji=data["emoji"],
            voters=voters,
            current_user_voted=data["current_user_voted"],
            signature=data["signature"],
        )
        runs[idx] = replacement
        count += 1
    return count


def _enrich_suggestion_colors(doc: Document, model: KixModel) -> int:
    """Patch suggestion colors from the kix model onto matching suggestions."""
    count = 0
    for sug_id, color in model.suggestion_colors.items():
        if sug_id in doc.suggestions:
            doc.suggestions[sug_id].color = color
            count += 1
    return count
