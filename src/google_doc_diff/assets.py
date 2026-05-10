"""Image extraction shared between `gdoc pull` and `gdoc replay`."""

from __future__ import annotations

from pathlib import Path

from google_doc_diff.ast.nodes import Image
from google_doc_diff.emit import emit_document_md


def count_images(document) -> int:
    """How many Image nodes appear anywhere in the AST."""
    return sum(1 for _ in _iter_nodes(document) if isinstance(_, Image))


def extract_image_assets(document, md_path: Path, api, *, on_error=None) -> int:
    """Download every Image's src into <md_path>.assets/ and rewrite the AST
    to point at the local copies. Re-emits the markdown if anything was saved.
    Returns the number of images successfully extracted.
    """
    assets_dir = md_path.with_suffix(".assets")
    assets_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for node in _iter_nodes(document):
        if not isinstance(node, Image) or not node.src.startswith("http"):
            continue
        try:
            blob = api.fetch_revision_export(node.src)
        except Exception as e:
            if on_error is not None:
                on_error(node, e)
            continue
        fname = f"{node.image_id}{_guess_ext(node.src)}"
        (assets_dir / fname).write_bytes(blob)
        node.src = f"{assets_dir.name}/{fname}"
        saved += 1
    if saved:
        md_path.write_text(emit_document_md(document))
    return saved


def has_pua_widgets(document) -> bool:
    """True if the AST contains any U+E907 placeholder widget — used to
    short-circuit the markdown-export cross-reference when no chip recovery
    is needed."""
    from google_doc_diff.ast.nodes import SmartChip
    for node in _iter_nodes(document):
        if isinstance(node, SmartChip) and node.data.get("glyph") == "U+E907":
            return True
    return False


_CHILD_ATTRS = ("runs", "blocks", "rows", "cells", "children", "tabs")


def _iter_nodes(root):
    """Recursive descent over the standard child-attribute names."""
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
            continue
        yield node
        for attr in _CHILD_ATTRS:
            children = getattr(node, attr, None)
            if children:
                stack.extend(reversed(list(children)))


def _guess_ext(url: str) -> str:
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        if ext in url.lower():
            return ext
    return ".bin"
