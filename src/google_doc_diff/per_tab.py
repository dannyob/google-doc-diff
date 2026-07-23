"""Per-tab pull for documents whose bulk `documents.get` call returns 500.

See docs/superpowers/specs/2026-07-20-per-tab-pull-design.md. This path is
lossy by necessity: Google exposes no high-fidelity route to these documents
at any granularity, so content arrives as markdown (no suggestions, no stable
paragraph ids) and comments are re-attached from the Drive API, which is
unaffected by document size.
"""

import hashlib
import time
from datetime import UTC, datetime

from google_doc_diff.api import drive_url_for
from google_doc_diff.ast.anchor_comments import anchor_comments
from google_doc_diff.ast.from_docs_json import build_comments
from google_doc_diff.ast.from_google_md import build_from_google_md
from google_doc_diff.ast.nodes import Document, Tab
from google_doc_diff.tabs import TabRef, parse_tab_refs


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


def build_per_tab_document(
    api,
    doc_id: str,
    *,
    delay: float = 1.0,
    sleep=time.sleep,
    on_progress=None,
    on_notice=None,
) -> Document:
    """Build a Document by exporting each tab separately.

    Tabs are fetched sequentially with `delay` seconds between requests: the
    export endpoint rate-limits hard enough that parallel fetching exhausts
    the quota and poisons subsequent serial requests too.
    """
    dropped: list[str] = []
    refs = parse_tab_refs(api.fetch_edit_html(doc_id), dropped=dropped)
    if not refs:
        raise PerTabError(
            f"no tabs found in the /edit payload for {doc_id}; "
            "the document may have no tabs, or the payload format may have changed"
        )
    if dropped and on_notice:
        on_notice(f"skipped {len(dropped)} unparseable tab op(s): {', '.join(dropped)}")

    exports: dict[str, str] = {}
    for n, ref in enumerate(refs, start=1):
        if n > 1:
            sleep(delay)
        exports[ref.tab_id] = api.export_tab_markdown(doc_id, ref.tab_id)
        if on_progress:
            on_progress(ref, n, len(refs))

    validate_tab_exports(exports, refs)

    tabs = [_tab_from_markdown(ref, exports[ref.tab_id], doc_id) for ref in refs]

    meta = api.get_document_metadata(doc_id)
    document = Document(
        doc_id=doc_id,
        title=meta.get("title") or "(untitled)",
        revision_id=meta.get("revisionId", ""),
        drive_url=drive_url_for(doc_id),
        captured_at=datetime.now(UTC),
        schema_version=1,
        last_modifying_user=None,
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=False,
        tabs=tabs,
        comments=build_comments(api.list_comments(doc_id)),
    )
    return anchor_comments(document)


def _tab_from_markdown(ref: TabRef, markdown: str, doc_id: str) -> Tab:
    """Parse one tab's exported markdown into a Tab node.

    build_from_google_md returns a whole Document with a single placeholder
    tab; we keep its blocks and restore the tab's real identity. The `t-`
    prefix matches from_docs_json's convention (kix/enrich strips it again).

    Always level=0 with no parent/children: we have no confirmed op shape
    for child tabs in the /edit payload, so nesting is flattened rather than
    guessed at. `gdoc pull` surfaces this in its degraded-fidelity warning.
    """
    parsed = build_from_google_md(markdown, doc_id=doc_id)
    blocks = parsed.tabs[0].blocks if parsed.tabs else []
    return Tab(tab_id="t-" + ref.tab_id, title=ref.title, level=0, blocks=blocks)
