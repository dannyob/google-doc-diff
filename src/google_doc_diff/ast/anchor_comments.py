"""Anchor Drive comments into the AST by text-matching their quoted snippets.

The Drive Comments API exposes each comment's anchor as `quotedFileContent.value`
(the text the comment was anchored to at creation time) plus an opaque
`anchor` token that we can't resolve to a structural position. So we
text-search for the snippet across the AST and wrap the matching runs in
a `CommentAnchor`. Comments whose snippet can't be found are marked
`orphaned=True` and rendered at the end of the containing tab anyway.
"""

from __future__ import annotations

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


def anchor_comments(doc: Document) -> Document:
    """Walk doc.tabs and wrap comment-anchored text in CommentAnchor nodes.

    Mutates the AST in place. Comments still appear in `doc.comments`; this
    just ensures they have a referrer in the prose so the emitter renders
    them. Comments whose snippet can't be found — or whose snippet appears
    more than once in the doc — are marked orphaned. The multiple-match
    guard prevents stale comments on recurring-boilerplate docs (weekly
    meeting notes, etc.) from anchoring to the wrong week's content.
    """
    full_text = _full_text(doc.tabs)
    for cmt in doc.comments.values():
        if cmt.deleted or not cmt.quoted_text:
            continue
        if full_text.count(cmt.quoted_text) != 1:
            cmt.orphaned = True
            continue
        if not _try_anchor_in_tabs(doc.tabs, cmt):
            cmt.orphaned = True
    return doc


def _full_text(tabs: list[Tab]) -> str:
    """Concatenate all Run text across all tabs for ambiguity detection."""
    parts: list[str] = []

    def _walk_blocks(blocks):
        for block in blocks:
            if isinstance(block, (Paragraph, Heading, ListItem)):
                for r in block.runs:
                    if isinstance(r, Run):
                        parts.append(r.text)
            elif isinstance(block, Table):
                for row in block.rows:
                    if isinstance(row, Row):
                        for cell in row.cells:
                            if isinstance(cell, Cell):
                                _walk_blocks(cell.blocks)

    for tab in tabs:
        _walk_blocks(tab.blocks)
        for child in tab.children:
            _walk_blocks(child.blocks)
    return "".join(parts)


def _try_anchor_in_tabs(tabs: list[Tab], cmt: Comment) -> bool:
    for tab in tabs:
        if _try_anchor_in_blocks(tab.blocks, cmt):
            return True
        if _try_anchor_in_tabs(tab.children, cmt):
            return True
    return False


def _try_anchor_in_blocks(blocks: list, cmt: Comment) -> bool:
    for block in blocks:
        if isinstance(block, Paragraph | Heading | ListItem):
            new_runs = _wrap_in_runs(block.runs, cmt)
            if new_runs is not None:
                block.runs = new_runs
                return True
        elif isinstance(block, Table):
            if _try_anchor_in_table(block, cmt):
                return True
    return False


def _try_anchor_in_table(t: Table, cmt: Comment) -> bool:
    for row in t.rows:
        if not isinstance(row, Row):
            continue
        for cell in row.cells:
            if not isinstance(cell, Cell):
                continue
            if _try_anchor_in_blocks(cell.blocks, cmt):
                return True
    return False


def _wrap_in_runs(runs: list, cmt: Comment) -> list | None:
    """If `cmt.quoted_text` appears (possibly across consecutive Runs),
    return a new run list with a CommentAnchor wrapping the match.

    Returns None if no match.
    """
    snippet = cmt.quoted_text
    if not snippet:
        return None

    # Try each contiguous Run-only window starting at i.
    for i in range(len(runs)):
        if not isinstance(runs[i], Run):
            continue
        # Concatenate text from i onward (only Runs); track byte ranges
        # back to (run_index, char_index_in_run).
        joined_chars: list[tuple[int, int, str]] = []   # (run_idx, char_idx, ch)
        j = i
        while j < len(runs) and isinstance(runs[j], Run):
            for k, ch in enumerate(runs[j].text):
                joined_chars.append((j, k, ch))
            j += 1
        joined = "".join(c for _, _, c in joined_chars)
        idx = joined.find(snippet)
        if idx < 0:
            continue
        end_idx = idx + len(snippet)
        # Reconstruct: prefix runs (untouched), the CommentAnchor wrapping
        # the matched chars (split runs at boundaries as needed), suffix
        # runs (untouched).
        return _splice_anchor(runs, joined_chars, idx, end_idx, cmt.comment_id)
    return None


def _splice_anchor(
    runs: list,
    joined_chars: list[tuple[int, int, str]],
    char_start: int,
    char_end: int,
    comment_id: str,
) -> list:
    """Construct a new run list with the [char_start:char_end] window from
    joined_chars wrapped in a CommentAnchor, splitting runs at the boundaries."""
    if char_start == char_end:
        return runs

    start_run, start_char, _ = joined_chars[char_start]
    end_run, end_char, _ = joined_chars[char_end - 1]

    out: list = list(runs[:start_run])

    # Pre-split: portion of start_run BEFORE the match
    head_text = runs[start_run].text[:start_char]
    if head_text:
        out.append(_run_with_text(runs[start_run], head_text))

    # The CommentAnchor's runs: matched portion across start_run..end_run
    inner: list = []
    for r_idx in range(start_run, end_run + 1):
        r = runs[r_idx]
        text = r.text
        lo = start_char if r_idx == start_run else 0
        hi = (end_char + 1) if r_idx == end_run else len(text)
        if hi > lo:
            inner.append(_run_with_text(r, text[lo:hi]))
    out.append(CommentAnchor(comment_id=comment_id, runs=inner))

    # Tail: portion of end_run AFTER the match
    tail_text = runs[end_run].text[end_char + 1:]
    if tail_text:
        out.append(_run_with_text(runs[end_run], tail_text))

    out.extend(runs[end_run + 1:])
    return out


def _run_with_text(template: Run, text: str) -> Run:
    return Run(text=text, formatting=template.formatting or StyleDescriptor())
