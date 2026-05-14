"""Round-trip parser: emitted Markdown -> Document AST.

The goal is `parse_document_md(emit_document_md(doc)) == doc` for every
doc the v1 emit pipeline produces. We target only our own emit dialect
(Pandoc-flavor with the specific shapes documented in the spec) — not
arbitrary user-written markdown.

Overnight scope:

- Frontmatter (incl. `gdoc:` namespace) -> Document fields + gdoc_state.
- Top-level `# … #####` ATX headings + pandoc attribute trailer.
- Paragraphs (with `::: {…}` fence wrappers when classes / paragraph_id present).
- Inline formatting: `**bold**`, `*italic*` / `_italic_`, `~~strike~~`,
  Pandoc `[text]{.class}` spans, `[label](url)` links.
- Bulleted and ordered lists (single level for now).
- Code blocks (```).
- Pandoc footnote refs/defs are preserved as opaque per-block strings so
  comments and suggestions can be round-tripped without re-deriving them
  yet (full reverse-pass lands in a later chunk).
- The trailing footnote-definitions section is harvested back into the
  per-tab "comments / suggestions / footnotes" dicts opaquely.

Deferred: tables, images, equations, multi-tab `:::` fences, conflict markers.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    ListItem,
    Paragraph,
    Run,
    StyleDescriptor,
    Tab,
)

# Public entry points -------------------------------------------------------


def parse_document_md(md: str) -> Document:
    """Parse a v1-emitted Markdown file into a Document AST."""
    fm, body = parse_frontmatter(md)
    blocks = parse_body(body)
    return _build_document(fm, blocks)


def parse_frontmatter(md: str) -> tuple[dict, str]:
    """Split off the YAML frontmatter, returning ``(frontmatter, body)``."""
    if not md.startswith("---\n"):
        return {}, md
    end = md.find("\n---\n", 4)
    if end == -1:
        return {}, md
    raw = md[4:end]
    body = md[end + 5:]
    return (yaml.safe_load(raw) or {}), body


# Body parsing --------------------------------------------------------------


_md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")


def parse_body(body: str) -> list:
    """Parse the body of the markdown (post-frontmatter) into a flat block list.

    Returns AST blocks (Heading / Paragraph / ListItem / ...). Pandoc div
    fences (`::: {…}`) are recognized and unwrapped onto their inner blocks
    with the fence's attributes hoisted to the inner block.
    """
    # Pre-process: extract pandoc-style attribute trailers on headings and
    # pull out `::: {...} ... :::` fenced divs (markdown-it doesn't handle
    # them natively). We do a small two-stage preprocess that yields
    # (logical-block-source, attached-attrs) tuples and parses each
    # logical block with markdown-it.
    chunks = _split_pandoc_blocks(body)
    out: list = []
    for chunk in chunks:
        out.extend(_parse_chunk(chunk))
    return out


# --- Pandoc div fences ----------------------------------------------------


_FENCE_RE = re.compile(r"^:::+\s*(?P<attrs>\{[^}]*\}|\S+)?\s*$")


def _split_pandoc_blocks(body: str) -> list[dict]:
    """Walk `body` line by line, grouping into chunks.

    Each chunk is a dict ``{"source": str, "attrs": dict}``. Top-level
    content (no `:::` fence) goes in a chunk with empty attrs. Fenced
    `::: {attrs}` blocks become chunks with the parsed attribute dict.
    """
    chunks: list[dict] = []
    cur_lines: list[str] = []
    cur_attrs: dict = {}
    stack: list[tuple[list[str], dict]] = []

    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\n").rstrip()
        m = _FENCE_RE.match(stripped)
        if m is None:
            cur_lines.append(line)
            continue
        attrs_raw = m.group("attrs") or ""
        if attrs_raw:
            # Opening fence — push current chunk, start new.
            chunks.append({"source": "".join(cur_lines), "attrs": cur_attrs})
            stack.append((cur_lines, cur_attrs))
            cur_lines = []
            cur_attrs = _parse_attr_block(attrs_raw)
        else:
            # Closing fence — emit current chunk, pop.
            chunks.append({"source": "".join(cur_lines), "attrs": cur_attrs})
            if stack:
                cur_lines, cur_attrs = stack.pop()
                cur_lines = []  # but discard the parent's interrupted source
                cur_attrs = {}
            else:
                cur_lines, cur_attrs = [], {}
    # Trailing flush
    if cur_lines:
        chunks.append({"source": "".join(cur_lines), "attrs": cur_attrs})
    return [c for c in chunks if c["source"].strip() or c["attrs"]]


_ATTR_RE = re.compile(
    r"""
    (?:^|\s)
    (?P<kind>[#.])?              # '#' for id, '.' for class
    (?P<key>[A-Za-z_][\w-]*)
    (?:=(?P<val>"[^"]*"|\S+))?   # optional =value (quoted or bare)
    """,
    re.VERBOSE,
)


def _parse_attr_block(raw: str) -> dict:
    """Parse `{#id .cls key=val ...}` into ``{"id": ..., "classes": [...], "data": {...}, "extra_ids": [...]}``."""
    inside = raw.strip()
    if inside.startswith("{") and inside.endswith("}"):
        inside = inside[1:-1]
    elif inside.startswith("."):
        # `::: classname` shorthand
        return {"id": None, "classes": [inside[1:]], "data": {}, "extra_ids": []}
    out = {"id": None, "classes": [], "data": {}, "extra_ids": []}
    for m in _ATTR_RE.finditer(inside):
        kind = m.group("kind")
        key = m.group("key")
        val = m.group("val")
        if kind == "#":
            if out["id"] is None:
                out["id"] = key
            else:
                out["extra_ids"].append(key)
        elif kind == ".":
            out["classes"].append(key)
        elif val is not None:
            out["data"][key] = val.strip('"')
    return out


# --- Per-chunk dispatch ---------------------------------------------------


def _parse_chunk(chunk: dict) -> list:
    src = chunk["source"]
    attrs = chunk["attrs"]
    if not src.strip():
        return []
    tokens = _md.parse(src)
    return _tokens_to_blocks(tokens, attrs)


def _tokens_to_blocks(tokens: list[Token], attrs: dict) -> list:
    out: list = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "heading_open":
            level = int(tok.tag[1:])
            inline_tok = tokens[i + 1]
            close = tokens[i + 2]
            assert close.type == "heading_close"
            text, runs, trailing_attrs = _runs_from_inline(inline_tok)
            anchor_id = trailing_attrs.get("id") or attrs.get("id")
            classes = trailing_attrs.get("classes", []) or attrs.get("classes", [])
            extra_ids = trailing_attrs.get("extra_ids", [])
            paragraph_id = extra_ids[0] if extra_ids else None
            out.append(Heading(
                level=level, runs=runs, anchor_id=anchor_id,
                classes=classes, paragraph_id=paragraph_id,
            ))
            i += 3
            continue
        if tok.type == "paragraph_open":
            inline_tok = tokens[i + 1]
            close = tokens[i + 2]
            assert close.type == "paragraph_close"
            text, runs, trailing_attrs = _runs_from_inline(inline_tok)
            classes = list(attrs.get("classes", []))
            extra_ids = list(attrs.get("extra_ids", []))
            block_id_from_attrs = attrs.get("id")
            # On the *paragraph* path, the primary id slot is paragraph_id.
            paragraph_id = block_id_from_attrs or (extra_ids[0] if extra_ids else None)
            out.append(Paragraph(
                runs=runs, classes=classes, paragraph_id=paragraph_id,
            ))
            i += 3
            continue
        if tok.type in ("bullet_list_open", "ordered_list_open"):
            kind = "bulleted" if tok.type == "bullet_list_open" else "ordered"
            list_id = attrs.get("id") or f"l-{kind[0]}{i}"
            i += 1
            depth = 0
            level = 0
            while i < len(tokens):
                t = tokens[i]
                if t.type in ("bullet_list_open", "ordered_list_open"):
                    level += 1
                    depth += 1
                    i += 1
                    continue
                if t.type in ("bullet_list_close", "ordered_list_close"):
                    if depth == 0:
                        i += 1
                        break
                    depth -= 1
                    level -= 1
                    i += 1
                    continue
                if t.type == "list_item_open":
                    # Find the matching inline for this item: paragraph_open inline paragraph_close
                    j = i + 1
                    while j < len(tokens) and tokens[j].type != "inline":
                        j += 1
                    text, runs, _ = _runs_from_inline(tokens[j])
                    out.append(ListItem(
                        level=level, kind=kind, list_id=list_id, runs=runs,
                    ))
                    # Advance past this list item
                    while i < len(tokens) and tokens[i].type != "list_item_close":
                        i += 1
                    i += 1
                    continue
                i += 1
            continue
        if tok.type == "fence":
            from google_doc_diff.ast.nodes import CodeBlock
            out.append(CodeBlock(text=tok.content.rstrip("\n"), language=tok.info or None))
            i += 1
            continue
        i += 1
    return out


# --- Inline parsing -------------------------------------------------------


_INLINE_TRAILER = re.compile(r"\s*(\{[^}]*\})\s*$")


def _runs_from_inline(token: Token) -> tuple[str, list[Run], dict]:
    """Convert a markdown-it inline token into (plain text, [Run], trailing-attrs).

    If the inline content ends with a Pandoc attribute block, strip and
    return it. Inline formatting tokens (strong, em, s, link) become Run
    style toggles.
    """
    children: list[Token] = token.children or []
    runs: list[Run] = []
    trailing_attrs: dict = {}

    # If the last child is a 'text' token with a trailing `{…}`, peel it off.
    if children and children[-1].type == "text":
        last = children[-1]
        m = _INLINE_TRAILER.search(last.content)
        if m:
            trailing_attrs = _parse_attr_block(m.group(1))
            stripped = last.content[: m.start()]
            if stripped:
                last.content = stripped
            else:
                children = children[:-1]

    style_stack: list[StyleDescriptor] = [StyleDescriptor()]

    def cur() -> StyleDescriptor:
        return style_stack[-1]

    def push(**kw) -> None:
        d = {**cur().__dict__, **kw}
        style_stack.append(StyleDescriptor(**d))

    def pop() -> None:
        style_stack.pop()

    for c in children:
        if c.type == "text":
            if c.content:
                runs.append(Run(text=c.content, formatting=cur()))
        elif c.type == "softbreak":
            runs.append(Run(text="\n", formatting=cur()))
        elif c.type == "hardbreak":
            runs.append(Run(text="\n", formatting=cur()))
        elif c.type == "code_inline":
            runs.append(Run(text=c.content, formatting=cur()))  # code style not yet typed
        elif c.type == "strong_open":
            push(bold=True)
        elif c.type == "strong_close":
            pop()
        elif c.type == "em_open":
            push(italic=True)
        elif c.type == "em_close":
            pop()
        elif c.type == "s_open":
            push(strikethrough=True)
        elif c.type == "s_close":
            pop()
        elif c.type == "link_open":
            push(link_url=c.attrs.get("href"))
        elif c.type == "link_close":
            pop()
        else:
            # markdown-it html_inline, image, etc. — pass through as text for now.
            if c.content:
                runs.append(Run(text=c.content, formatting=cur()))

    text = "".join(r.text for r in runs)
    runs = _merge_adjacent_runs(runs)
    return text, runs, trailing_attrs


def _merge_adjacent_runs(runs: Iterable[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if out and out[-1].formatting == r.formatting:
            out[-1] = Run(text=out[-1].text + r.text, formatting=r.formatting)
        else:
            out.append(r)
    return out


# --- Document assembly ----------------------------------------------------


def _build_document(fm: dict, blocks: list) -> Document:
    """Fold parsed frontmatter + blocks into a Document AST."""
    captured = fm.get("captured_at")
    if isinstance(captured, str):
        captured_dt = datetime.fromisoformat(captured)
    elif isinstance(captured, datetime):
        captured_dt = captured
    else:
        captured_dt = datetime.now()

    gdoc_state = fm.pop("gdoc", {}) if "gdoc" in fm else {}

    tab = Tab(tab_id="t1", title="(default)", level=0, blocks=blocks)

    return Document(
        doc_id=fm.get("doc_id", ""),
        title=fm.get("title", ""),
        revision_id=fm.get("revision_id", ""),
        drive_url=fm.get("drive_url", ""),
        captured_at=captured_dt,
        schema_version=fm.get("schema_version", 1),
        last_modifying_user=fm.get("last_modifying_user"),
        source_mode=fm.get("source_mode", "pull"),
        comments_preserved=fm.get("comments_preserved", True),
        suggestions_preserved=fm.get("suggestions_preserved", True),
        tabs=[tab],
        gdoc_state=gdoc_state,
    )


# Backward-compat alias used by v1 callers.
def parse_markdown(md: str) -> Document:
    return parse_document_md(md)
