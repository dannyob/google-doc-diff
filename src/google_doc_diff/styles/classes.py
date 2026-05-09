"""Class-name derivation for Docs styles.

Bare HTML elements carry the default styling for their named-style type
(`<h1>` is Heading 1; `<p>` is Normal). A class is added only when:
    1. The named style cannot be expressed by the bare element (Title, Subtitle).
    2. The element has inline overrides that diverge from its named style — a
       synthesized `gd-style-{hash8}` class collapses identical overrides.

Hashes are derived via hashlib (NOT Python's built-in hash, which is
PYTHONHASHSEED-randomized) so synthesized class names are stable across
processes — required by the spec's determinism guarantee.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict

from google_doc_diff.ast.nodes import StyleDescriptor

# Maps Docs `namedStyleType` -> our class name. Heading types map to bare
# element names so `<h1>...</h1>` is the canonical form; the class only
# attaches when something diverges (see synthesize_inline_class).
NAMED_STYLE_CLASSES: dict[str, str] = {
    "NORMAL_TEXT": "gd-normal",
    "TITLE": "gd-title",
    "SUBTITLE": "gd-subtitle",
    "HEADING_1": "gd-heading-1",
    "HEADING_2": "gd-heading-2",
    "HEADING_3": "gd-heading-3",
    "HEADING_4": "gd-heading-4",
    "HEADING_5": "gd-heading-5",
    "HEADING_6": "gd-heading-6",
}

# Named-style types whose bare HTML element CAN'T carry the right styling
# without a class — TITLE and SUBTITLE both render as h1/p but need
# distinguishing classes; everything else uses bare elements by default.
ALWAYS_CLASS_NAMED_STYLES = {"TITLE", "SUBTITLE"}


def named_paragraph_class(named_style_type: str) -> str | None:
    """Return the CSS class for a Docs named style.

    Returns the class string for TITLE/SUBTITLE (always emitted); returns the
    class for HEADING_n / NORMAL_TEXT only when the caller explicitly needs
    it (e.g., applying named style to a non-heading paragraph).
    """
    return NAMED_STYLE_CLASSES.get(named_style_type)


def is_default_for_named_style(named_style_type: str) -> bool:
    """True when this named style can be emitted as a bare HTML element."""
    return named_style_type not in ALWAYS_CLASS_NAMED_STYLES


def synthesize_inline_class(descriptor: StyleDescriptor) -> str | None:
    """Synthesize a deterministic class for a non-default StyleDescriptor.

    Returns None if the descriptor is empty (all fields None / falsy meaning
    "no override"); the caller emits the bare element in that case.
    """
    if descriptor == StyleDescriptor():
        return None
    blob = repr(sorted(asdict(descriptor).items()))
    h = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]
    return f"gd-style-{h}"


def list_class_for(list_id: str) -> str:
    """Synthesize a deterministic class for a Docs list ID.

    Docs list IDs (e.g. 'kix.abc123...') are stable per-doc but not
    human-readable; we hash to a short suffix.
    """
    h = hashlib.sha256(list_id.encode("utf-8")).hexdigest()[:6]
    return f"gd-list-{h}"
