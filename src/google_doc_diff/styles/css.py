"""CSS-rule generation for descriptors and named styles.

The emitted CSS pairs the bare element selector with the explicit class so
either form gets the same styling, e.g.:

    h1, .gd-heading-1 { font-size: 20pt; ... }

Synthesized classes for inline overrides emit as plain class selectors:

    .gd-style-a1b2c3d4 { font-family: "Source Code Pro"; ... }
"""

from __future__ import annotations

from google_doc_diff.ast.nodes import Document, ParagraphProperties, StyleDescriptor
from google_doc_diff.styles.classes import (
    NAMED_STYLE_CLASSES,
    is_default_for_named_style,
)

# Paragraph-property field name -> CSS custom-property name.
# Custom properties (`--ot-*`) carry OT names directly so the round-trip
# parser doesn't need to reverse a lossy CSS-property mapping.
_OT_PROP_NAMES: dict[str, str] = {
    "alignment": "--ot-alignment",
    "direction": "--ot-direction",
    "heading_depth": "--ot-heading-depth",
    "indent_first_line_pt": "--ot-indent-first-line",
    "indent_left_pt": "--ot-indent-left",
    "indent_right_pt": "--ot-indent-right",
    "keep_lines_together": "--ot-keep-lines-together",
    "keep_with_next": "--ot-keep-with-next",
    "line_height": "--ot-line-height",
    "page_break_before": "--ot-page-break-before",
    "space_after_pt": "--ot-space-after",
    "space_before_pt": "--ot-space-before",
}


def _format_ot_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def paragraph_props_to_css(pp) -> str:
    """Render set fields of a ParagraphProperties as `--ot-*: value;` lines.

    Accepts either a ParagraphProperties instance or a plain dict so callers
    can serialize partial property bags without instantiating. Output is
    deterministic (sorted by CSS custom-property name) and omits None fields.
    """
    if isinstance(pp, ParagraphProperties):
        items = {k: getattr(pp, k) for k in _OT_PROP_NAMES}
    else:
        items = dict(pp)
    lines = []
    for key in sorted(_OT_PROP_NAMES):
        v = items.get(key)
        if v is None:
            continue
        lines.append(f"  {_OT_PROP_NAMES[key]}: {_format_ot_value(v)};")
    return "\n".join(lines)

# Bare HTML element to pair with each named-style class.
NAMED_STYLE_ELEMENT: dict[str, str] = {
    "NORMAL_TEXT": "p",
    "TITLE": "h1",
    "SUBTITLE": "p",
    "HEADING_1": "h1",
    "HEADING_2": "h2",
    "HEADING_3": "h3",
    "HEADING_4": "h4",
    "HEADING_5": "h5",
    "HEADING_6": "h6",
}


def descriptor_to_css(d: StyleDescriptor) -> str:
    """Render the StyleDescriptor's set fields as CSS declarations.

    Returns the rule body (no braces, no selector). Empty fields are omitted.
    Properties emit in a stable order so output is deterministic.
    """
    parts: list[str] = []
    if d.bold is not None:
        parts.append(f"font-weight: {700 if d.bold else 400}")
    if d.italic:
        parts.append("font-style: italic")
    decorations: list[str] = []
    if d.underline:
        decorations.append("underline")
    if d.strikethrough:
        decorations.append("line-through")
    if decorations:
        parts.append(f"text-decoration: {' '.join(decorations)}")
    if d.superscript:
        parts.append("vertical-align: super")
        parts.append("font-size: smaller")
    if d.subscript:
        parts.append("vertical-align: sub")
        parts.append("font-size: smaller")
    if d.font_family:
        parts.append(f'font-family: "{d.font_family}"')
    if d.font_size_pt is not None:
        # Format with no trailing zeros: 11.0 -> 11pt, 11.5 -> 11.5pt
        if float(d.font_size_pt).is_integer():
            parts.append(f"font-size: {int(d.font_size_pt)}pt")
        else:
            parts.append(f"font-size: {d.font_size_pt}pt")
    if d.foreground_color:
        parts.append(f"color: {d.foreground_color}")
    if d.background_color:
        parts.append(f"background-color: {d.background_color}")
    return "; ".join(parts)


def paired_named_rule(tag: str, class_name: str, body: str) -> str:
    """Render `tag, .class { body; }`."""
    if not body:
        return ""
    return f"{tag}, .{class_name} {{ {body}; }}"


def class_only_rule(class_name: str, body: str) -> str:
    if not body:
        return ""
    return f".{class_name} {{ {body}; }}"


def build_css(doc: Document) -> str:
    """Build the full CSS block for a Document.

    Walks `doc.named_styles` (each entry is the raw API style descriptor as a
    dict) and `doc.css_classes` (synthesized inline-override class -> rule
    body produced during AST emission planning).
    """
    rules: list[str] = []

    # Named-style rules (paired tag + class so bare elements pick up the styling).
    for named_type, class_name in NAMED_STYLE_CLASSES.items():
        descriptor_dict = doc.named_styles.get(named_type)
        if not descriptor_dict:
            continue
        descriptor = _dict_to_descriptor(descriptor_dict)
        body = descriptor_to_css(descriptor)
        if not body:
            continue
        if is_default_for_named_style(named_type):
            rules.append(paired_named_rule(NAMED_STYLE_ELEMENT[named_type], class_name, body))
        else:
            rules.append(
                f"{NAMED_STYLE_ELEMENT[named_type]}.{class_name} {{ {body}; }}"
            )

    # Synthesized inline-override classes.
    for class_name in sorted(doc.css_classes):
        body = doc.css_classes[class_name]
        if body:
            rules.append(class_only_rule(class_name, body))

    return "\n".join(r for r in rules if r)


def _dict_to_descriptor(d: dict) -> StyleDescriptor:
    """Coerce a raw API-shaped style dict into a StyleDescriptor.

    Accepts the keys our future from_docs_json builder will use; unknown keys
    are ignored so the function stays forward-compatible with whatever the
    builder emits next.
    """
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
    )
