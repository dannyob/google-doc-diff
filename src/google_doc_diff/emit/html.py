"""Serialize a Document AST to semantic HTML.

Mirrors emit/markdown.py: same metadata-emission helpers, same stable IDs.
HTML doesn't need CriticMarkup or footnote-marker workarounds because it can
attach arbitrary attributes to <ins>/<del>/<span>/<aside> directly.
"""

from __future__ import annotations

import html as _html
import io
import json

from google_doc_diff.ast.nodes import (
    BookmarkAnchor,
    CodeBlock,
    Comment,
    CommentAnchor,
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
    SuggestionDel,
    SuggestionIns,
    Tab,
    Table,
    TableOfContents,
    Unsupported,
)
from google_doc_diff.styles.classes import synthesize_inline_class
from google_doc_diff.styles.css import build_css


def emit_document_html(doc: Document) -> str:
    out = io.StringIO()
    out.write("<!doctype html>\n<html>\n<head>\n")
    out.write(f"  <title>{_html_escape(doc.title)}</title>\n")
    out.write('  <meta charset="utf-8">\n')
    out.write(f'  <meta name="gd-doc-id" content="{_attr(doc.doc_id)}">\n')
    out.write(f'  <meta name="gd-revision-id" content="{_attr(doc.revision_id)}">\n')
    out.write(f'  <meta name="gd-drive-url" content="{_attr(doc.drive_url)}">\n')
    out.write(f'  <meta name="gd-captured-at" content="{doc.captured_at.isoformat()}">\n')
    out.write(f'  <meta name="gd-schema-version" content="{doc.schema_version}">\n')
    out.write(f'  <meta name="gd-source-mode" content="{doc.source_mode}">\n')
    out.write(
        f'  <meta name="gd-comments-preserved" '
        f'content="{str(doc.comments_preserved).lower()}">\n'
    )
    out.write(
        f'  <meta name="gd-suggestions-preserved" '
        f'content="{str(doc.suggestions_preserved).lower()}">\n'
    )
    if doc.last_modifying_user:
        out.write(
            f'  <meta name="gd-last-modifying-user" '
            f'content="{_attr(doc.last_modifying_user)}">\n'
        )
    css = build_css(doc)
    if css:
        out.write("  <style>\n")
        for line in css.splitlines():
            out.write(f"    {line}\n")
        out.write("  </style>\n")
    out.write("</head>\n<body>\n")

    if _is_single_default_tab(doc):
        body = _emit_blocks(doc.tabs[0].blocks, doc)
        out.write(body)
        out.write(_emit_aside_collections(doc.tabs[0].blocks, doc))
    else:
        for tab in doc.tabs:
            out.write(_emit_tab(tab, doc))

    out.write("</body>\n</html>\n")
    return out.getvalue()


# --- tabs -----------------------------------------------------------------


def _is_single_default_tab(doc: Document) -> bool:
    return (
        len(doc.tabs) == 1
        and not doc.tabs[0].children
        and doc.tabs[0].title in ("", "(default)")
    )


def _emit_tab(tab: Tab, doc: Document) -> str:
    out = (
        f'<section class="gd-tab" data-tab-id="{_attr(tab.tab_id)}" '
        f'data-title="{_attr(tab.title)}" data-level="{tab.level}">\n'
    )
    out += _emit_blocks(tab.blocks, doc)
    out += _emit_aside_collections(tab.blocks, doc)
    for child in tab.children:
        out += _emit_tab(child, doc)
    out += "</section>\n"
    return out


# --- blocks ---------------------------------------------------------------


def _emit_blocks(blocks: list, doc: Document) -> str:
    out: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if isinstance(block, ListItem):
            j = i
            while (
                j < len(blocks)
                and isinstance(blocks[j], ListItem)
                and blocks[j].list_id == block.list_id
            ):
                j += 1
            out.append(_emit_list(blocks[i:j], doc))
            i = j
            continue
        out.append(_emit_block(block, doc))
        i += 1
    return "\n".join(s for s in out if s) + ("\n" if out else "")


def _emit_block(block, doc: Document) -> str:
    if isinstance(block, Heading):
        return _emit_heading(block, doc)
    if isinstance(block, Paragraph):
        return _emit_paragraph(block, doc)
    if isinstance(block, Table):
        return _emit_table(block, doc)
    if isinstance(block, Image):
        return _emit_image(block)
    if isinstance(block, CodeBlock):
        return _emit_code_block(block)
    if isinstance(block, EquationBlock):
        return f'<div class="gd-equation-block">{_html_escape(block.latex)}</div>'
    if isinstance(block, HorizontalRule):
        return "<hr>"
    if isinstance(block, PageBreak):
        return '<div class="gd-pagebreak"></div>'
    if isinstance(block, SectionBreak):
        return '<div class="gd-sectionbreak"></div>'
    if isinstance(block, TableOfContents):
        return '<div class="gd-toc"></div>'
    if isinstance(block, Unsupported):
        return _emit_unsupported(block, inline=False)
    raise TypeError(f"unhandled block kind: {type(block).__name__}")


def _emit_heading(h: Heading, doc: Document) -> str:
    inner = _emit_inline_runs(h.runs, doc)
    attrs = _attr_block(h.anchor_id, h.classes)
    return f"<h{h.level}{attrs}>{inner}</h{h.level}>"


def _emit_paragraph(p: Paragraph, doc: Document) -> str:
    inner = _emit_inline_runs(p.runs, doc)
    if not inner.strip() and not p.classes:
        return ""
    attrs = _attr_block(None, p.classes)
    return f"<p{attrs}>{inner}</p>"


def _emit_image(img: Image) -> str:
    bits = [f'id="i-{_attr(img.image_id)}"', f'src="{_attr(img.src)}"', f'alt="{_attr(img.alt)}"']
    if img.width_px:
        bits.append(f'width="{img.width_px}"')
    if img.height_px:
        bits.append(f'height="{img.height_px}"')
    return "<img " + " ".join(bits) + ">"


def _emit_code_block(cb: CodeBlock) -> str:
    lang_attr = f' class="language-{_attr(cb.language)}"' if cb.language else ""
    return f"<pre><code{lang_attr}>{_html_escape(cb.text.rstrip())}</code></pre>"


def _emit_list(items: list[ListItem], doc: Document) -> str:
    """Render contiguous ListItems as a (possibly nested) ul/ol."""
    if not items:
        return ""
    tag = "ol" if items[0].kind == "ordered" else "ul"
    return _emit_list_recursive(items, doc, tag, 0)


def _emit_list_recursive(items: list[ListItem], doc: Document, tag: str, level: int) -> str:
    """Recursive list renderer that handles arbitrary nesting depth."""
    out = [f"<{tag}>"]
    i = 0
    while i < len(items):
        if items[i].level != level:
            i += 1
            continue
        text = _emit_inline_runs(items[i].runs, doc)
        # Find children of this item (consecutive items with greater level).
        j = i + 1
        children: list[ListItem] = []
        while j < len(items) and items[j].level > level:
            children.append(items[j])
            j += 1
        if children:
            child_tag = "ol" if children[0].kind == "ordered" else "ul"
            child_html = _emit_list_recursive(children, doc, child_tag, level + 1)
            out.append(f"<li>{text}{child_html}</li>")
        else:
            out.append(f"<li>{text}</li>")
        i = j
    out.append(f"</{tag}>")
    return "".join(out)


# --- tables ---------------------------------------------------------------


def _emit_table(t: Table, doc: Document) -> str:
    out = ["<table>"]
    for row in t.rows:
        out.append("  <tr>")
        for cell in row.cells:
            attrs = []
            if cell.colspan != 1:
                attrs.append(f'colspan="{cell.colspan}"')
            if cell.rowspan != 1:
                attrs.append(f'rowspan="{cell.rowspan}"')
            if cell.classes:
                attrs.append(f'class="{" ".join(sorted(cell.classes))}"')
            attr_str = (" " + " ".join(attrs)) if attrs else ""
            cell_html = _emit_blocks(cell.blocks, doc).rstrip()
            out.append(f"    <td{attr_str}>{cell_html}</td>")
        out.append("  </tr>")
    out.append("</table>")
    return "\n".join(out)


# --- inline runs ----------------------------------------------------------


def _emit_inline_runs(runs: list, doc: Document) -> str:
    return "".join(_emit_inline(r, doc) for r in runs)


def _emit_inline(node, doc: Document) -> str:
    if isinstance(node, Run):
        return _emit_run(node)
    if isinstance(node, LineBreak):
        return "<br>"
    if isinstance(node, CommentAnchor):
        inner = _emit_inline_runs(node.runs, doc)
        return (
            f'<span class="gd-cmt-anchor" data-comment-id="{_attr(node.comment_id)}">'
            f'{inner}</span>'
        )
    if isinstance(node, SuggestionIns):
        return _emit_suggestion(node, doc, "ins")
    if isinstance(node, SuggestionDel):
        return _emit_suggestion(node, doc, "del")
    if isinstance(node, FootnoteRef):
        return f'<sup><a href="#{_attr(node.footnote_id)}">{_html_escape(node.footnote_id)}</a></sup>'
    if isinstance(node, BookmarkAnchor):
        return f'<a id="{_attr(node.bookmark_id)}"></a>'
    if isinstance(node, NamedRangeAnchor):
        return f'<a id="{_attr(node.named_range_id)}"></a>'
    if isinstance(node, SmartChip):
        return _emit_smart_chip(node)
    if isinstance(node, InlineEquation):
        return f'<span class="gd-equation">{_html_escape(node.latex)}</span>'
    if isinstance(node, Image):
        return _emit_image(node)
    if isinstance(node, Unsupported):
        return _emit_unsupported(node, inline=True)
    if isinstance(node, HorizontalRule | PageBreak | SectionBreak):
        return "<br>"
    raise TypeError(f"unhandled inline node: {type(node).__name__}")


def _emit_run(r: Run) -> str:
    fmt = r.formatting
    text = _html_escape(r.text)
    if fmt.bold:
        text = f"<strong>{text}</strong>"
    if fmt.italic:
        text = f"<em>{text}</em>"
    if fmt.strikethrough:
        text = f"<s>{text}</s>"
    if fmt.underline:
        text = f"<u>{text}</u>"
    if fmt.superscript:
        text = f"<sup>{text}</sup>"
    if fmt.subscript:
        text = f"<sub>{text}</sub>"
    if fmt.link_url:
        text = f'<a href="{_attr(fmt.link_url)}">{text}</a>'
    cls = synthesize_inline_class(fmt)
    if cls:
        text = f'<span class="{cls}">{text}</span>'
    return text


def _emit_suggestion(node, doc: Document, tag: str) -> str:
    sug = doc.suggestions.get(node.suggestion_id)
    inner = "".join(_emit_run(r) if isinstance(r, Run) else _emit_inline(r, doc)
                    for r in node.runs)
    attrs = [f'data-suggestion-id="s-{_attr(node.suggestion_id)}"']
    if sug:
        attrs.append(f'data-author="{_attr(sug.author)}"')
        attrs.append(f'data-created="{sug.created_time.isoformat()}"')
        attrs.append(f'data-kind="{sug.kind}"')
    return f"<{tag} {' '.join(attrs)}>{inner}</{tag}>"


def _emit_smart_chip(c: SmartChip) -> str:
    visible = c.display_text or _smart_chip_default_text(c)
    attrs = [f'class="gd-chip gd-chip-{c.kind}"']
    for k in sorted(c.data):
        attrs.append(f'data-{k}="{_attr(str(c.data[k]))}"')
    return f"<span {' '.join(attrs)}>{_html_escape(visible)}</span>"


def _smart_chip_default_text(c: SmartChip) -> str:
    if c.kind == "person":
        return f"@{c.data.get('email', '?')}"
    return c.kind


def _emit_unsupported(u: Unsupported, *, inline: bool) -> str:
    raw_attr = _attr(json.dumps(u.raw, sort_keys=True))
    if inline:
        return f'<span class="gd-unsupported" data-kind="{_attr(u.kind)}" data-raw="{raw_attr}"></span>'
    return f'<div class="gd-unsupported" data-kind="{_attr(u.kind)}" data-raw="{raw_attr}"></div>'


# --- aside collections (comments + footnotes at end of scope) ------------


def _emit_aside_collections(blocks: list, doc: Document) -> str:
    """Emit <aside class="gd-comment"> + <aside class="gd-footnote"> for every
    referenced ID in this scope. Walk blocks for anchors, gather ids."""
    cmt_ids = _collect_ids(blocks, CommentAnchor, "comment_id")
    fn_ids = _collect_ids(blocks, FootnoteRef, "footnote_id")
    out = ""
    for cid in sorted(cmt_ids):
        cmt = doc.comments.get(cid)
        if cmt:
            out += _format_comment_aside(cmt) + "\n"
    for fid in sorted(fn_ids):
        fn = doc.footnotes.get(fid)
        if fn:
            out += _format_footnote_aside(fn, doc) + "\n"
    return out


def _collect_ids(blocks: list, kind: type, attr: str) -> set[str]:
    found: set[str] = set()

    def walk(node):
        if isinstance(node, kind):
            found.add(getattr(node, attr))
        for child_attr in ("runs", "blocks", "rows", "cells"):
            children = getattr(node, child_attr, None)
            if children:
                for c in children:
                    walk(c)

    for b in blocks:
        walk(b)
    return found


def _format_comment_aside(cmt: Comment) -> str:
    head = (
        f'<aside class="gd-comment" id="{_attr(cmt.comment_id)}" '
        f'data-author="{_attr(cmt.author)}" '
        f'data-created="{cmt.created_time.isoformat()}" '
        f'data-resolved="{str(cmt.resolved).lower()}">\n'
    )
    body = f"  <p><strong>{_html_escape(cmt.author)}</strong> {cmt.created_time.date().isoformat()}: {_html_escape(cmt.content)}</p>\n"
    for reply in cmt.replies:
        marker = ""
        if reply.action == "resolve":
            marker = " (resolved)"
        elif reply.action == "reopen":
            marker = " (reopened)"
        body += (
            f"  <blockquote><strong>{_html_escape(reply.author)}</strong> "
            f"{reply.created_time.date().isoformat()}{marker}: "
            f"{_html_escape(reply.content)}</blockquote>\n"
        )
    if cmt.orphaned:
        body += f"  <p><em>orphaned: original anchor &quot;{_html_escape(cmt.quoted_text)}&quot;</em></p>\n"
    return head + body + "</aside>"


def _format_footnote_aside(fn: Footnote, doc: Document) -> str:
    body = _emit_blocks(fn.blocks, doc)
    return f'<aside class="gd-footnote" id="{_attr(fn.footnote_id)}">\n{body}</aside>'


# --- helpers --------------------------------------------------------------


def _attr_block(anchor_id: str | None, classes: list[str]) -> str:
    parts = []
    if anchor_id:
        parts.append(f'id="{_attr(anchor_id)}"')
    if classes:
        parts.append(f'class="{" ".join(sorted(classes))}"')
    return (" " + " ".join(parts)) if parts else ""


def _html_escape(s: str) -> str:
    return _html.escape(s, quote=False)


def _attr(s: str) -> str:
    return _html.escape(s, quote=True)
