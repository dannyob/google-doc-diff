"""Extract the OT op stream from a Google Docs /edit HTML page.

The /edit page embeds the document model as a sequence of
``DOCS_modelChunk = {...};`` assignments (interleaved with
``DOCS_modelChunk = undefined;`` resets). Each chunk's ``chunk`` array holds
ops. Document *content* ops (``is``/``as``/``ae``/``te``/``iss``…) are not at
the top level — they are wrapped one-per ``nm`` op routed to a tab via
``nmr == ["ksm", TAB_ID]``, with the real op as the ``nmc`` payload. Top-level
ops like ``mkch``/``umv``/``dc`` are structural, not tab content.

``extract_ot_ops`` parses every chunk, unwraps the ``ksm`` wrappers, and groups
the resulting inner ops by tab id.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

_MARKER = "DOCS_modelChunk = "


@dataclass
class KixModel:
    """The document's OT op stream, unwrapped and grouped by tab id."""

    ops_by_tab: dict[str, list[dict]]
    revision: int
    model_version: int
    suggestion_colors: dict[str, str] = field(default_factory=dict)

    @property
    def ops(self) -> list[dict]:
        """All inner ops across tabs in chunk order (tab attribution dropped)."""
        return [op for ops in self.ops_by_tab.values() for op in ops]


def extract_ot_ops(html: str) -> KixModel | None:
    """Parse and unwrap all DOCS_modelChunk blocks.

    Returns None if no model chunk is found (login page, redirect, etc.).
    """
    chunks = _parse_all_chunks(html)
    if not chunks:
        return None

    ops_by_tab: dict[str, list[dict]] = defaultdict(list)
    revision = 0
    model_version = 0
    suggestion_colors: dict[str, str] = {}

    for raw in chunks:
        rev = raw.get("revision")
        if isinstance(rev, int):
            revision = rev
        colors = raw.get("suggestionColors")
        if isinstance(colors, dict):
            suggestion_colors.update(colors)
        for op in raw.get("chunk", []):
            if not isinstance(op, dict):
                continue
            if op.get("ty") == "umv":
                mv = op.get("mv")
                if isinstance(mv, int):
                    model_version = mv
                continue
            tab_id = _ksm_tab(op)
            if tab_id is not None and isinstance(op.get("nmc"), dict):
                ops_by_tab[tab_id].append(op["nmc"])

    if not ops_by_tab:
        return None

    return KixModel(
        ops_by_tab=dict(ops_by_tab),
        revision=revision,
        model_version=model_version or revision,
        suggestion_colors=suggestion_colors,
    )


def _ksm_tab(op: dict) -> str | None:
    """Return the tab id if ``op`` is a keyed-sub-model wrapper, else None."""
    if op.get("ty") != "nm":
        return None
    nmr = op.get("nmr")
    if isinstance(nmr, list) and len(nmr) >= 2 and nmr[0] == "ksm":
        return nmr[1]
    return None


def _parse_all_chunks(html: str) -> list[dict]:
    """Extract every ``DOCS_modelChunk = {...}`` object (skipping ``undefined``)."""
    chunks: list[dict] = []
    i = 0
    while True:
        idx = html.find(_MARKER, i)
        if idx < 0:
            break
        brace = html.find("{", idx)
        semi = html.find(";", idx)
        # `DOCS_modelChunk = undefined;` has no object before the semicolon.
        if brace < 0 or (semi != -1 and semi < brace):
            i = idx + len(_MARKER)
            continue
        end = _find_closing_brace(html, brace)
        if end < 0:
            break
        try:
            chunks.append(json.loads(html[brace:end]))
        except json.JSONDecodeError:
            pass
        i = end
    return chunks


def _find_closing_brace(s: str, start: int) -> int:
    """Bracket-match from an opening brace, respecting JSON string escapes."""
    depth = 0
    in_str = False
    esc = False
    for k in range(start, len(s)):
        c = s[k]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if in_str:
            if c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k + 1
    return -1
