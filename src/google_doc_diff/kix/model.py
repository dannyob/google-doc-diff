"""Extract the OT op stream from a Google Docs /edit HTML page."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class KixModel:
    """The document's OT op stream as extracted from the /edit bootstrap."""

    ops: list[dict]
    revision: int
    model_version: int
    suggestion_colors: dict[str, str] = field(default_factory=dict)


def extract_ot_ops(html: str) -> KixModel | None:
    """Parse DOCS_modelChunk from /edit HTML.

    Returns None if the chunk isn't found (login page, redirect, etc.).
    """
    marker = "DOCS_modelChunk = "
    idx = html.find(marker)
    if idx < 0:
        return None
    start = html.find("{", idx)
    if start < 0:
        return None
    end = _find_closing_brace(html, start)
    if end < 0:
        return None
    try:
        raw = json.loads(html[start:end])
    except json.JSONDecodeError:
        return None
    ops = raw.get("chunk", [])
    revision = raw.get("revision", 0)
    mv = revision
    for op in reversed(ops):
        if op.get("ty") == "umv":
            mv = op.get("mv", revision)
            break
    suggestion_colors = raw.get("suggestionColors", {})
    return KixModel(
        ops=ops, revision=revision, model_version=mv, suggestion_colors=suggestion_colors
    )


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
