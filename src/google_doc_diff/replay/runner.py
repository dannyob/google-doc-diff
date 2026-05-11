"""Replay event executor.

For each event in the merged timeline:
  1. Determine the prose state (most recent prose_change <= event.timestamp).
     Fetch via Drive v2 exportLinks (text/markdown), cached per revision.
  2. Build the AST from that markdown via from_google_md.
  3. Apply the comment state (all comment events <= event.timestamp).
  4. Re-anchor surviving comments via quotedFileContent (uses the existing
     anchor_comments pass; orphans are tagged orphaned=True).
  5. Set captured_at = event.timestamp (NOT wall-clock) for byte-identical
     re-runs of `--resume`.
  6. Emit Markdown; write to the output path (overwriting prior).
  7. With --commit: stage the file + create one git commit per event with
     event.author and event.timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from google_doc_diff.api import GdocAPI, drive_url_for
from google_doc_diff.assets import extract_image_assets
from google_doc_diff.ast.anchor_comments import anchor_comments
from google_doc_diff.ast.chip_counts import attach_widget_renderings
from google_doc_diff.ast.from_docs_json import build_document
from google_doc_diff.ast.from_google_md import build_from_google_md
from google_doc_diff.ast.nodes import Comment, CommentReply, Document
from google_doc_diff.emit import emit_document_md
from google_doc_diff.replay import git as gitwrap
from google_doc_diff.replay.timeline import Event


@dataclass
class RunnerOptions:
    out_path: Path
    commit: bool = False
    cwd: Path = field(default_factory=Path.cwd)
    extract_assets: bool = False
    include_comments: bool = True
    progress: bool = True


class ReplayRunner:
    def __init__(self, api: GdocAPI, doc_id: str, options: RunnerOptions):
        self.api = api
        self.doc_id = doc_id
        self.opt = options
        self._md_cache: dict[str, str] = {}
        self._current_docs_json: dict | None = None

    def _ensure_current_doc_loaded(self) -> dict:
        if self._current_docs_json is None:
            self._current_docs_json = self.api.get_document(self.doc_id)
        return self._current_docs_json

    # -- public ----------------------------------------------------------

    def execute(
        self,
        events: list[Event],
        *,
        on_event=None,
    ) -> list[tuple[Event, str | None]]:
        """Run the timeline. Returns list of (event, git_sha-or-None).

        After the loop, overwrites the output file with the rich JSON-derived
        live state (full chip metadata, suggestions intact) but does NOT
        commit it. So HEAD = historical replay; working tree = live doc;
        `git diff HEAD` = what's changed since the last replayed event.
        """
        results: list[tuple[Event, str | None]] = []
        latest_revision: str | None = None
        comment_state: dict[str, Comment] = {}

        # Hoist current-doc fetch + title out of the per-event loop.
        try:
            self._ensure_current_doc_loaded()
            title = (self._current_docs_json or {}).get("title", "")
        except Exception:
            title = ""

        for ev in events:
            if ev.kind == "prose_change":
                latest_revision = ev.revision_id
            else:
                _apply_comment_event(comment_state, ev, self.opt.include_comments)

            if latest_revision is None:
                results.append((ev, None))
                if on_event:
                    on_event(ev, None)
                continue

            try:
                md = self._fetch_revision_md(latest_revision, ev)
                doc = self._build_event_document(
                    md, ev, latest_revision, comment_state, title,
                )
                self.opt.out_path.write_text(emit_document_md(doc))
            except Exception as e:
                # A single transient failure (e.g. Google's export endpoint
                # returning 500 for one revision after retries) shouldn't
                # kill an otherwise-successful replay. Skip this event with
                # a marker and continue; --resume can retry it later.
                results.append((ev, None))
                if on_event:
                    on_event(ev, f"(skipped: {e})")
                continue

            sha: str | None = None
            if self.opt.commit:
                sha = self._commit_event(ev)
            results.append((ev, sha))
            if on_event:
                on_event(ev, sha)

        if results:
            try:
                self._write_rich_head_state()
            except Exception as e:
                if on_event:
                    on_event(None, f"rich head state failed: {e}")
        return results

    def _write_rich_head_state(self) -> None:
        """Overwrite the output file with the live doc's rich JSON state.
        Reuses cached docs_json + cached markdown when available."""
        from google_doc_diff.assets import has_pua_widgets

        docs_json = self._ensure_current_doc_loaded()
        try:
            comments_json = self.api.list_comments(self.doc_id)
        except Exception:
            comments_json = []
        doc = build_document(
            docs_json,
            comments_json=comments_json,
            drive_url=drive_url_for(self.doc_id),
        )
        doc.source_mode = "pull"   # head state is identical to a fresh pull
        if has_pua_widgets(doc):
            md = self._head_revision_md()
            if md is not None:
                try:
                    attach_widget_renderings(doc, md)
                except Exception:
                    pass
        self.opt.out_path.write_text(emit_document_md(doc))
        if self.opt.extract_assets:
            extract_image_assets(doc, self.opt.out_path, self.api)

    def _head_revision_md(self) -> str | None:
        """Return the markdown export of the head revision, reusing the
        per-revision cache if the head was already fetched during the loop."""
        head_rev_id = (self._current_docs_json or {}).get("revisionId")
        if head_rev_id and head_rev_id in self._md_cache:
            return self._md_cache[head_rev_id]
        try:
            revs = self.api.list_revisions(self.doc_id)
            if not revs:
                return None
            md_url = (revs[-1].get("exportLinks") or {}).get("text/markdown")
            if not md_url:
                return None
            text = self.api.fetch_revision_export(md_url).decode("utf-8", errors="replace")
            self._md_cache[revs[-1]["id"]] = text
            return text
        except Exception:
            return None

    # -- helpers ----------------------------------------------------------

    def _fetch_revision_md(self, revision_id: str, ev: Event) -> str:
        if revision_id in self._md_cache:
            return self._md_cache[revision_id]
        export_links = ev.payload.get("export_links") if ev.kind == "prose_change" else None
        url = (export_links or {}).get("text/markdown") if export_links else None
        if not url:
            # Fall back to looking it up directly. (Shouldn't normally fire
            # because every prose_change carries its export_links payload.)
            revs = self.api.list_revisions(self.doc_id)
            for r in revs:
                if r["id"] == revision_id:
                    url = (r.get("exportLinks") or {}).get("text/markdown")
                    break
        if not url:
            raise RuntimeError(f"no text/markdown exportLink for revision {revision_id}")
        body = self.api.fetch_revision_export(url)
        text = body.decode("utf-8", errors="replace")
        self._md_cache[revision_id] = text
        return text

    def _build_event_document(
        self,
        md: str,
        ev: Event,
        revision_id: str,
        comment_state: dict[str, Comment],
        title: str,
    ) -> Document:
        doc = build_from_google_md(
            md,
            doc_id=self.doc_id,
            title=title,
            revision_id=revision_id,
            captured_at=ev.timestamp,
            last_modifying_user=ev.author,
            source_mode="replay",
        )
        doc.drive_url = drive_url_for(self.doc_id)
        if self.opt.include_comments:
            doc.comments = {cid: c for cid, c in comment_state.items() if not c.deleted}
            doc.comments_preserved = True
            anchor_comments(doc)
        return doc

    def _commit_event(self, ev: Event) -> str:
        rel_path = self.opt.out_path
        try:
            rel_path = self.opt.out_path.relative_to(self.opt.cwd)
        except ValueError:
            pass
        gitwrap.add([rel_path], cwd=self.opt.cwd)
        msg = _commit_message_for(ev)
        name, email = _split_author(ev.author)
        return gitwrap.commit(
            msg,
            author_name=name,
            author_email=email,
            timestamp=ev.timestamp,
            cwd=self.opt.cwd,
        )


def _apply_comment_event(state: dict[str, Comment], ev: Event, include_comments: bool) -> None:
    if not include_comments:
        return
    if ev.kind == "comment_create":
        c = ev.payload.get("comment") or {}
        state[ev.comment_id] = _comment_from_api(c)
    elif ev.kind == "comment_edit":
        if ev.comment_id in state:
            updated = _comment_from_api(ev.payload.get("comment") or {})
            cur = state[ev.comment_id]
            cur.content = updated.content
            cur.modified_time = ev.timestamp
    elif ev.kind == "comment_delete":
        if ev.comment_id in state:
            state[ev.comment_id].deleted = True
    elif ev.kind in ("reply_create", "reply_resolve", "reply_reopen"):
        cur = state.get(ev.comment_id)
        if not cur:
            return
        r = ev.payload.get("reply") or {}
        action = None
        if ev.kind == "reply_resolve":
            action = "resolve"
            cur.resolved = True
        elif ev.kind == "reply_reopen":
            action = "reopen"
            cur.resolved = False
        cur.replies.append(CommentReply(
            reply_id="r-" + r.get("id", ""),
            author=ev.author,
            created_time=ev.timestamp,
            modified_time=ev.timestamp,
            content=r.get("content", ""),
            action=action,
        ))


def _comment_from_api(c: dict) -> Comment:
    from google_doc_diff.ast.from_docs_json import _author_email, _parse_iso
    return Comment(
        comment_id="c-" + c.get("id", ""),
        author=_author_email(c.get("author")),
        created_time=_parse_iso(c.get("createdTime")),
        modified_time=_parse_iso(c.get("modifiedTime") or c.get("createdTime")),
        content=c.get("content", ""),
        quoted_text=(c.get("quotedFileContent") or {}).get("value", ""),
        resolved=bool(c.get("resolved")),
        deleted=bool(c.get("deleted")),
        replies=[],
    )


def _commit_message_for(ev: Event) -> str:
    if ev.kind == "prose_change":
        return f"prose: revision {ev.revision_id}"
    if ev.kind == "comment_create":
        return f"comment: {ev.comment_id}"
    if ev.kind == "comment_edit":
        return f"comment edit: {ev.comment_id}"
    if ev.kind == "comment_delete":
        return f"comment delete: {ev.comment_id}"
    if ev.kind == "reply_create":
        return f"reply: {ev.comment_id} {ev.reply_id}"
    if ev.kind == "reply_resolve":
        return f"resolve: {ev.comment_id}"
    if ev.kind == "reply_reopen":
        return f"reopen: {ev.comment_id}"
    return ev.kind


def _split_author(author: str) -> tuple[str, str]:
    """Split 'Name <email>' or just 'email' into (name, email)."""
    if "@" in author and "<" not in author:
        return author.split("@")[0], author
    return author, author
