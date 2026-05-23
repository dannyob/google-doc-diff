"""AST node dataclasses for google-doc-diff.

The AST mirrors the Google Docs API structure, normalized into Python
dataclasses. Stable IDs (where the API exposes them) are first-class: every
comment, suggestion, footnote, tab, bookmark, named-range, and image carries
its Docs/Drive ID, prefixed by kind. See the spec's "Stable ID strategy"
section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: F401  (used by Comment / Suggestion below)

# --- Run + StyleDescriptor --------------------------------------------------


@dataclass(frozen=True)
class StyleDescriptor:
    """Frozen bundle of inline formatting properties for a Run.

    None on a field means 'inherit / not set'. Equality and hash are by
    field-tuple so identical descriptors collapse to the same downstream
    artifacts.

    NB: Python's built-in hash() randomizes string hashes per process
    (PYTHONHASHSEED). When `styles/classes.py` synthesizes a deterministic
    class name like `gd-style-{hash8}`, it MUST use hashlib (e.g.
    sha256(repr(descriptor).encode()).hexdigest()[:8]) — never the built-in
    hash(). This dataclass's __hash__ is fine for in-process dict keys but
    NOT for cross-process determinism.
    """

    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strikethrough: bool | None = None
    superscript: bool | None = None
    subscript: bool | None = None
    font_family: str | None = None
    font_size_pt: float | None = None
    foreground_color: str | None = None      # "#RRGGBB"
    background_color: str | None = None
    link_url: str | None = None
    link_anchor: str | None = None
    # v2 round-trip extensions (OT ts_* coverage beyond the public Docs API)
    vertical_alignment: str | None = None    # 'normal' | 'super' | 'sub'
    small_caps: bool | None = None
    weight: int | None = None                # 100..900, separate from `bold`
    language: str | None = None              # BCP-47 tag


@dataclass(frozen=True)
class ParagraphProperties:
    """Paragraph-level OT properties (the `ps_*` namespace in the editor model).

    None on a field means 'inherit / not set' — matches StyleDescriptor.
    Round-trip carriers in markdown: `--ot-*` custom properties inside the
    inline `<style>` block, referenced from blocks via class names.
    """

    line_height: float | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    indent_left_pt: float | None = None
    indent_right_pt: float | None = None
    indent_first_line_pt: float | None = None
    alignment: str | None = None             # 'left' | 'right' | 'center' | 'justify'
    heading_depth: int | None = None         # 1..6, or None for body text
    keep_with_next: bool | None = None
    keep_lines_together: bool | None = None
    page_break_before: bool | None = None
    direction: str | None = None             # 'ltr' | 'rtl'


@dataclass
class Run:
    """A contiguous span of text with one StyleDescriptor."""

    text: str
    formatting: StyleDescriptor = field(default_factory=StyleDescriptor)


# --- Inline (run-level) wrappers --------------------------------------------


@dataclass
class CommentAnchor:
    """Marks a span of inline content as the anchor for a comment.

    Comment text + replies live in Document.comments[comment_id].
    """

    comment_id: str
    runs: list[Run] = field(default_factory=list)


@dataclass
class SuggestionIns:
    """An insertion suggestion. May share suggestion_id with a paired
    SuggestionDel for replacement suggestions."""

    suggestion_id: str
    runs: list[Run] = field(default_factory=list)


@dataclass
class SuggestionDel:
    """A deletion suggestion. May share suggestion_id with a paired
    SuggestionIns for replacement suggestions."""

    suggestion_id: str
    runs: list[Run] = field(default_factory=list)


@dataclass
class FootnoteRef:
    """A footnote marker in prose; the footnote body lives in
    Document.footnotes[footnote_id]."""

    footnote_id: str


@dataclass
class BookmarkAnchor:
    bookmark_id: str


@dataclass
class NamedRangeAnchor:
    named_range_id: str


@dataclass
class SmartChip:
    """A Docs smart chip (person, file, date, place, calendar event, ...).

    `data` carries kind-specific fields verbatim from the API.
    """

    kind: str
    data: dict
    display_text: str = ""


@dataclass(frozen=True)
class Voter:
    """One voter on a VotingChip, identified by their Google obfuscated id."""

    obfuscated_id: str


@dataclass
class VotingChip:
    """A doc-tag voting chip with full per-voter state.

    Captured at pull time from the OT stream (the public Docs API hides this
    as a U+E907 placeholder). Round-trip target: `voting-chip-populate` ops
    via the `/save` channel.
    """

    chip_id: str
    emoji: str
    voters: list[Voter] = field(default_factory=list)
    current_user_voted: bool = False
    signature: str = ""


@dataclass
class InlineEquation:
    latex: str


@dataclass(frozen=True)
class LineBreak:
    """A soft line break inside a paragraph (Docs <br>-equivalent)."""


@dataclass
class Unsupported:
    """Typed-fallback node for any Docs API element this version doesn't
    understand. Both serializers preserve it as an opaque blob with kind
    and raw JSON in data attributes."""

    kind: str
    raw: dict


# --- Block-level nodes ------------------------------------------------------


@dataclass
class Heading:
    level: int                           # 1..6
    runs: list[Run] = field(default_factory=list)
    anchor_id: str | None = None
    classes: list[str] = field(default_factory=list)
    paragraph_id: str | None = None      # v2: stable id for diff/round-trip
    paragraph_properties: ParagraphProperties | None = None


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    paragraph_id: str | None = None      # v2: stable id for diff/round-trip
    paragraph_properties: ParagraphProperties | None = None


@dataclass
class ListItem:
    level: int
    kind: str                            # 'bulleted' | 'ordered'
    list_id: str
    runs: list[Run] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)


@dataclass
class Cell:
    blocks: list = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    classes: list[str] = field(default_factory=list)


@dataclass
class Row:
    cells: list[Cell] = field(default_factory=list)


@dataclass
class Table:
    rows: list[Row] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)


@dataclass
class Image:
    image_id: str
    src: str
    alt: str = ""
    width_px: int | None = None
    height_px: int | None = None


@dataclass
class CodeBlock:
    text: str
    language: str | None = None


@dataclass
class EquationBlock:
    latex: str


@dataclass(frozen=True)
class HorizontalRule:
    pass


@dataclass(frozen=True)
class PageBreak:
    pass


@dataclass(frozen=True)
class SectionBreak:
    pass


@dataclass(frozen=True)
class TableOfContents:
    pass


@dataclass
class Conflict:
    """A three-way-merge conflict the user must resolve in markdown.

    Both sides edited the same region but with different content. The
    block carries the local and remote blocks side-by-side; emit
    renders it as a `.gd-conflict` pandoc div so the user can edit it
    down to whichever side they want (or a manual merge), then re-run
    `gdoc push --continue`.
    """
    conflict_id: str
    local_blocks: list = field(default_factory=list)
    remote_blocks: list = field(default_factory=list)
    base_blocks: list = field(default_factory=list)


# --- Cross-cutting collections ---------------------------------------------


@dataclass
class CommentReply:
    reply_id: str
    author: str                          # email; "unknown@gdoc-diff" if missing
    created_time: datetime
    modified_time: datetime
    content: str
    action: str | None = None            # None | 'resolve' | 'reopen'
    deleted: bool = False


@dataclass
class Comment:
    comment_id: str
    author: str
    created_time: datetime
    modified_time: datetime
    content: str
    quoted_text: str = ""                # 'quotedFileContent.value' from API
    resolved: bool = False
    deleted: bool = False
    replies: list[CommentReply] = field(default_factory=list)
    orphaned: bool = False


@dataclass
class Suggestion:
    suggestion_id: str
    author: str
    created_time: datetime
    kind: str                            # 'insertion' | 'deletion' | 'replacement'
    attached_comment_id: str | None = None


@dataclass
class Footnote:
    footnote_id: str
    blocks: list = field(default_factory=list)


# --- Tab + Document -------------------------------------------------------


@dataclass
class Tab:
    tab_id: str
    title: str
    level: int                           # 0 = top-level
    parent_tab_id: str | None = None
    children: list[Tab] = field(default_factory=list)
    blocks: list = field(default_factory=list)


@dataclass
class Document:
    doc_id: str
    title: str
    revision_id: str
    drive_url: str
    captured_at: datetime
    schema_version: int
    last_modifying_user: str | None
    source_mode: str                      # 'pull' | 'replay'
    comments_preserved: bool
    suggestions_preserved: bool
    tabs: list[Tab] = field(default_factory=list)
    comments: dict[str, Comment] = field(default_factory=dict)
    suggestions: dict[str, Suggestion] = field(default_factory=dict)
    footnotes: dict[str, Footnote] = field(default_factory=dict)
    # The next three are typed as dict[str, dict] in v1 (raw API blobs).
    # Tighten to typed dataclasses when classes.py / list emitter / image
    # extractor actually consume them.
    named_styles: dict[str, dict] = field(default_factory=dict)
    list_definitions: dict[str, dict] = field(default_factory=dict)
    inline_objects: dict[str, dict] = field(default_factory=dict)
    css_classes: dict[str, str] = field(default_factory=dict)
    # v2 round-trip state: opaque bag emitted under the frontmatter `gdoc:`
    # key. Holds base_revision, model_version, per-chip signatures, raw
    # `Unsupported` payloads, etc. Empty by default for backwards-compat.
    gdoc_state: dict = field(default_factory=dict)
