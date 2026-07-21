"""Per-tab pull for documents whose bulk `documents.get` call returns 500.

See docs/superpowers/specs/2026-07-20-per-tab-pull-design.md. This path is
lossy by necessity: Google exposes no high-fidelity route to these documents
at any granularity, so content arrives as markdown (no suggestions, no stable
paragraph ids) and comments are re-attached from the Drive API, which is
unaffected by document size.
"""

import hashlib

from google_doc_diff.tabs import TabRef


class PerTabError(Exception):
    """A per-tab pull could not be completed safely."""


def _looks_like_html(text: str) -> bool:
    return text.lstrip()[:200].lower().startswith(("<!doctype", "<html"))


def validate_tab_exports(exports: dict[str, str], refs: list[TabRef]) -> None:
    """Abort on export sets that would produce silently wrong content.

    Raises PerTabError if any export is an HTML error page, or if two tabs
    exported identical bytes -- the signature of a tab id the export endpoint
    did not recognise, since it answers those with the default tab's content
    and a 200.
    """
    titles = {r.tab_id: r.title for r in refs}

    def label(tab_id: str) -> str:
        return f"{titles.get(tab_id, '?')!r} ({tab_id})"

    for tab_id, text in exports.items():
        if _looks_like_html(text):
            raise PerTabError(
                f"tab {label(tab_id)} returned an HTML error page, not markdown"
            )

    by_hash: dict[str, list[str]] = {}
    for tab_id, text in exports.items():
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        by_hash.setdefault(digest, []).append(tab_id)

    for tab_ids in by_hash.values():
        if len(tab_ids) > 1:
            names = ", ".join(label(t) for t in sorted(tab_ids))
            raise PerTabError(
                f"tabs exported identical content: {names}. The export endpoint "
                "answers an unrecognised tab id with the default tab's content, "
                "so this is either a stale tab id or two genuinely identical tabs."
            )
