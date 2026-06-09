"""Post-processing enrichment: decorate an existing AST with kix OT details.

The OT op stream (see ``kix.model``) is grouped by tab id. For each tab we feed
its unwrapped inner ops to the matchers here:

* **voting chips** — ``ae`` ops with ``et == "emoji-voting"`` plus nested
  ``dtvc`` ``voting-chip-populate`` ops carry the emoji and voter ids the
  public Docs API omits.
* **suggestion colors** — taken from the chunk's ``suggestionColors`` map.

Precise *comment-anchor* placement also lives in the stream (``as`` ops with
``st == "doco_anchor"``, carrying ``si``/``ei`` ranges) but resolving those
``si`` offsets to AST blocks needs a faithful reconstruction of Kix's index
space (table/list/footnote/suggestion content the public AST doesn't size the
same way). That's deferred — comment anchoring stays on the text-matching path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    ListItem,
    Paragraph,
    SmartChip,
    Table,
    Voter,
    VotingChip,
)
from google_doc_diff.kix.model import KixModel

logger = logging.getLogger(__name__)

_BLOCK_TYPES = (Paragraph, Heading, ListItem)


def _model_ops_for_tab(model: KixModel, tab) -> list[dict] | None:
    """Look up a tab's ops, reconciling the AST's ``t-`` tab-id prefix.

    The model is keyed by Google's raw tab id (``t.xxxx``); the AST stores it
    as ``t-`` + that id (see ``from_docs_json``).
    """
    ops = model.ops_by_tab.get(tab.tab_id)
    if ops is None:
        ops = model.ops_by_tab.get(tab.tab_id.removeprefix("t-"))
    return ops


@dataclass
class EnrichResult:
    """Summary of what the enrichment pass did."""

    suggestion_colors_applied: int = 0
    voting_chips_enriched: int = 0


def enrich_from_kix(doc: Document, model: KixModel) -> EnrichResult:
    """Mutate doc in place with details from the OT stream."""
    result = EnrichResult()
    result.suggestion_colors_applied = _enrich_suggestion_colors(doc, model)
    result.voting_chips_enriched = _enrich_voting_chips(doc, model)
    return result


# --- voting chips ----------------------------------------------------------


def _parse_voting_chips(ops: list[dict]) -> dict[str, dict]:
    """Scan a tab's ops for emoji-voting ae + dtvc populate pairs."""
    voting_ids: set[str] = set()
    for op in ops:
        if op.get("ty") == "ae" and op.get("et") == "emoji-voting" and "id" in op:
            voting_ids.add(op["id"])

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


def _iter_tabs(tabs):
    """Yield tabs depth-first, including nested child tabs."""
    for tab in tabs:
        yield tab
        yield from _iter_tabs(tab.children)


def _enrich_voting_chips(doc: Document, model: KixModel) -> int:
    """Replace SmartChip nodes with VotingChip nodes, per tab."""
    count = 0
    for tab in _iter_tabs(doc.tabs):
        ops = _model_ops_for_tab(model, tab)
        if ops:
            count += _enrich_voting_chips_in_tab(tab, ops)
    return count


def _enrich_voting_chips_in_tab(tab, ops: list[dict]) -> int:
    chips = _parse_voting_chips(ops)
    if not chips:
        return 0

    te_order = sorted(
        (op["spi"], op["id"])
        for op in ops
        if op.get("ty") == "te" and op.get("id") in chips and "spi" in op
    )
    ordered_chip_ids = [chip_id for _, chip_id in te_order]

    smart_chips: list[tuple[list, int]] = []
    _collect_smart_chips(tab.blocks, smart_chips)

    if len(smart_chips) != len(ordered_chip_ids):
        logger.warning(
            "tab %s: %d widget chips in AST vs %d voting chips in kix stream; "
            "skipping enrichment to avoid mispairing",
            tab.tab_id, len(smart_chips), len(ordered_chip_ids),
        )
        return 0

    count = 0
    for (runs, idx), chip_id in zip(smart_chips, ordered_chip_ids, strict=True):
        data = chips[chip_id]
        voters = [Voter(obfuscated_id=v) for v in data["voters"]]
        runs[idx] = VotingChip(
            chip_id=chip_id,
            emoji=data["emoji"],
            voters=voters,
            current_user_voted=data["current_user_voted"],
            signature=data["signature"],
        )
        count += 1
    return count


def _collect_smart_chips(blocks, out: list[tuple[list, int]]) -> None:
    """Collect (runs, index) for every widget SmartChip in document order,
    descending into table cells.

    Only PUA-glyph placeholders (see ``from_docs_json._split_chips``) can be
    voting chips; person/richlink/date chips carry no ``glyph`` and must not
    consume a pairing slot."""
    for block in blocks:
        if isinstance(block, _BLOCK_TYPES):
            for i, run in enumerate(block.runs):
                if isinstance(run, SmartChip) and "glyph" in run.data:
                    out.append((block.runs, i))
        elif isinstance(block, Table):
            for row in block.rows:
                for cell in row.cells:
                    _collect_smart_chips(cell.blocks, out)


# --- suggestion colors -----------------------------------------------------


def _enrich_suggestion_colors(doc: Document, model: KixModel) -> int:
    """Patch suggestion colors from the kix model onto matching suggestions."""
    count = 0
    for sug_id, color in model.suggestion_colors.items():
        if sug_id in doc.suggestions:
            doc.suggestions[sug_id].color = color
            count += 1
    return count
