"""Anchor Drive comments into the AST by text-matching their quoted snippets.

The Drive Comments API exposes each comment's anchor as `quotedFileContent.value`
(the text the comment was anchored to at creation time) plus an opaque
`anchor` token (`kix.<paragraph_id>`) that the *public* Docs API can't resolve
to a structural position. So we text-search for the snippet across the AST and
wrap the matching runs in a `CommentAnchor`.

When a snippet appears multiple times (common in recurring meeting docs where
boilerplate repeats weekly), we use **ordered pairing**: group comments by
snippet, sort the comments by date (newest first) and the occurrences by
position (first = top of doc = newest), then zip. This matches newest comments
to the newest section and oldest comments to the oldest section — correct for
chronological docs.

When a `kix_resolver` is provided (backed by Chrome cookie auth), it can
resolve `kix.<id>` anchors to exact paragraph positions, bypassing text
matching entirely. This gives perfect accuracy but requires browser-session
access.

Comments whose snippet can't be found or paired are marked `orphaned=True`
and rendered at the end of the containing tab anyway.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from google_doc_diff.ast.nodes import (
    Cell,
    Comment,
    CommentAnchor,
    Document,
    Heading,
    ListItem,
    Paragraph,
    Row,
    Run,
    StyleDescriptor,
    Tab,
    Table,
)

AnchorResolver = Callable[[str], int | None]


def anchor_comments(
    doc: Document,
    *,
    kix_resolver: AnchorResolver | None = None,
) -> Document:
    """Walk doc.tabs and wrap comment-anchored text in CommentAnchor nodes.

    Args:
        doc: The document AST (mutated in place).
        kix_resolver: Optional callable ``(kix_anchor: str) -> block_index | None``.
            When provided, resolves ``kix.<id>`` anchor strings to a block
            index in the first tab's block list, giving exact positioning
            without text matching. Pass ``None`` to use the text-matching
            fallback.
    """
    active = [
        c for c in doc.comments.values()
        if not c.deleted and c.quoted_text
    ]
    if not active:
        return doc

    if kix_resolver:
        _anchor_via_resolver(doc.tabs, active, kix_resolver)
    else:
        _anchor_via_text_matching(doc.tabs, active)

    return doc


# --- resolver path (exact, needs Chrome cookies) --------------------------


def _anchor_via_resolver(
    tabs: list[Tab], comments: list[Comment], resolver: AnchorResolver,
) -> None:
    """Use the kix_resolver to find exact block positions."""
    if not tabs:
        return
    blocks = tabs[0].blocks
    for cmt in comments:
        if not cmt.anchor:
            cmt.orphaned = True
            continue
        block_idx = resolver(cmt.anchor)
        if block_idx is None or block_idx >= len(blocks):
            cmt.orphaned = True
            continue
        block = blocks[block_idx]
        if not isinstance(block, (Paragraph, Heading, ListItem)):
            cmt.orphaned = True
            continue
        new_runs = _wrap_in_runs(block.runs, cmt)
        if new_runs is not None:
            block.runs = new_runs
        else:
            cmt.orphaned = True


# --- text-matching path (public API only) ---------------------------------


def _anchor_via_text_matching(tabs: list[Tab], comments: list[Comment]) -> None:
    """Text-match with ordered pairing for ambiguous snippets."""
    snippets = {c.quoted_text for c in comments}
    occurrence_map = _build_occurrence_map_for_snippets(tabs, snippets)

    by_snippet: dict[str, list[Comment]] = defaultdict(list)
    for cmt in comments:
        by_snippet[cmt.quoted_text].append(cmt)

    for snippet, cmts in by_snippet.items():
        occurrences = occurrence_map.get(snippet, [])
        if not occurrences:
            for c in cmts:
                c.orphaned = True
            continue

        if len(occurrences) == 1:
            # Unambiguous — anchor all comments quoting this snippet to the
            # single occurrence (they may be replies across time to the same
            # anchored text).
            block, run_start = occurrences[0]
            for c in cmts:
                new_runs = _wrap_in_runs(block.runs, c)
                if new_runs is not None:
                    block.runs = new_runs
                else:
                    c.orphaned = True
            continue

        # Multiple occurrences — ordered pairing. Sort comments newest-
        # first and pair them with occurrences bottom-up. In a newest-at-
        # top chronological doc this means the newest comment gets the
        # lowest occurrence (which is the newest section's instance),
        # and old comments land near old content at the bottom.
        cmts_sorted = sorted(cmts, key=lambda c: c.created_time, reverse=True)
        # Align from the bottom: if 2 comments and 6 occurrences, pair
        # with occurrences[4] and occurrences[5] (the two oldest).
        offset = max(0, len(occurrences) - len(cmts_sorted))
        for i, c in enumerate(cmts_sorted):
            occ_idx = offset + i
            if occ_idx < len(occurrences):
                block, _ = occurrences[occ_idx]
                new_runs = _wrap_in_runs(block.runs, c)
                if new_runs is not None:
                    block.runs = new_runs
                else:
                    c.orphaned = True
            else:
                c.orphaned = True


def _build_occurrence_map_for_snippets(
    tabs: list[Tab],
    snippets: set[str],
) -> dict[str, list[tuple[object, int]]]:
    """Map each snippet to its (block, char_offset) occurrences in doc order."""
    out: dict[str, list[tuple[object, int]]] = defaultdict(list)

    def _scan_blocks(blocks):
        for block in blocks:
            if isinstance(block, (Paragraph, Heading, ListItem)):
                text = _block_run_text(block.runs)
                for snippet in snippets:
                    idx = 0
                    while True:
                        idx = text.find(snippet, idx)
                        if idx < 0:
                            break
                        out[snippet].append((block, idx))
                        idx += len(snippet)
            elif isinstance(block, Table):
                for row in block.rows:
                    if isinstance(row, Row):
                        for cell in row.cells:
                            if isinstance(cell, Cell):
                                _scan_blocks(cell.blocks)

    for tab in tabs:
        _scan_blocks(tab.blocks)
        for child in tab.children:
            _scan_blocks(child.blocks)
    return out


def _block_run_text(runs: list) -> str:
    return "".join(r.text for r in runs if isinstance(r, Run))


# --- shared: wrapping runs in a CommentAnchor -----------------------------


def _wrap_in_runs(runs: list, cmt: Comment) -> list | None:
    """If `cmt.quoted_text` appears (possibly across consecutive Runs),
    return a new run list with a CommentAnchor wrapping the match.

    Returns None if no match.
    """
    snippet = cmt.quoted_text
    if not snippet:
        return None

    # Flatten runs into chars, looking through existing CommentAnchors so
    # anchoring one comment doesn't make the text invisible to subsequent
    # comments on the same block.
    flat = _flatten_to_chars(runs)
    if not flat:
        return None
    joined = "".join(ch for _, _, ch in flat)
    idx = joined.find(snippet)
    if idx < 0:
        return None
    end_idx = idx + len(snippet)
    # If the matched range spans any CommentAnchor (i.e. text already
    # anchored by another comment), skip — nesting anchors is unsupported
    # and the text is already associated with a comment region.
    start_run_idx = flat[idx][0]
    end_run_idx = flat[end_idx - 1][0]
    for ri in range(start_run_idx, end_run_idx + 1):
        if isinstance(runs[ri], CommentAnchor):
            return None
    return _splice_anchor(runs, flat, idx, end_idx, cmt.comment_id)


def _flatten_to_chars(runs: list) -> list[tuple[int, int, str]]:
    """Flatten a run list to (run_idx, char_idx, char) triples.

    Looks through CommentAnchor wrappers so text already wrapped by a
    previous comment anchor is still findable by subsequent comments.
    Only flattens contiguous Run / CommentAnchor sequences — other
    inline types (SmartChip, etc.) break the window.
    """
    out: list[tuple[int, int, str]] = []
    for i, r in enumerate(runs):
        if isinstance(r, Run):
            for k, ch in enumerate(r.text):
                out.append((i, k, ch))
        elif isinstance(r, CommentAnchor):
            # Walk inner runs of the CommentAnchor — keep the outer run_idx
            # pointing at the CommentAnchor's position so _splice_anchor can
            # find it. The char indices are relative to the CommentAnchor's
            # inner text.
            inner_off = 0
            for inner_r in r.runs:
                if isinstance(inner_r, Run):
                    for k, ch in enumerate(inner_r.text):
                        out.append((i, inner_off + k, ch))
                    inner_off += len(inner_r.text)
    return out


def _splice_anchor(
    runs: list,
    joined_chars: list[tuple[int, int, str]],
    char_start: int,
    char_end: int,
    comment_id: str,
) -> list:
    if char_start == char_end:
        return runs

    start_run, start_char, _ = joined_chars[char_start]
    end_run, end_char, _ = joined_chars[char_end - 1]

    out: list = list(runs[:start_run])

    head_text = runs[start_run].text[:start_char]
    if head_text:
        out.append(_run_with_text(runs[start_run], head_text))

    inner: list = []
    for r_idx in range(start_run, end_run + 1):
        r = runs[r_idx]
        text = r.text
        lo = start_char if r_idx == start_run else 0
        hi = (end_char + 1) if r_idx == end_run else len(text)
        if hi > lo:
            inner.append(_run_with_text(r, text[lo:hi]))
    out.append(CommentAnchor(comment_id=comment_id, runs=inner))

    tail_text = runs[end_run].text[end_char + 1:]
    if tail_text:
        out.append(_run_with_text(runs[end_run], tail_text))

    out.extend(runs[end_run + 1:])
    return out


def _run_with_text(template: Run, text: str) -> Run:
    return Run(text=text, formatting=template.formatting or StyleDescriptor())
