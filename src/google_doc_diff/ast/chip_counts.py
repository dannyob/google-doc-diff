"""Recover rendered content of inline widgets (votes, reactions, dropdowns,
date chips, etc.) by cross-referencing Google's markdown export.

Docs encodes every API-invisible inline widget as a single PUA codepoint
(U+E907) in textRun.content. The markdown export shows the resolved value
('(➕ 3)', 'Standard White (#FFFFFF)', etc.); we walk the AST and stream
through the export, anchored on surrounding paragraph text.
"""

from __future__ import annotations

import re

from google_doc_diff.ast.nodes import Document, Run, SmartChip


def attach_widget_renderings(doc: Document, md: str) -> int:
    """For each widget SmartChip in the AST, attach its rendered form."""
    md_norm = _normalize(md)
    pos = 0
    last_seen_anchor: str | None = None
    resolved = 0
    for chip, preceding in _walk_widgets(doc):
        anchor = _normalize((preceding or "")[-40:].strip())
        if not anchor:
            new_pos = pos
        else:
            idx = md_norm.find(anchor, pos)
            if idx >= 0:
                new_pos = idx + len(anchor)
                last_seen_anchor = anchor
            elif anchor == last_seen_anchor:
                # Same context as the previous chip; continue from current pos
                # rather than re-search.
                new_pos = pos
            else:
                # Novel anchor not found ahead — skip rather than risk
                # attaching the wrong rendering.
                continue
        rendered, end = _capture_widget(md_norm, new_pos)
        if not rendered:
            continue
        emoji, count = _parse_chip_rendering(rendered)
        chip.data["rendered"] = rendered
        if emoji is not None:
            chip.data["emoji"] = emoji
        if count is not None:
            chip.data["count"] = count
        chip.kind = _classify(rendered, emoji)
        chip.display_text = rendered
        pos = end
        resolved += 1
    return resolved


_INLINE_SKIP = set(" \t\n\\*_")


def _capture_widget(md: str, pos: int) -> tuple[str, int]:
    """Skip whitespace, emphasis markers, blank lines, AND complete
    heading lines (because those are page structure, not widget renderings),
    then optionally cross a single table-cell boundary, then capture content
    up to the next newline or `|`."""
    while True:
        while pos < len(md) and md[pos] in _INLINE_SKIP:
            pos += 1
        if pos < len(md) and md[pos] == "#":
            nl = md.find("\n", pos)
            pos = nl + 1 if nl >= 0 else len(md)
            continue
        break
    if pos < len(md) and md[pos] == "|":
        pos += 1
        while pos < len(md) and md[pos] in _INLINE_SKIP:
            pos += 1
    if pos >= len(md):
        return "", pos
    end = pos
    while end < len(md) and md[end] not in "\n|":
        end += 1
    rendered = md[pos:end].strip(" \t\\*_")
    return rendered, end


def _walk_widgets(doc: Document):
    """Yield (chip, preceding_text_anchor) for every widget chip in order."""

    state = {"prev_block_tail": ""}

    def emit_runs(runs):
        prev = ""
        for r in runs:
            if isinstance(r, SmartChip) and r.data.get("glyph") == "U+E907":
                yield r, (prev if prev.strip() else state["prev_block_tail"])
            elif isinstance(r, Run):
                prev += r.text
            elif hasattr(r, "runs"):
                yield from emit_runs(r.runs)

    def block_tail(block) -> str:
        runs = getattr(block, "runs", None) or []
        text = ""
        for r in runs:
            if isinstance(r, Run):
                text += r.text
            elif hasattr(r, "runs"):
                for rr in r.runs:
                    if isinstance(rr, Run):
                        text += rr.text
        return text[-60:].strip()

    def walk_blocks(blocks):
        for block in blocks:
            runs = getattr(block, "runs", None)
            if runs is not None:
                yield from emit_runs(runs)
                tail = block_tail(block)
                if tail:
                    state["prev_block_tail"] = tail
            elif hasattr(block, "rows"):
                for row in block.rows:
                    for cell in row.cells:
                        yield from walk_blocks(cell.blocks)

    def walk_tabs(tabs):
        for tab in tabs:
            yield from walk_blocks(tab.blocks)
            yield from walk_tabs(tab.children)

    yield from walk_tabs(doc.tabs)


def _normalize(s: str) -> str:
    """Strip Pandoc-flavor backslash escapes so JSON anchors line up with
    the exported markdown for substring search."""
    return re.sub(r"\\(?=[#*_\[\](){}+\-.!|`'\"~])", "", s)


_VOTE_RE = re.compile(r"^\(([^()\s]{1,8}?)\s+(\d+)\\?\)$")


def _parse_chip_rendering(rendered: str) -> tuple[str | None, int | None]:
    m = _VOTE_RE.match(rendered.strip())
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


_EMOJI_KIND = {
    "➕": "vote-thumbsup",
    "➖": "vote-thumbsdown",
    "👍": "reaction-thumbsup",
    "👎": "reaction-thumbsdown",
    "❤️": "reaction-heart",
    "❤": "reaction-heart",
    "🚀": "reaction-rocket",
    "🎉": "reaction-party",
    "💡": "reaction-idea",
    "🔥": "reaction-fire",
    "👀": "reaction-eyes",
    "💯": "reaction-100",
}


def _classify(rendered: str, emoji: str | None) -> str:
    if emoji is not None:
        return _EMOJI_KIND.get(emoji, "reaction-other")
    r = rendered.strip()
    if re.match(r"^[A-Za-z0-9 _\-]+\s*\(\\?#?[0-9A-Fa-f]{3,8}\)$", r):
        return "dropdown-color"
    if re.match(r"^\d{4}-\d{2}-\d{2}", r) or re.match(r"^[A-Z][a-z]{2}\s\d", r):
        return "date"
    return "widget"
