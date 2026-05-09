"""v2 round-trip parser for our flavored Pandoc markdown. Stubbed in v1."""

from __future__ import annotations

from google_doc_diff.ast.nodes import Document


def parse_markdown(md: str) -> Document:  # pragma: no cover
    """Parse our emitted Markdown back into a Document AST.

    Not implemented in v1. v1 enforces round-trip readiness via the
    structural attribute audit (every stable ID present in both the markdown
    and HTML emit outputs); true equality `parse_markdown(emit_markdown(d))
    == d` is the v2 acceptance gate. See the design spec at
    docs/superpowers/specs/2026-05-09-google-doc-diff-design.md.
    """
    raise NotImplementedError(
        "Markdown round-trip parser is v2 work. "
        "v1 enforces round-trip readiness via the structural attribute audit; "
        "see docs/superpowers/specs/2026-05-09-google-doc-diff-design.md."
    )
