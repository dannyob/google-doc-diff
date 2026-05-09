"""v2 round-trip parser for our emitted HTML. Stubbed in v1."""

from __future__ import annotations

from google_doc_diff.ast.nodes import Document


def parse_html(html: str) -> Document:  # pragma: no cover
    """Parse our emitted HTML back into a Document AST.

    Not implemented in v1. See parse/markdown.py for context.
    """
    raise NotImplementedError(
        "HTML round-trip parser is v2 work. "
        "v1 enforces round-trip readiness via the structural attribute audit; "
        "see docs/superpowers/specs/2026-05-09-google-doc-diff-design.md."
    )
