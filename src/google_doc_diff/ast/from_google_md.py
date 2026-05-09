"""Lossy AST builder for Google's native markdown export.

Used by `gdoc pull --revision` and `gdoc replay` to consume per-revision
content fetched via Drive v2 exportLinks. Comments and suggestions are NOT
preserved by Google's markdown export (the rendered text strips both); the
caller must layer them in separately if needed.

Distinct from `parse/markdown.py`, which targets *our* flavored Pandoc
markdown for the v2 round-trip path.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from markdown_it import MarkdownIt

from google_doc_diff.ast.nodes import (
    Cell,
    Document,
    Heading,
    HorizontalRule,
    ListItem,
    Paragraph,
    Row,
    Run,
    StyleDescriptor,
    Tab,
    Table,
)


def build_from_google_md(
    md: str,
    *,
    doc_id: str,
    title: str = "",
    revision_id: str = "",
    drive_url: str = "",
    captured_at: datetime | None = None,
    last_modifying_user: str | None = None,
    source_mode: str = "pull",
) -> Document:
    """Parse Google-exported markdown into a Document AST.

    The result has comments_preserved=False and suggestions_preserved=False
    because Google's export strips both.
    """
    if captured_at is None:
        captured_at = datetime.now(UTC)
    if not drive_url and doc_id:
        drive_url = f"https://docs.google.com/document/d/{doc_id}/edit"

    parser = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    tokens = parser.parse(md)
    blocks = _walk_tokens(tokens)

    if not title:
        title = _first_heading_text(blocks) or "(untitled)"

    return Document(
        doc_id=doc_id,
        title=title,
        revision_id=revision_id,
        drive_url=drive_url,
        captured_at=captured_at,
        schema_version=1,
        last_modifying_user=last_modifying_user,
        source_mode=source_mode,
        comments_preserved=False,
        suggestions_preserved=False,
        tabs=[Tab(tab_id="t-default", title="(default)", level=0, blocks=blocks)],
    )


def _first_heading_text(blocks: list) -> str:
    for b in blocks:
        if isinstance(b, Heading):
            return "".join(r.text for r in b.runs if isinstance(r, Run))
    return ""


# --- token walker ---------------------------------------------------------


def _walk_tokens(tokens: list) -> list:
    """Convert a flat markdown-it token stream into AST blocks."""
    out: list = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        ttype = tok.type
        if ttype == "heading_open":
            level = int(tok.tag[1])
            inline = tokens[i + 1]
            runs = _inline_to_runs(inline)
            out.append(Heading(level=level, runs=runs))
            i += 3  # heading_open, inline, heading_close
        elif ttype == "paragraph_open":
            inline = tokens[i + 1]
            runs = _inline_to_runs(inline)
            out.append(Paragraph(runs=runs))
            i += 3
        elif ttype == "bullet_list_open":
            j, items = _consume_list(tokens, i, ordered=False, level=0)
            out.extend(items)
            i = j
        elif ttype == "ordered_list_open":
            j, items = _consume_list(tokens, i, ordered=True, level=0)
            out.extend(items)
            i = j
        elif ttype == "table_open":
            j, table = _consume_table(tokens, i)
            out.append(table)
            i = j
        elif ttype == "hr":
            out.append(HorizontalRule())
            i += 1
        elif ttype in ("html_block",):
            # Pass raw HTML through as a paragraph (rare in Google export).
            out.append(Paragraph(runs=[Run(text=tok.content.strip())]))
            i += 1
        elif ttype.endswith("_open") or ttype.endswith("_close"):
            i += 1  # silently skip blockquote / fence / etc. wrappers we don't model
        else:
            i += 1
    return out


def _consume_list(tokens: list, i: int, *, ordered: bool, level: int) -> tuple[int, list]:
    """Consume a {bullet|ordered}_list_open ... close; return (next_index, items)."""
    list_id = f"L{ordered}{level}"  # not stable across calls; reasonable placeholder
    kind = "ordered" if ordered else "bulleted"
    items: list = []
    j = i + 1                                          # past list_open
    while j < len(tokens):
        tok = tokens[j]
        if tok.type in ("bullet_list_close", "ordered_list_close"):
            return j + 1, items
        if tok.type == "list_item_open":
            # Walk children until matching list_item_close.
            k = j + 1
            depth = 1
            child_tokens: list = []
            while k < len(tokens) and depth > 0:
                if tokens[k].type == "list_item_open":
                    depth += 1
                elif tokens[k].type == "list_item_close":
                    depth -= 1
                    if depth == 0:
                        break
                child_tokens.append(tokens[k])
                k += 1
            # First paragraph is the item text; nested lists are children.
            item_runs: list[Run] = []
            nested: list = []
            cj = 0
            while cj < len(child_tokens):
                ct = child_tokens[cj]
                if ct.type == "paragraph_open":
                    item_runs = _inline_to_runs(child_tokens[cj + 1])
                    cj += 3
                elif ct.type in ("bullet_list_open", "ordered_list_open"):
                    sub_ordered = ct.type.startswith("ordered")
                    cnj, sub_items = _consume_list(child_tokens, cj,
                                                   ordered=sub_ordered, level=level + 1)
                    nested.extend(sub_items)
                    cj = cnj
                else:
                    cj += 1
            items.append(ListItem(level=level, kind=kind, list_id=list_id, runs=item_runs))
            items.extend(nested)
            j = k + 1
            continue
        j += 1
    return j, items


def _consume_table(tokens: list, i: int) -> tuple[int, Table]:
    rows: list[Row] = []
    j = i + 1
    current_cells: list[Cell] = []
    while j < len(tokens):
        tok = tokens[j]
        if tok.type == "table_close":
            return j + 1, Table(rows=rows)
        if tok.type == "tr_open":
            current_cells = []
        elif tok.type == "tr_close":
            rows.append(Row(cells=current_cells))
        elif tok.type in ("th_open", "td_open"):
            inline = tokens[j + 1]
            runs = _inline_to_runs(inline) if inline.type == "inline" else []
            current_cells.append(Cell(blocks=[Paragraph(runs=runs)]))
            j += 2  # past inline + th_close (we'll +=1 below)
        j += 1
    return j, Table(rows=rows)


# --- inline tokens -> Run list -------------------------------------------


def _inline_to_runs(inline_tok) -> list:
    """Walk inline children: text, em, strong, s, code_inline, link, softbreak."""
    children = inline_tok.children or []
    runs: list = []
    fmt_stack: list[StyleDescriptor] = [StyleDescriptor()]
    link_url: str | None = None

    def push(field: str):
        cur = fmt_stack[-1]
        new = StyleDescriptor(**{**_descriptor_dict(cur), field: True})
        fmt_stack.append(new)

    def pop():
        if len(fmt_stack) > 1:
            fmt_stack.pop()

    for tok in children:
        ttype = tok.type
        if ttype == "text":
            if not tok.content:
                continue
            d = _descriptor_dict(fmt_stack[-1])
            if link_url:
                d["link_url"] = link_url
            runs.append(Run(text=tok.content, formatting=StyleDescriptor(**d)))
        elif ttype == "strong_open":
            push("bold")
        elif ttype == "strong_close":
            pop()
        elif ttype == "em_open":
            push("italic")
        elif ttype == "em_close":
            pop()
        elif ttype == "s_open":
            push("strikethrough")
        elif ttype == "s_close":
            pop()
        elif ttype == "code_inline":
            d = _descriptor_dict(fmt_stack[-1])
            d["font_family"] = "monospace"
            runs.append(Run(text=tok.content, formatting=StyleDescriptor(**d)))
        elif ttype == "link_open":
            link_url = tok.attrGet("href") or ""
        elif ttype == "link_close":
            link_url = None
        elif ttype == "softbreak":
            runs.append(Run(text=" ", formatting=fmt_stack[-1]))
        elif ttype == "hardbreak":
            from google_doc_diff.ast.nodes import LineBreak
            runs.append(LineBreak())
        elif ttype == "html_inline":
            # Strip out raw <br> and similar; render plain.
            stripped = re.sub(r"<[^>]+>", "", tok.content)
            if stripped:
                runs.append(Run(text=stripped, formatting=fmt_stack[-1]))
    return runs


def _descriptor_dict(d: StyleDescriptor) -> dict:
    return {
        "bold": d.bold, "italic": d.italic, "underline": d.underline,
        "strikethrough": d.strikethrough, "superscript": d.superscript,
        "subscript": d.subscript, "font_family": d.font_family,
        "font_size_pt": d.font_size_pt,
        "foreground_color": d.foreground_color,
        "background_color": d.background_color,
        "link_url": d.link_url, "link_anchor": d.link_anchor,
    }
