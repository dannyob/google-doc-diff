"""Serialize a Document AST to Pandoc-flavor Markdown.

The output carries enough metadata (stable IDs, classes, frontmatter) to
support a future round-trip parser. Comments render as Pandoc footnotes
(short single-graf ones inline via `^[...]`; longer ones as reference-style
`[^c-...]` definitions placed at the end of the containing tab). Suggestions
render as CriticMarkup `{++...++}` / `{--...--}` / `{~~old~>new~~}` with a
footnote-style metadata sidecar carrying author/timestamp.

Determinism: identical AST in -> identical text out, byte for byte.
"""

from __future__ import annotations

import io

import yaml

from google_doc_diff.ast.nodes import (
    BookmarkAnchor,
    CodeBlock,
    Comment,
    CommentAnchor,
    Conflict,
    Document,
    EquationBlock,
    Footnote,
    FootnoteRef,
    Heading,
    HorizontalRule,
    Image,
    InlineEquation,
    LineBreak,
    ListItem,
    NamedRangeAnchor,
    PageBreak,
    Paragraph,
    Run,
    SectionBreak,
    SmartChip,
    Suggestion,
    SuggestionDel,
    SuggestionIns,
    Tab,
    Table,
    TableOfContents,
    Unsupported,
)
from google_doc_diff.styles.classes import synthesize_inline_class
from google_doc_diff.styles.css import build_css


def emit_document_md(doc: Document) -> str:
    """Render a Document to a single Markdown string."""
    out = io.StringIO()
    out.write(_emit_frontmatter(doc))
    out.write("\n")
    css = build_css(doc)
    if css:
        out.write("<style>\n")
        out.write(css)
        out.write("\n</style>\n\n")

    # Collect footnote definitions per scope. A scope is a tab; if the doc has
    # no real tabs (single default), the scope is the whole doc body.
    if _is_single_default_tab(doc):
        body, fn_defs = _emit_blocks_collecting_footnotes(doc.tabs[0].blocks, doc)
        out.write(body)
        if fn_defs:
            out.write("\n")
            out.write(fn_defs)
    else:
        for tab in doc.tabs:
            out.write(_emit_tab(tab, doc, depth=0))
            out.write("\n")
    return _normalize(out.getvalue())


# --- frontmatter ----------------------------------------------------------


def _emit_frontmatter(doc: Document) -> str:
    fm = {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "revision_id": doc.revision_id,
        "drive_url": doc.drive_url,
        "captured_at": doc.captured_at.isoformat(),
        "schema_version": doc.schema_version,
        "last_modifying_user": doc.last_modifying_user,
        "source_mode": doc.source_mode,
        "comments_preserved": doc.comments_preserved,
        "suggestions_preserved": doc.suggestions_preserved,
    }
    if doc.gdoc_state:
        fm["gdoc"] = doc.gdoc_state
    body = yaml.safe_dump(fm, sort_keys=True, default_flow_style=False, allow_unicode=True)
    return f"---\n{body}---\n"


# --- tabs -----------------------------------------------------------------


def _is_single_default_tab(doc: Document) -> bool:
    return (
        len(doc.tabs) == 1
        and not doc.tabs[0].children
        and doc.tabs[0].title in ("", "(default)")
    )


def _emit_tab(tab: Tab, doc: Document, depth: int) -> str:
    fence = ":" * (3 + depth)
    head = (
        f'{fence} {{.gd-tab data-tab-id="{tab.tab_id}" '
        f'data-title="{_attr_escape(tab.title)}" data-level="{tab.level}"}}\n'
    )
    body, fn_defs = _emit_blocks_collecting_footnotes(tab.blocks, doc)
    out = head + body
    if fn_defs:
        out += "\n" + fn_defs
    for child in tab.children:
        out += "\n" + _emit_tab(child, doc, depth + 1)
    out += f"\n{fence}\n"
    return out


# --- blocks + footnote collection ----------------------------------------


def _emit_blocks_collecting_footnotes(blocks: list, doc: Document) -> tuple[str, str]:
    """Return (block-content, footnote-definitions) for a scope.

    Footnote definitions cover comments (when not rendered as inline ^[...]
    notes), suggestions, and real footnotes referenced anywhere in `blocks`.
    """
    out: list[str] = []
    fn_ids: set[str] = set()

    i = 0
    while i < len(blocks):
        block = blocks[i]
        if isinstance(block, ListItem):
            # Coalesce contiguous ListItems sharing the same list_id.
            j = i
            while (
                j < len(blocks)
                and isinstance(blocks[j], ListItem)
                and blocks[j].list_id == block.list_id
            ):
                j += 1
            out.append(_emit_list(blocks[i:j], doc, fn_ids))
            i = j
            continue
        out.append(_emit_block(block, doc, fn_ids))
        i += 1

    body = "\n\n".join(s for s in out if s) + "\n"
    fn_defs = _emit_footnote_definitions(fn_ids, doc)
    return body, fn_defs


def _emit_block(block, doc: Document, fn_ids: set) -> str:
    if isinstance(block, Heading):
        return _emit_heading(block, doc, fn_ids)
    if isinstance(block, Paragraph):
        return _emit_paragraph(block, doc, fn_ids)
    if isinstance(block, Table):
        return _emit_table(block, doc, fn_ids)
    if isinstance(block, Image):
        return _emit_image_block(block)
    if isinstance(block, CodeBlock):
        return _emit_code_block(block)
    if isinstance(block, EquationBlock):
        return f"$$\n{block.latex}\n$$"
    if isinstance(block, HorizontalRule):
        return "---"
    if isinstance(block, PageBreak):
        return '<div class="gd-pagebreak"></div>'
    if isinstance(block, SectionBreak):
        return '<div class="gd-sectionbreak"></div>'
    if isinstance(block, TableOfContents):
        return '<div class="gd-toc"></div>'
    if isinstance(block, Unsupported):
        return _emit_unsupported(block, inline=False)
    if isinstance(block, Conflict):
        return _emit_conflict(block, doc, fn_ids)
    raise TypeError(f"unhandled block kind: {type(block).__name__}")


CONFLICT_LOCAL_MARKER = "<<<<<<< LOCAL"
CONFLICT_SEPARATOR = "======="
CONFLICT_REMOTE_MARKER = ">>>>>>> REMOTE"


def _emit_conflict(c: Conflict, doc: Document, fn_ids: set) -> str:
    """Render a 3-way-merge conflict as a `.gd-conflict` div with git-style markers.

    Shape:

        ::: {.gd-conflict #c-1}
        <<<<<<< LOCAL
        … local blocks …
        =======
        … remote blocks …
        >>>>>>> REMOTE
        :::

    Git-style markers are familiar to anyone who's resolved a merge
    conflict, and they don't nest pandoc fences inside pandoc fences —
    keeping the parser straightforward.
    """
    local_md = "\n\n".join(_emit_block(b, doc, fn_ids) for b in c.local_blocks)
    remote_md = "\n\n".join(_emit_block(b, doc, fn_ids) for b in c.remote_blocks)
    return "\n".join([
        f"::: {{.gd-conflict #{c.conflict_id}}}",
        CONFLICT_LOCAL_MARKER,
        local_md,
        CONFLICT_SEPARATOR,
        remote_md,
        CONFLICT_REMOTE_MARKER,
        ":::",
    ])


def _emit_heading(h: Heading, doc: Document, fn_ids: set) -> str:
    text = _emit_inline_runs(h.runs, doc, fn_ids)
    hashes = "#" * h.level
    classes = list(h.classes)
    # v2: paragraph_id rides alongside anchor_id. anchor_id wins on the
    # heading-anchor channel; paragraph_id is added as a second `#…` token.
    extra_ids = [h.paragraph_id] if h.paragraph_id else []
    attr_str = _format_attr_block(h.anchor_id, classes, extra_ids=extra_ids)
    if attr_str:
        return f"{hashes} {text} {attr_str}"
    return f"{hashes} {text}"


def _emit_paragraph(p: Paragraph, doc: Document, fn_ids: set) -> str:
    text = _emit_inline_runs(p.runs, doc, fn_ids)
    if not text.strip() and not p.classes and not p.paragraph_id:
        return ""
    if "gd-subtitle" in p.classes and not p.paragraph_id:
        return f"::: gd-subtitle\n{text}\n:::"
    if not p.classes and not p.paragraph_id:
        return text
    extra_ids = [p.paragraph_id] if p.paragraph_id else []
    attr_str = _format_attr_block(None, list(p.classes), extra_ids=extra_ids)
    return f"::: {{{attr_str.strip('{}')}}}\n{text}\n:::" if attr_str else text


def _emit_image_block(img: Image) -> str:
    bits = [f'#i-{img.image_id}'] if img.image_id else []
    if img.width_px:
        bits.append(f'width={img.width_px}')
    if img.height_px:
        bits.append(f'height={img.height_px}')
    attr = " ".join(bits)
    suffix = f"{{{attr}}}" if attr else ""
    alt = img.alt or ""
    return f"![{alt}]({img.src}){suffix}"


def _emit_code_block(cb: CodeBlock) -> str:
    lang = cb.language or ""
    return f"```{lang}\n{cb.text.rstrip()}\n```"


# --- lists ----------------------------------------------------------------


def _emit_list(items: list[ListItem], doc: Document, fn_ids: set) -> str:
    """Render a contiguous run of items as a Pandoc list."""
    out_lines: list[str] = []
    kind = items[0].kind
    for item in items:
        marker = "1." if item.kind == "ordered" else "-"
        indent = "    " * item.level if kind == "ordered" else "  " * item.level
        id_prefix = f"[]{{#{item.paragraph_id}}}" if item.paragraph_id else ""
        text = _emit_inline_runs(item.runs, doc, fn_ids)
        out_lines.append(f"{indent}{marker} {id_prefix}{text}")
    return "\n".join(out_lines)


# --- tables --------------------------------------------------------------


def _emit_table(t: Table, doc: Document, fn_ids: set) -> str:
    """Pipe table when simple; raw HTML when any cell spans."""
    needs_html = any(
        cell.colspan != 1 or cell.rowspan != 1
        for row in t.rows
        for cell in row.cells
    )
    if needs_html:
        return _emit_table_html(t, doc, fn_ids)
    return _emit_table_pipe(t, doc, fn_ids)


def _emit_table_pipe(t: Table, doc: Document, fn_ids: set) -> str:
    if not t.rows:
        return ""
    header = t.rows[0]
    header_cells = [_cell_to_inline(c, doc, fn_ids) for c in header.cells]
    head = "| " + " | ".join(header_cells) + " |"
    sep = "| " + " | ".join("---" for _ in header_cells) + " |"
    body_lines = []
    for row in t.rows[1:]:
        body_lines.append("| " + " | ".join(_cell_to_inline(c, doc, fn_ids) for c in row.cells) + " |")
    return "\n".join([head, sep, *body_lines])


def _cell_to_inline(cell, doc: Document, fn_ids: set) -> str:
    """Flatten a cell's blocks into a one-line cell representation.

    Cells in pipe tables can't carry block structure; we join paragraph
    contents with `<br>` for soft breaks across paragraphs and prefix list
    items with their marker.
    """
    parts: list[str] = []
    for blk in cell.blocks:
        if isinstance(blk, Paragraph | Heading):
            text = _emit_inline_runs(blk.runs, doc, fn_ids)
            if text.strip():
                parts.append(text)
        elif isinstance(blk, ListItem):
            marker = "1. " if blk.kind == "ordered" else "• "
            indent = "&nbsp;&nbsp;" * blk.level
            text = _emit_inline_runs(blk.runs, doc, fn_ids)
            if text.strip():
                parts.append(f"{indent}{marker}{text}")
        else:
            text = _emit_block(blk, doc, fn_ids).replace("\n", " ").strip()
            if text:
                parts.append(text)
    return "<br>".join(parts) or "&nbsp;"


def _emit_table_html(t: Table, doc: Document, fn_ids: set) -> str:
    out = ["<table>"]
    for row in t.rows:
        out.append("  <tr>")
        for cell in row.cells:
            attrs = []
            if cell.colspan != 1:
                attrs.append(f'colspan="{cell.colspan}"')
            if cell.rowspan != 1:
                attrs.append(f'rowspan="{cell.rowspan}"')
            attr_str = (" " + " ".join(attrs)) if attrs else ""
            cell_md = _cell_to_inline(cell, doc, fn_ids)
            out.append(f"    <td{attr_str}>{cell_md}</td>")
        out.append("  </tr>")
    out.append("</table>")
    return "\n".join(out)


# --- inline runs -----------------------------------------------------------


def _emit_inline_runs(runs: list, doc: Document, fn_ids: set) -> str:
    """Emit a list of inline nodes, threading footnote-id collection."""
    out: list[str] = []
    # First pass: detect replacement-suggestion pairs (ins+del with same id, adjacent).
    runs = _coalesce_replacement_suggestions(runs)
    for r in runs:
        out.append(_emit_inline(r, doc, fn_ids))
    return "".join(out)


def _coalesce_replacement_suggestions(runs: list) -> list:
    """Pair a SuggestionDel immediately followed by a SuggestionIns (or vice
    versa) with the same suggestion_id into a synthetic 3-tuple representing
    a CriticMarkup substitution."""
    out: list = []
    i = 0
    while i < len(runs):
        cur = runs[i]
        nxt = runs[i + 1] if i + 1 < len(runs) else None
        if (
            isinstance(cur, SuggestionDel)
            and isinstance(nxt, SuggestionIns)
            and cur.suggestion_id == nxt.suggestion_id
        ):
            out.append(("REPLACEMENT", cur.suggestion_id, cur.runs, nxt.runs))
            i += 2
            continue
        if (
            isinstance(cur, SuggestionIns)
            and isinstance(nxt, SuggestionDel)
            and cur.suggestion_id == nxt.suggestion_id
        ):
            out.append(("REPLACEMENT", cur.suggestion_id, nxt.runs, cur.runs))
            i += 2
            continue
        out.append(cur)
        i += 1
    return out


def _emit_inline(node, doc: Document, fn_ids: set) -> str:
    if isinstance(node, Run):
        return _emit_run(node)
    if isinstance(node, LineBreak):
        return "  \n"
    if isinstance(node, CommentAnchor):
        return _emit_comment_anchor(node, doc, fn_ids)
    if isinstance(node, SuggestionIns):
        return _emit_suggestion_ins(node, fn_ids)
    if isinstance(node, SuggestionDel):
        return _emit_suggestion_del(node, fn_ids)
    if isinstance(node, FootnoteRef):
        fn_ids.add(node.footnote_id)
        return f"[^{node.footnote_id}]"
    if isinstance(node, BookmarkAnchor):
        return f"[]{{#{node.bookmark_id}}}"
    if isinstance(node, NamedRangeAnchor):
        return f"[]{{#{node.named_range_id}}}"
    if isinstance(node, SmartChip):
        return _emit_smart_chip(node)
    if isinstance(node, InlineEquation):
        return f"${node.latex}$"
    if isinstance(node, Image):
        return _emit_image_block(node)
    if isinstance(node, Unsupported):
        return _emit_unsupported(node, inline=True)
    if isinstance(node, HorizontalRule | PageBreak | SectionBreak):
        # Block-level breaks that the Docs API surfaces as inline elements
        # inside paragraphs. Render as a soft break.
        return "  \n"
    if isinstance(node, tuple) and node[0] == "REPLACEMENT":
        # ('REPLACEMENT', suggestion_id, old_runs, new_runs)
        _, sid, old, new = node
        old_text = "".join(_emit_run(r) if isinstance(r, Run) else _emit_inline(r, doc, fn_ids)
                           for r in old)
        new_text = "".join(_emit_run(r) if isinstance(r, Run) else _emit_inline(r, doc, fn_ids)
                           for r in new)
        fn_ids.add(f"s-{sid}")
        return f"{{~~{old_text}~>{new_text}~~}}[^s-{sid}]"
    raise TypeError(f"unhandled inline node: {type(node).__name__}")


def _emit_run(r: Run) -> str:
    text = r.text
    fmt = r.formatting
    # Order matters: inline-override class wraps everything else.
    if fmt.link_url:
        text = f"[{_md_escape_text(text)}]({fmt.link_url})"
    else:
        text = _md_escape_text(text)
        if fmt.bold and fmt.italic:
            text = f"***{text}***"
        elif fmt.bold:
            text = f"**{text}**"
        elif fmt.italic:
            text = f"*{text}*"
        if fmt.strikethrough:
            text = f"~~{text}~~"
    # Don't wrap links in a class span — the [text](url) syntax can't nest
    # inside [...]{.class} without breaking markdown parsers. Link-default
    # styling (blue + underline) is inherent to the link and doesn't need a
    # separate class.
    if not fmt.link_url:
        cls = synthesize_inline_class(fmt)
        if cls:
            return f"[{text}]{{.{cls}}}"
    return text


def _md_escape_text(s: str) -> str:
    """Escape a minimal set of Markdown metacharacters at run-emission time.

    Aggressive escaping would garble Pandoc; we only escape the characters
    that begin Markdown structures inside prose: backslash, brackets,
    backticks, asterisks, underscores at word boundaries, hash at line
    start. For now, escape the most-common-confusable chars.
    """
    return (
        s.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("`", "\\`")
    )


def _emit_comment_anchor(c: CommentAnchor, doc: Document, fn_ids: set) -> str:
    inner = "".join(_emit_inline(r, doc, fn_ids) for r in c.runs)
    cmt = doc.comments.get(c.comment_id)
    if cmt is None:
        return inner
    is_short = (
        not cmt.replies
        and "\n" not in cmt.content
        and not cmt.resolved
        and not cmt.deleted
    )
    if is_short:
        note_body = f"**{cmt.author}** {cmt.created_time.date().isoformat()}: {cmt.content}"
        return (
            f"[{inner}]{{.gd-cmt-anchor #{c.comment_id}}}^[{note_body}]"
        )
    fn_ids.add(c.comment_id)
    return f"[{inner}]{{.gd-cmt-anchor #{c.comment_id}}}[^{c.comment_id}]"


def _emit_suggestion_ins(s: SuggestionIns, fn_ids: set) -> str:
    text = "".join(_emit_run(r) if isinstance(r, Run) else "" for r in s.runs)
    fn_ids.add(f"s-{s.suggestion_id}")
    return f"{{++{text}++}}[^s-{s.suggestion_id}]"


def _emit_suggestion_del(s: SuggestionDel, fn_ids: set) -> str:
    text = "".join(_emit_run(r) if isinstance(r, Run) else "" for r in s.runs)
    fn_ids.add(f"s-{s.suggestion_id}")
    return f"{{--{text}--}}[^s-{s.suggestion_id}]"


def _emit_smart_chip(c: SmartChip) -> str:
    visible = c.display_text or _smart_chip_default_text(c)
    attrs = [f'data-kind="{c.kind}"']
    for k in sorted(c.data):
        attrs.append(f'data-{k}="{_attr_escape(str(c.data[k]))}"')
    return f"[{visible}]{{.gd-chip {' '.join(attrs)}}}"


def _smart_chip_default_text(c: SmartChip) -> str:
    if c.kind == "person":
        return f"@{c.data.get('email', '?')}"
    return c.kind


def _emit_unsupported(u: Unsupported, *, inline: bool) -> str:
    import json
    raw_attr = _attr_escape(json.dumps(u.raw, sort_keys=True))
    attrs = f'.gd-unsupported data-kind="{u.kind}" data-raw="{raw_attr}"'
    if inline:
        return f"[]{{{attrs}}}"
    return f"::: {{{attrs}}}\n:::"


# --- footnote definitions (comments + suggestions + real footnotes) -------


def _emit_footnote_definitions(fn_ids: set, doc: Document) -> str:
    if not fn_ids:
        return ""
    out: list[str] = []
    for fid in sorted(fn_ids):
        if fid.startswith("c-"):
            cmt = doc.comments.get(fid)
            if cmt:
                out.append(_format_comment_footnote(cmt))
        elif fid.startswith("s-"):
            sid = fid[2:]
            sug = doc.suggestions.get(sid)
            if sug:
                out.append(_format_suggestion_footnote(sug))
        elif fid.startswith("fn-"):
            fn = doc.footnotes.get(fid)
            if fn:
                out.append(_format_footnote_definition(fn, doc))
    return "\n\n".join(out) + ("\n" if out else "")


def _format_comment_footnote(cmt: Comment) -> str:
    lines = [
        f"[^{cmt.comment_id}]: ::: {{.gd-comment "
        f'data-author="{_attr_escape(cmt.author)}" '
        f'data-created="{cmt.created_time.isoformat()}" '
        f'data-resolved="{str(cmt.resolved).lower()}"}}'
    ]
    lines.append(f"    **{cmt.author}** {cmt.created_time.date().isoformat()}: {cmt.content}")
    for reply in cmt.replies:
        marker = ""
        if reply.action == "resolve":
            marker = " (resolved)"
        elif reply.action == "reopen":
            marker = " (reopened)"
        lines.append("")
        lines.append(
            f"    > **{reply.author}** {reply.created_time.date().isoformat()}{marker}: {reply.content}"
        )
    if cmt.orphaned:
        lines.append("")
        lines.append(f'    > (orphaned: original anchor "{_attr_escape(cmt.quoted_text)}")')
    lines.append("    :::")
    return "\n".join(lines)


def _format_suggestion_footnote(s: Suggestion) -> str:
    return (
        f"[^s-{s.suggestion_id}]: ::: {{.gd-suggestion "
        f'data-kind="{s.kind}" '
        f'data-author="{_attr_escape(s.author)}" '
        f'data-created="{s.created_time.isoformat()}"}} :::'
    )


def _format_footnote_definition(fn: Footnote, doc: Document) -> str:
    body, _ = _emit_blocks_collecting_footnotes(fn.blocks, doc)
    indented = "\n".join("    " + line if line else "" for line in body.rstrip("\n").split("\n"))
    return f"[^{fn.footnote_id}]: ::: .gd-footnote\n{indented}\n    :::"


# --- attribute / text helpers ---------------------------------------------


def _format_attr_block(
    anchor_id: str | None,
    classes: list[str],
    extra_ids: list[str] | None = None,
) -> str:
    """Render `{#id1 .class1 .class2 #id2}` style pandoc attribute block.

    `anchor_id` is the *primary* identifier (heading anchor or block anchor —
    becomes the first `#…` token, which pandoc parses as the canonical id).
    `extra_ids` (e.g. v2's `paragraph_id`) appears after the classes so the
    primary id keeps its position-of-honor.
    """
    parts: list[str] = []
    if anchor_id:
        parts.append(f"#{anchor_id}")
    for cls in sorted(classes):
        parts.append(f".{cls}")
    for extra in extra_ids or ():
        if extra:
            parts.append(f"#{extra}")
    if not parts:
        return ""
    return "{" + " ".join(parts) + "}"


def _attr_escape(s: str) -> str:
    return s.replace('"', '\\"')


def _normalize(s: str) -> str:
    """Squeeze runs of >2 blank lines, ensure exactly one trailing newline."""
    while "\n\n\n\n" in s:
        s = s.replace("\n\n\n\n", "\n\n\n")
    return s.rstrip("\n") + "\n"
