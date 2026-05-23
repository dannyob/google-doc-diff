"""Build a Document AST from Google Docs API JSON + Drive Comments JSON.

This is the high-fidelity path used by `gdoc pull` for the current revision.
For historical revisions, see `from_google_md` (lossy; comments+suggestions
not preserved by Drive's exportLinks).

Docs API reference:
    https://developers.google.com/workspace/docs/api/reference/rest/v1/documents

The walker is defensive: any element it doesn't recognize is wrapped in an
Unsupported AST node so output never crashes on novel API features.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from google_doc_diff.ast.anchor_comments import anchor_comments
from google_doc_diff.ast.nodes import (
    Cell,
    Comment,
    CommentReply,
    Document,
    Footnote,
    FootnoteRef,
    Heading,
    HorizontalRule,
    Image,
    InlineEquation,
    LineBreak,
    ListItem,
    PageBreak,
    Paragraph,
    Row,
    Run,
    SectionBreak,
    SmartChip,
    StyleDescriptor,
    Suggestion,
    SuggestionDel,
    SuggestionIns,
    Tab,
    Table,
    TableOfContents,
    Unsupported,
)
from google_doc_diff.styles.classes import (
    NAMED_STYLE_CLASSES,
    is_default_for_named_style,
    synthesize_inline_class,
)


def build_document(
    docs_json: dict,
    comments_json: list[dict] | None = None,
    *,
    drive_url: str | None = None,
    captured_at: datetime | None = None,
    last_modifying_user: str | None = None,
) -> Document:
    """Build a Document AST from a Docs API response and optional Comments list."""
    doc_id = docs_json.get("documentId", "")
    title = docs_json.get("title", "")
    revision_id = docs_json.get("revisionId", "")
    if not drive_url:
        from google_doc_diff.api import drive_url_for
        drive_url = drive_url_for(doc_id)
    if not captured_at:
        captured_at = datetime.now(UTC)

    builder = _DocBuilder(docs_json)
    tabs = builder.build_tabs()
    _stamp_paragraph_ids(tabs)

    document = Document(
        doc_id=doc_id,
        title=title,
        revision_id=revision_id,
        drive_url=drive_url,
        captured_at=captured_at,
        schema_version=1,
        last_modifying_user=last_modifying_user,
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=True,
        tabs=tabs,
        comments=_build_comments(comments_json or []),
        suggestions=builder.suggestions,
        footnotes=builder.footnotes,
        named_styles=_build_named_styles(docs_json),
        list_definitions=_build_list_definitions(docs_json),
        inline_objects=builder.inline_objects,
        css_classes=builder.css_classes,
    )
    return anchor_comments(document)


# --- Comments (Drive Comments API) ---------------------------------------


def _stamp_paragraph_ids(tabs: list) -> None:
    """Assign a deterministic paragraph_id to every Paragraph/Heading.

    Format: ``p-{tab_idx}-{block_idx}`` — stable across re-pulls of the
    same docs_json so a fresh pull and a previously-pulled-and-edited
    local markdown share the same ids for unchanged paragraphs. This is
    the join key the diff layer uses to recognise "same block, different
    content" vs "deleted + reinserted".

    Headings already carry an `anchor_id` from Google's `headingId`; the
    paragraph_id is a separate diff key (the diff layer reads
    paragraph_id first, then falls back to anchor_id for headings).
    """
    def _walk(tab_list: list, prefix: str) -> None:
        for ti, tab in enumerate(tab_list):
            base = f"{prefix}{ti}" if prefix else str(ti)
            for bi, block in enumerate(tab.blocks):
                if not isinstance(block, (Paragraph, Heading, ListItem)):
                    continue
                if block.paragraph_id is not None:
                    continue
                # Skip visually-empty blocks. Docs always ends the body with
                # a trailing empty paragraph; stamping it would round-trip
                # back as an empty `::: {#p-…}` fence, then later diff as
                # a phantom DeleteBlock when the local md drops it.
                if _block_is_empty(block.runs):
                    continue
                block.paragraph_id = f"p-{base}-{bi}"
            if tab.children:
                _walk(tab.children, f"{base}.")
    _walk(tabs, "")


def _block_is_empty(runs) -> bool:
    """True iff a block carries no visible text content.

    Runs can be Run / SmartChip / Suggestion* / etc. A SmartChip, image,
    or suggestion is "visible" regardless of any `.text` attribute, so we
    only treat a block as empty when EVERY run is a Run with whitespace
    text. (Anything non-Run counts as content.)
    """
    if not runs:
        return True
    for r in runs:
        if not isinstance(r, Run):
            return False
        if r.text and r.text.strip():
            return False
    return True


def _build_comments(comments: list[dict]) -> dict[str, Comment]:
    out: dict[str, Comment] = {}
    for c in comments:
        cid = "c-" + c["id"]
        replies = []
        for r in c.get("replies", []):
            replies.append(CommentReply(
                reply_id="r-" + r["id"],
                author=_author_email(r.get("author")),
                created_time=_parse_iso(r.get("createdTime")),
                modified_time=_parse_iso(r.get("modifiedTime") or r.get("createdTime")),
                content=r.get("content", ""),
                action=r.get("action") or None,
                deleted=bool(r.get("deleted")),
            ))
        out[cid] = Comment(
            comment_id=cid,
            author=_author_email(c.get("author")),
            created_time=_parse_iso(c.get("createdTime")),
            modified_time=_parse_iso(c.get("modifiedTime") or c.get("createdTime")),
            content=c.get("content", ""),
            quoted_text=(c.get("quotedFileContent") or {}).get("value", ""),
            resolved=bool(c.get("resolved")),
            deleted=bool(c.get("deleted")),
            replies=replies,
        )
    return out


def _author_email(author: dict | None) -> str:
    if not author:
        return "unknown@gdoc-diff"
    return author.get("emailAddress") or author.get("displayName") or "unknown@gdoc-diff"


def _parse_iso(s: str | None) -> datetime:
    if not s:
        return datetime.fromtimestamp(0, tz=UTC)
    # Drive returns "2026-05-09T22:05:44.239Z"; Python's fromisoformat handles
    # "Z" as of 3.11.
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


# --- Named styles --------------------------------------------------------


def _build_named_styles(docs_json: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    named = docs_json.get("namedStyles", {}).get("styles", [])
    for entry in named:
        nst = entry.get("namedStyleType")
        if not nst:
            continue
        descriptor = _text_style_to_descriptor_dict(entry.get("textStyle", {}))
        if descriptor:
            out[nst] = descriptor
    return out


def _text_style_to_descriptor_dict(ts: dict) -> dict:
    """Project the Docs textStyle dict onto our StyleDescriptor field names."""
    out: dict = {}
    if "bold" in ts:
        out["bold"] = ts["bold"]
    if "italic" in ts:
        out["italic"] = ts["italic"]
    if "underline" in ts:
        out["underline"] = ts["underline"]
    if "strikethrough" in ts:
        out["strikethrough"] = ts["strikethrough"]
    if ts.get("baselineOffset") == "SUPERSCRIPT":
        out["superscript"] = True
    if ts.get("baselineOffset") == "SUBSCRIPT":
        out["subscript"] = True
    wff = ts.get("weightedFontFamily") or {}
    if wff.get("fontFamily"):
        out["font_family"] = wff["fontFamily"]
    fs = ts.get("fontSize") or {}
    if fs.get("magnitude") is not None:
        out["font_size_pt"] = float(fs["magnitude"])
    fg = _color_to_hex(ts.get("foregroundColor"))
    if fg:
        out["foreground_color"] = fg
    bg = _color_to_hex(ts.get("backgroundColor"))
    if bg:
        out["background_color"] = bg
    link = ts.get("link") or {}
    if "url" in link:
        out["link_url"] = link["url"]
    if "bookmarkId" in link:
        out["link_anchor"] = "bm-" + link["bookmarkId"]
    if "headingId" in link:
        out["link_anchor"] = "h-" + link["headingId"]
    return out


def _color_to_hex(color: dict | None) -> str | None:
    if not color:
        return None
    rgb = ((color.get("color") or {}).get("rgbColor")) or {}
    if not rgb:
        return None
    r = int(round((rgb.get("red") or 0) * 255))
    g = int(round((rgb.get("green") or 0) * 255))
    b = int(round((rgb.get("blue") or 0) * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


def _descriptor_from_dict(d: dict) -> StyleDescriptor:
    return StyleDescriptor(
        bold=d.get("bold"),
        italic=d.get("italic"),
        underline=d.get("underline"),
        strikethrough=d.get("strikethrough"),
        superscript=d.get("superscript"),
        subscript=d.get("subscript"),
        font_family=d.get("font_family"),
        font_size_pt=d.get("font_size_pt"),
        foreground_color=d.get("foreground_color"),
        background_color=d.get("background_color"),
        link_url=d.get("link_url"),
        link_anchor=d.get("link_anchor"),
    )


# --- List definitions ----------------------------------------------------


def _build_list_definitions(docs_json: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for lid, lst in (docs_json.get("lists") or {}).items():
        out[lid] = lst.get("listProperties", {})
    return out


# --- Document body walker -------------------------------------------------


class _DocBuilder:
    def __init__(self, docs_json: dict):
        self.docs_json = docs_json
        self.suggestions: dict[str, Suggestion] = {}
        self.footnotes: dict[str, Footnote] = {}
        self.css_classes: dict[str, str] = {}
        self._named_style_dicts = _build_named_styles(docs_json)
        # Multi-tab docs nest inlineObjects (and lists) under each
        # documentTab rather than at the top level. Merge everything into a
        # single lookup so the inline-element walker always finds them.
        self.inline_objects: dict = dict(docs_json.get("inlineObjects") or {})
        self.lists: dict = dict(docs_json.get("lists") or {})
        for tab in _iter_doc_tabs(docs_json.get("tabs") or []):
            dt = tab.get("documentTab", {})
            self.inline_objects.update(dt.get("inlineObjects") or {})
            self.lists.update(dt.get("lists") or {})

    def build_tabs(self) -> list[Tab]:
        tabs_data = self.docs_json.get("tabs")
        if tabs_data:
            return [self._build_tab(t, level=0, parent_id=None) for t in tabs_data]
        body = self.docs_json.get("body", {})
        blocks = self._walk_structural_elements(body.get("content", []))
        return [Tab(tab_id="t-default", title="(default)", level=0,
                    blocks=_strip_leading_section_break(blocks))]

    def _build_tab(self, tab_data: dict, *, level: int, parent_id: str | None) -> Tab:
        props = tab_data.get("tabProperties", {})
        tab_id = "t-" + props.get("tabId", "")
        title = props.get("title", "")
        doc_tab = tab_data.get("documentTab", {})
        body = doc_tab.get("body", {})
        blocks = _strip_leading_section_break(
            self._walk_structural_elements(body.get("content", []))
        )
        # Footnotes can live inside documentTab.footnotes
        for fid, fn in (doc_tab.get("footnotes") or {}).items():
            fn_id = "fn-" + fid
            fn_blocks = self._walk_structural_elements(fn.get("content", []))
            self.footnotes[fn_id] = Footnote(footnote_id=fn_id, blocks=fn_blocks)
        children: list[Tab] = []
        for child in tab_data.get("childTabs", []):
            children.append(self._build_tab(child, level=level + 1, parent_id=tab_id))
        return Tab(
            tab_id=tab_id,
            title=title,
            level=level,
            parent_tab_id=parent_id,
            children=children,
            blocks=blocks,
        )

    # -- structural elements ---------------------------------------------

    def _walk_structural_elements(self, elements: list[dict]) -> list:
        out: list = []
        for el in elements:
            if "paragraph" in el:
                out.append(self._build_paragraph(el["paragraph"]))
            elif "table" in el:
                out.append(self._build_table(el["table"]))
            elif "sectionBreak" in el:
                out.append(SectionBreak())
            elif "tableOfContents" in el:
                out.append(TableOfContents())
            else:
                out.append(Unsupported(kind="structuralElement", raw=el))
        return out

    def _build_paragraph(self, p: dict):
        ps = p.get("paragraphStyle") or {}
        nst = ps.get("namedStyleType") or "NORMAL_TEXT"

        runs = self._build_runs(p.get("elements", []))

        bullet = p.get("bullet")
        if bullet is not None:
            list_id = bullet.get("listId", "")
            level = bullet.get("nestingLevel", 0) or 0
            kind = self._infer_list_kind(list_id, level)
            return ListItem(
                level=level,
                kind=kind,
                list_id=list_id,
                runs=runs,
            )

        # Heading?
        if nst.startswith("HEADING_"):
            level = int(nst.split("_")[1])
            anchor = ps.get("headingId")
            return Heading(
                level=level,
                runs=_rstrip_runs(runs),
                anchor_id=("h-" + anchor) if anchor else None,
            )
        if nst == "TITLE":
            return Heading(
                level=1,
                runs=_rstrip_runs(runs),
                anchor_id=None,
                classes=[NAMED_STYLE_CLASSES["TITLE"]],
            )
        if nst == "SUBTITLE":
            return Paragraph(runs=runs, classes=[NAMED_STYLE_CLASSES["SUBTITLE"]])

        # NORMAL_TEXT (or anything else): bare paragraph; classes only added
        # if the named style itself diverges from default — we let the inline
        # override mechanism cover that.
        classes: list[str] = []
        if not is_default_for_named_style(nst) and nst in NAMED_STYLE_CLASSES:
            classes.append(NAMED_STYLE_CLASSES[nst])
        return Paragraph(runs=runs, classes=classes)

    def _infer_list_kind(self, list_id: str, level: int) -> str:
        """'bulleted' or 'ordered' based on glyphType in the list definition."""
        ldef = self.lists.get(list_id, {})
        nesting_levels = (ldef.get("listProperties") or {}).get("nestingLevels", [])
        if level < len(nesting_levels):
            glyph_type = nesting_levels[level].get("glyphType")
            if glyph_type and glyph_type != "GLYPH_TYPE_UNSPECIFIED":
                return "ordered"
        return "bulleted"

    def _build_table(self, t: dict) -> Table:
        rows: list[Row] = []
        for tr in t.get("tableRows", []):
            cells: list[Cell] = []
            for tc in tr.get("tableCells", []):
                style = tc.get("tableCellStyle") or {}
                cells.append(Cell(
                    blocks=self._walk_structural_elements(tc.get("content", [])),
                    colspan=style.get("columnSpan") or 1,
                    rowspan=style.get("rowSpan") or 1,
                ))
            rows.append(Row(cells=cells))
        return Table(rows=rows)

    # -- runs -----------------------------------------------------------

    def _build_runs(self, elements: list[dict]) -> list:
        """Map a paragraph's `elements` list into a flat list of inline AST
        nodes, threading suggestion-id wrappers as we go."""
        out: list = []
        for el in elements:
            for node in self._build_inline(el):
                out.append(node)
        return out

    def _build_inline(self, el: dict):
        # Handle each inline-element kind. Yields zero or more inline nodes.
        if "textRun" in el:
            yield from self._build_text_run(el["textRun"])
            return
        if "footnoteReference" in el:
            ref = el["footnoteReference"]
            yield FootnoteRef(footnote_id="fn-" + ref.get("footnoteId", ""))
            return
        if "inlineObjectElement" in el:
            ioe = el["inlineObjectElement"]
            obj_id = ioe.get("inlineObjectId", "")
            obj = self.inline_objects.get(obj_id, {})
            if not obj:
                # Truly dangling: typically the decorative icon rendered
                # next to a richLink/dateElement chip. Suppress.
                return
            yield self._build_image(obj_id, obj)
            return
        if "person" in el:
            person = el["person"]
            yield SmartChip(
                kind="person",
                data={"email": person.get("personProperties", {}).get("email", "")},
                display_text=person.get("personProperties", {}).get("name", ""),
            )
            return
        if "richLink" in el:
            rl = el["richLink"]
            props = rl.get("richLinkProperties", {})
            mime_type = props.get("mimeType", "")
            yield SmartChip(
                kind=_classify_rich_link(mime_type),
                data={
                    "uri": props.get("uri", ""),
                    "mime_type": mime_type,
                    "rich_link_id": rl.get("richLinkId", ""),
                },
                display_text=props.get("title", ""),
            )
            return
        if "dateElement" in el:
            de = el["dateElement"]
            props = de.get("dateElementProperties") or {}
            yield SmartChip(
                kind="date",
                data={
                    "date_id": de.get("dateId", ""),
                    "timestamp": props.get("timestamp", ""),
                    "date_format": props.get("dateFormat", ""),
                    "time_format": props.get("timeFormat", ""),
                    "locale": props.get("locale", ""),
                },
                display_text=props.get("displayText") or props.get("timestamp", ""),
            )
            return
        if "horizontalRule" in el:
            yield HorizontalRule()
            return
        if "pageBreak" in el:
            yield PageBreak()
            return
        if "columnBreak" in el:
            yield LineBreak()
            return
        if "equation" in el:
            yield InlineEquation(latex=el["equation"].get("rendering", "") or "")
            return
        if "autoText" in el:
            # Auto-text (page numbers, etc.) — render as run-with-content.
            at = el["autoText"]
            yield Run(text=at.get("content") or f"<{at.get('type', 'auto')}>")
            return
        yield Unsupported(kind="inlineElement", raw=el)

    def _build_text_run(self, tr: dict):
        content = tr.get("content", "")
        if not content:
            return
        ts = tr.get("textStyle") or {}
        style_dict = _text_style_to_descriptor_dict(ts)
        descriptor = _descriptor_from_dict(style_dict)

        # Synthesize an inline-override class from the descriptor and
        # register it in css_classes so the emitter can render the rule.
        cls = synthesize_inline_class(descriptor)
        if cls and cls not in self.css_classes:
            from google_doc_diff.styles.css import descriptor_to_css
            body = descriptor_to_css(descriptor)
            if body:
                self.css_classes[cls] = body

        # Newlines: Docs textRuns carry trailing "\n" for paragraph boundaries.
        text = content.rstrip("\n")
        if not text:
            return

        # Suggestion wrapping (handled at run granularity).
        sugg_ins_ids = tr.get("suggestedInsertionIds") or []
        sugg_del_ids = tr.get("suggestedDeletionIds") or []
        wrapper: type | None = None
        wrapper_sid: str | None = None
        if sugg_ins_ids:
            wrapper, wrapper_sid = SuggestionIns, sugg_ins_ids[0]
            self._record_suggestion(wrapper_sid, "insertion", tr)
        elif sugg_del_ids:
            wrapper, wrapper_sid = SuggestionDel, sugg_del_ids[0]
            self._record_suggestion(wrapper_sid, "deletion", tr)

        # Split on chip glyphs: Docs encodes voting / reaction chips as
        # Private Use Area codepoints inline within the textRun content
        # (no separate API representation). Split into Run + SmartChip + Run
        # so chips render as recognizable spans rather than invisible glyphs.
        nodes = _split_chips(text, descriptor)
        if wrapper is not None:
            yield wrapper(suggestion_id=wrapper_sid, runs=nodes)
        else:
            yield from nodes

    def _record_suggestion(self, sid: str, kind: str, _source: dict) -> None:
        if sid in self.suggestions:
            existing = self.suggestions[sid]
            if existing.kind != kind:
                # Two operations under the same suggestion ID = replacement.
                self.suggestions[sid] = Suggestion(
                    suggestion_id=sid, author=existing.author,
                    created_time=existing.created_time, kind="replacement",
                    attached_comment_id=existing.attached_comment_id,
                )
            return
        # Author and timestamp aren't in the textRun; the Docs API surfaces
        # author info in `suggestionsViewMode` responses we don't use. Default
        # to a placeholder; replay/from_google_md doesn't apply here.
        self.suggestions[sid] = Suggestion(
            suggestion_id=sid,
            author="unknown@gdoc-diff",
            created_time=datetime.fromtimestamp(0, tz=UTC),
            kind=kind,
        )

    # -- image -----------------------------------------------------------

    def _build_image(self, obj_id: str, obj: dict) -> Image:
        props = (obj.get("inlineObjectProperties") or {}).get("embeddedObject", {})
        emb_image = props.get("imageProperties", {})
        size = props.get("size", {})
        width_pt = (size.get("width") or {}).get("magnitude")
        height_pt = (size.get("height") or {}).get("magnitude")
        width_px = int(round(width_pt * 96 / 72)) if width_pt else None
        height_px = int(round(height_pt * 96 / 72)) if height_pt else None
        src = (
            emb_image.get("contentUri")
            or emb_image.get("sourceUri")
            or ""
        )
        return Image(
            image_id=obj_id,
            src=src,
            alt=props.get("description") or props.get("title") or "",
            width_px=width_px,
            height_px=height_px,
        )


# --- richLink classification ---------------------------------------------

# Map Google's MIME types for in-doc rich links to a stable kind suffix that
# downstream stylesheets / consumers can branch on.
_RICH_LINK_KINDS: dict[str, str] = {
    "application/vnd.google-apps.kix":          "richlink-doc",
    "application/vnd.google-apps.document":     "richlink-doc",
    "application/vnd.google-apps.ritz":         "richlink-spreadsheet",
    "application/vnd.google-apps.spreadsheet":  "richlink-spreadsheet",
    "application/vnd.google-apps.punch":        "richlink-slides",
    "application/vnd.google-apps.presentation": "richlink-slides",
    "application/vnd.google-apps.folder":       "richlink-folder",
    "application/vnd.google-apps.form":         "richlink-form",
    "application/vnd.google-apps.drawing":      "richlink-drawing",
    "application/vnd.google-apps.script":       "richlink-script",
    "application/vnd.google-apps.site":         "richlink-site",
    "application/vnd.google-apps.video":        "richlink-video",
}


def _classify_rich_link(mime_type: str) -> str:
    return _RICH_LINK_KINDS.get(mime_type, "richlink")


# --- chip glyph translation -----------------------------------------------

# Docs encodes inline UI widgets (voting chips, reactions, etc.) as a single
# Private Use Area codepoint (U+E907) inside textRun.content — the actual
# emoji + count for each chip live ONLY in Drive's rendered markdown export,
# not in the Docs API JSON. We emit a placeholder SmartChip(kind='reaction')
# at the JSON-build stage and let chip_counts.attach_widget_renderings fill in
# the emoji + count via the markdown cross-reference.


def _split_chips(text: str, descriptor: StyleDescriptor) -> list:
    """Split text on PUA chip glyphs into a sequence of Runs and SmartChips."""
    out: list = []
    buf = ""
    for ch in text:
        if 0xE000 <= ord(ch) <= 0xF8FF:
            if buf:
                out.append(Run(text=buf, formatting=descriptor))
                buf = ""
            out.append(SmartChip(
                kind="reaction",
                data={"glyph": f"U+{ord(ch):04X}"},
                display_text="?",   # filled in by the chip_counts pass
            ))
        else:
            buf += ch
    if buf:
        out.append(Run(text=buf, formatting=descriptor))
    return out


# --- helpers (also used by tests) ---------------------------------------


def _iter_doc_tabs(tabs: list):
    """Yield every tab dict in document order, recursing into child tabs."""
    for tab in tabs:
        yield tab
        children = tab.get("childTabs") or []
        if children:
            yield from _iter_doc_tabs(children)


def _hash8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _strip_leading_section_break(blocks: list) -> list:
    """Drop a SectionBreak that appears as the first block of a tab/body.

    Docs always emits a structural section break at the top of body content;
    it conveys no semantic information and only adds noise to our output.
    """
    if blocks and isinstance(blocks[0], SectionBreak):
        return blocks[1:]
    return blocks


def _rstrip_runs(runs: list) -> list:
    """Strip trailing whitespace from the last text-bearing run."""
    if not runs:
        return runs
    out = list(runs)
    for i in range(len(out) - 1, -1, -1):
        if isinstance(out[i], Run):
            out[i] = Run(text=out[i].text.rstrip(), formatting=out[i].formatting)
            if not out[i].text:
                out.pop(i)
                continue
            break
        # Non-Run inline node: stop trimming here.
        break
    return out
