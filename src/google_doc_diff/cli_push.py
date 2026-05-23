"""`gdoc push` implementation.

Two flows wired here:

  - `push_new(...)`     : create a fresh doc, push the markdown into it.
  - `push_force(...)`   : push the local markdown onto an existing doc,
                          overwriting any remote-side changes since the
                          markdown's base. (Three-way merge is deferred —
                          see the design spec.)

Both flows share the same final pipeline: parse → diff → apply.

Functions here take pre-built API services so they can be unit-tested
against fakes. The Click wrapper in `cli.py` builds the real Google API
services and forwards through.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from google_doc_diff.apply.docs_api import apply as apply_docs_api
from google_doc_diff.ast.from_docs_json import build_document
from google_doc_diff.ast.nodes import Document, Tab
from google_doc_diff.emit.markdown import emit_document_md
from google_doc_diff.merge import merge
from google_doc_diff.ops import OpPlan, diff
from google_doc_diff.parse.markdown import parse_document_md


@dataclass
class PushResult:
    doc_id: str
    plan: OpPlan
    ack: dict[str, Any]
    conflicts: list = None  # populated only on the merge path

    def __post_init__(self):
        if self.conflicts is None:
            self.conflicts = []


def push_new(
    md_path: Path,
    *,
    title: str,
    drive_service,
    docs_service,
) -> PushResult:
    """Create a new Doc and push the markdown's content into it."""
    md = md_path.read_text()
    local = parse_document_md(md)

    # 1. Create empty doc via Drive.
    created = drive_service.files().create(
        body={
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
        },
        fields="id",
    ).execute()
    doc_id = created["id"]

    # 2. Diff against an empty base.
    base = _empty_document()
    plan = diff(base, local)

    # 3. Apply.
    ack = apply_docs_api(plan, doc_id=doc_id, service=docs_service)

    return PushResult(doc_id=doc_id, plan=plan, ack=ack)


def push_force(
    md_path: Path,
    *,
    doc_id: str,
    docs_service,
) -> PushResult:
    """Force-push the local markdown to an existing doc, ignoring remote drift.

    Three-way merge against the remote is deferred. `--force` is the only
    mode shipped in the overnight build.
    """
    md = md_path.read_text()
    local = parse_document_md(md)

    # Fetch remote, build remote AST as the base.
    docs_payload = docs_service.documents().get(
        documentId=doc_id, includeTabsContent=True,
    ).execute()
    remote = build_document(docs_payload)

    plan = diff(remote, local)
    ack = apply_docs_api(plan, doc_id=doc_id, service=docs_service)
    return PushResult(doc_id=doc_id, plan=plan, ack=ack)


def push_merge(
    md_path: Path,
    *,
    doc_id: str,
    docs_service,
) -> PushResult:
    """Default push path: three-way merge between pull-time base, local, and remote.

    Looks for `<md_path>.pull-state.json` (written by `gdoc pull`). If
    present, uses its docs_json as the merge base. If absent, falls
    back to treating remote as the base — equivalent to `--force` for
    the no-state case, with the safety that any block local doesn't
    have but remote does will be preserved.

    On conflict, rewrites md_path with `.gd-conflict` divs and returns
    a PushResult whose `conflicts` is non-empty and `plan` is empty —
    the caller decides how to surface that to the user. No batchUpdate
    is sent in the conflict case.
    """
    md = md_path.read_text()
    local = parse_document_md(md)

    docs_payload = docs_service.documents().get(
        documentId=doc_id, includeTabsContent=True,
    ).execute()
    remote = build_document(docs_payload)

    base = _load_base_for_merge(md_path, fallback=remote)
    merged, conflicts = merge(base, local, remote)

    if conflicts:
        # Write the merged AST (which embeds Conflict nodes) back to md_path
        # so the user can resolve and re-push. Do NOT mutate the doc.
        md_path.write_text(emit_document_md(merged))
        return PushResult(
            doc_id=doc_id, plan=OpPlan(), ack={}, conflicts=conflicts,
        )

    # No conflicts — diff remote -> merged and apply.
    plan = diff(remote, merged)
    ack = apply_docs_api(plan, doc_id=doc_id, service=docs_service)
    return PushResult(doc_id=doc_id, plan=plan, ack=ack, conflicts=[])


def _load_base_for_merge(md_path: Path, *, fallback: Document) -> Document:
    """Read the `.pull-state.json` sidecar (if present) and rebuild its AST.

    Falls back to ``fallback`` if no sidecar exists — gives the merge
    something sensible to work with even for users who hand-wrote a md
    file that wasn't produced by `gdoc pull`.
    """
    state_path = md_path.with_suffix(md_path.suffix + ".pull-state.json")
    if not state_path.exists():
        return fallback
    raw = json.loads(state_path.read_text())
    docs_json = raw.get("docs_json") or {}
    return build_document(docs_json)


def push_dry_run(
    md_path: Path,
    *,
    doc_id: str | None = None,
    docs_service=None,
) -> OpPlan:
    """Compute the plan without applying. If `doc_id` is None, diffs against empty."""
    md = md_path.read_text()
    local = parse_document_md(md)
    if doc_id is None or docs_service is None:
        base = _empty_document()
    else:
        docs_payload = docs_service.documents().get(
            documentId=doc_id, includeTabsContent=True,
        ).execute()
        base = build_document(docs_payload)
    return diff(base, local)


def plan_to_json(plan: OpPlan) -> dict:
    """Serialize an OpPlan for `--plan-only` JSON output."""
    return {
        "ops": [_op_to_json(op) for op in plan],
        "summary": plan.summary(),
    }


def _op_to_json(op) -> dict:
    """Best-effort JSON-serializable view of a primitive."""
    out: dict = {"kind": type(op).__name__}
    for slot in (
        "block_id", "after_id", "offset", "text", "start", "end", "scope",
    ):
        if hasattr(op, slot):
            v = getattr(op, slot)
            if v is None or isinstance(v, (int, str, float, bool)):
                out[slot] = v
    # styles and embedded blocks: render via repr to keep types visible
    if hasattr(op, "run_style") and op.run_style is not None:
        out["run_style"] = _style_to_json(op.run_style)
    if hasattr(op, "style") and op.style is not None:
        out["style"] = _style_to_json(op.style)
    if hasattr(op, "block") and op.block is not None:
        out["block"] = repr(op.block)
    return out


def _style_to_json(s) -> dict:
    if hasattr(s, "__dict__"):
        return {k: v for k, v in s.__dict__.items() if v is not None}
    if isinstance(s, dict):
        return s
    return {"repr": repr(s)}


def _empty_document() -> Document:
    """A blank Document used as the base when creating from scratch."""
    from datetime import datetime
    return Document(
        doc_id="", title="", revision_id="", drive_url="",
        captured_at=datetime.now(tz=UTC),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="(default)", level=0, blocks=[])],
    )


# Pretty-printing for the CLI happy path.


def format_plan_summary(plan: OpPlan) -> str:
    counts = plan.summary()
    if not counts:
        return "  (no changes)"
    lines = []
    for kind in sorted(counts):
        lines.append(f"  {kind:<14} {counts[kind]}")
    return "\n".join(lines)


def write_plan_json(plan: OpPlan, out_path: Path) -> None:
    out_path.write_text(json.dumps(plan_to_json(plan), indent=2) + "\n")
