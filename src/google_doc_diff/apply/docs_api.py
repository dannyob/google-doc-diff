"""Translate OpPlan primitives to Docs API `batchUpdate` Request dicts.

Two surfaces:

  - `translate(ops, block_index, end_of_body)` is a pure function: given a
    list of primitives plus a snapshot of where each block_id lives in the
    doc, return a list of Docs API Request dicts. No I/O.
  - `apply(plan, doc_id, service)` runs the full pipeline: fetch current
    doc state, build block_index, translate, then call batchUpdate.

The translate function is the testable spine. `apply` is intentionally
thin so the policy / index logic can be unit-tested against fakes.

Limitations (overnight scope):

  - InsertBlock supports Paragraph + Heading + ListItem inserts. Tables /
    images / chips fall back to a no-op (TODO log).
  - ApplyStyle only the "text" scope is wired; paragraph/heading scopes
    will land with the named-style integration.
  - MoveBlock decomposes to delete + insert in `translate`.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from google_doc_diff.ast.nodes import (
    Heading,
    ListItem,
    Paragraph,
    Run,
    StyleDescriptor,
)
from google_doc_diff.ops.primitives import (
    ApplyStyle,
    DeleteBlock,
    DeleteRange,
    InsertBlock,
    InsertText,
    MoveBlock,
    OpPlan,
)

# --- translate -------------------------------------------------------------

# block_index maps:
#   block_id -> (text_start_index, text_end_index_exclusive)
# Indices are absolute character indices in the doc as understood by the
# Docs API. The end is exclusive, matching Docs API range semantics.
BlockIndex = dict[str, tuple[int, int]]


def translate(
    ops: Iterable,
    *,
    block_index: BlockIndex,
    end_of_body: int = 1,
) -> list[dict]:
    """Render a sequence of OpPlan primitives as a list of Docs API requests.

    `block_index` resolves block_ids to current absolute index ranges.
    `end_of_body` is the absolute index of the newline at end of body
    (i.e. the typical Docs API "doc end is index N" value). Used for
    inserts whose `after_id` is None (insert at top — actually at index 1
    in a fresh doc) and for inserts whose anchor is the last block.

    The function does NOT mutate indices in `block_index` as it goes; that
    bookkeeping happens in `apply()` after the batchUpdate succeeds.
    """
    reqs: list[dict] = []
    for op in ops:
        reqs.extend(_translate_op(op, block_index=block_index, end_of_body=end_of_body))
    return reqs


def _translate_op(op, *, block_index, end_of_body) -> list[dict]:
    if isinstance(op, InsertText):
        if op.block_id not in block_index:
            return []  # unresolvable; caller should have rebuilt the index
        start, _ = block_index[op.block_id]
        loc = start + op.offset
        out: list[dict] = [{
            "insertText": {"location": {"index": loc}, "text": op.text},
        }]
        if op.run_style is not None:
            out.extend(_style_requests(
                op.run_style, start_index=loc, end_index=loc + len(op.text),
            ))
        return out

    if isinstance(op, DeleteRange):
        if op.block_id not in block_index:
            return []
        start, _ = block_index[op.block_id]
        return [{
            "deleteContentRange": {"range": {
                "startIndex": start + op.start,
                "endIndex": start + op.end,
            }},
        }]

    if isinstance(op, ApplyStyle):
        if op.block_id not in block_index:
            return []
        start, _ = block_index[op.block_id]
        return _style_requests(
            op.style,
            start_index=start + op.start,
            end_index=start + op.end,
        )

    if isinstance(op, InsertBlock):
        return _translate_insert_block(op, block_index=block_index, end_of_body=end_of_body)

    if isinstance(op, DeleteBlock):
        if op.block_id not in block_index:
            return []
        start, end = block_index[op.block_id]
        return [{
            "deleteContentRange": {"range": {
                "startIndex": start,
                "endIndex": end,
            }},
        }]

    if isinstance(op, MoveBlock):
        # Naive decomposition. Real Docs API has a moveContent verb for some
        # objects; for paragraph moves we delete + reinsert.
        if op.block_id not in block_index:
            return []
        start, end = block_index[op.block_id]
        return [
            {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}},
            # The reinsert happens via a separate InsertBlock op that the
            # planner is expected to have emitted in the same plan. We don't
            # synthesise one here to keep translate as a pure 1-op-per-op
            # mapping.
        ]

    raise TypeError(f"unknown op {type(op).__name__}")


def _translate_insert_block(
    op: InsertBlock, *, block_index: BlockIndex, end_of_body: int,
) -> list[dict]:
    """Decompose an InsertBlock into insertText + style requests."""
    after_id = op.after_id
    if after_id is None:
        # Insert at very top of body.
        insert_at = 1
    elif after_id in block_index:
        _, end = block_index[after_id]
        # `end` is exclusive — i.e. the index just past the previous block's
        # trailing newline, which is where we want to insert.
        insert_at = end
    else:
        # Unresolvable anchor: fall back to end-of-body.
        insert_at = end_of_body

    block = op.block
    text, style_ranges, paragraph_style = _render_block_for_insert(block)
    reqs: list[dict] = []
    if not text:
        return reqs
    reqs.append({"insertText": {
        "location": {"index": insert_at},
        "text": text,
    }})
    # Apply per-run style.
    for rel_start, rel_end, fmt in style_ranges:
        if fmt is None:
            continue
        reqs.extend(_style_requests(
            fmt,
            start_index=insert_at + rel_start,
            end_index=insert_at + rel_end,
        ))
    # Apply paragraph-level style (e.g. HEADING_1) to the newly-inserted range.
    if paragraph_style:
        reqs.append({"updateParagraphStyle": {
            "range": {
                "startIndex": insert_at,
                "endIndex": insert_at + len(text),
            },
            "paragraphStyle": paragraph_style,
            "fields": ",".join(sorted(paragraph_style.keys())),
        }})
    return reqs


def _render_block_for_insert(block):
    """Return (text, style_ranges, paragraph_style) for an AST block insert.

    `style_ranges` is a list of (start_offset, end_offset, StyleDescriptor)
    in the block-local coordinate space. The final newline is included in
    `text` so the block is properly terminated for Docs.
    """
    if isinstance(block, (Paragraph, Heading)):
        runs: list[Run] = list(block.runs or [])
        parts: list[str] = []
        ranges: list[tuple[int, int, object]] = []
        off = 0
        for r in runs:
            if not r.text:
                continue
            parts.append(r.text)
            ranges.append((off, off + len(r.text), r.formatting))
            off += len(r.text)
        text = "".join(parts) + "\n"
        paragraph_style: dict = {}
        if isinstance(block, Heading) and 1 <= block.level <= 6:
            paragraph_style["namedStyleType"] = f"HEADING_{block.level}"
        return text, ranges, paragraph_style

    if isinstance(block, ListItem):
        runs = list(block.runs or [])
        text = "".join(r.text for r in runs) + "\n"
        ranges = []
        off = 0
        for r in runs:
            if not r.text:
                continue
            ranges.append((off, off + len(r.text), r.formatting))
            off += len(r.text)
        # ListItem styling happens via createParagraphBullets in the apply
        # post-pass; for now we just insert text and let the bullets be added
        # at a later iteration.
        return text, ranges, {}

    # Other block kinds: not yet supported — return empty insert.
    return "", [], {}


# --- style helpers --------------------------------------------------------


def _style_requests(style, *, start_index: int, end_index: int) -> list[dict]:
    if isinstance(style, StyleDescriptor):
        return _text_style_requests(style, start_index=start_index, end_index=end_index)
    # ParagraphProperties and dict-shaped styles aren't wired yet.
    return []


_TEXT_STYLE_FIELDS = (
    "bold", "italic", "underline", "strikethrough",
)


def _text_style_requests(
    s: StyleDescriptor, *, start_index: int, end_index: int,
) -> list[dict]:
    fields = []
    text_style: dict = {}
    for f in _TEXT_STYLE_FIELDS:
        v = getattr(s, f, None)
        if v is not None:
            text_style[f] = v
            fields.append(f)
    if s.foreground_color:
        text_style["foregroundColor"] = {"color": {"rgbColor": _rgb(s.foreground_color)}}
        fields.append("foregroundColor")
    if s.background_color:
        text_style["backgroundColor"] = {"color": {"rgbColor": _rgb(s.background_color)}}
        fields.append("backgroundColor")
    if s.font_family or s.font_size_pt is not None or s.weight is not None:
        wf: dict = {}
        if s.font_family:
            wf["fontFamily"] = s.font_family
        if s.weight is not None:
            wf["weight"] = s.weight
        text_style["weightedFontFamily"] = wf
        fields.append("weightedFontFamily")
        if s.font_size_pt is not None:
            text_style["fontSize"] = {"magnitude": s.font_size_pt, "unit": "PT"}
            fields.append("fontSize")
    if s.link_url:
        text_style["link"] = {"url": s.link_url}
        fields.append("link")
    if not fields:
        return []
    return [{
        "updateTextStyle": {
            "range": {"startIndex": start_index, "endIndex": end_index},
            "textStyle": text_style,
            "fields": ",".join(sorted(set(fields))),
        },
    }]


def _rgb(hex_str: str) -> dict:
    s = hex_str.lstrip("#")
    if len(s) != 6:
        return {}
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return {"red": r, "green": g, "blue": b}


# --- apply runner --------------------------------------------------------


def build_block_index_from_docs_document(doc: dict) -> tuple[BlockIndex, int]:
    """Walk a Docs API documents.get() response and assign each paragraph an id.

    For overnight scope we use *positional* synthetic ids `p-0`, `p-1`, ...
    keyed by document order. That's good enough for create-from-scratch
    (where the document is built fresh and we know the order) but will be
    refined when push merges against an existing doc that already has
    paragraph_ids in its markdown counterpart.
    """
    body = doc.get("body", {}).get("content", []) or []
    idx: BlockIndex = {}
    p_count = 0
    end = 1
    for el in body:
        para = el.get("paragraph")
        if para is None:
            end = el.get("endIndex", end)
            continue
        start = el.get("startIndex", 1)
        end_ix = el.get("endIndex", start)
        idx[f"p-{p_count}"] = (start, end_ix)
        p_count += 1
        end = end_ix
    return idx, end


def apply(
    plan: OpPlan,
    *,
    doc_id: str,
    service,
    block_index_resolver: Callable[[dict], tuple[BlockIndex, int]] = build_block_index_from_docs_document,
) -> dict:
    """Run a Docs API channel apply pass against `service`.

    Steps:
      1. Fetch the current doc (so we know where each block lives).
      2. Build a block_index from the response.
      3. Translate ops -> requests.
      4. POST batchUpdate.

    `service` is a googleapiclient discovery `Resource` (the Docs API client)
    or any duck-typed stand-in (e.g. our `FakeDocsService` in tests).
    """
    if not plan:
        return {}
    doc = service.documents().get(documentId=doc_id).execute()
    block_index, end_of_body = block_index_resolver(doc)
    reqs = translate(plan, block_index=block_index, end_of_body=end_of_body)
    if not reqs:
        return {}
    return service.documents().batchUpdate(
        documentId=doc_id, body={"requests": reqs},
    ).execute()
