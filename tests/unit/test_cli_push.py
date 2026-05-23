"""Tests for cli_push — the gdoc push runners (against fake services)."""
from __future__ import annotations

from pathlib import Path

from google_doc_diff.cli_push import (
    PushResult,
    plan_to_json,
    push_dry_run,
    push_force,
    push_merge,
    push_new,
    write_plan_json,
)

# --- Fakes ---------------------------------------------------------------


class _FakeDrive:
    """Stand-in for the Drive v3 client used in push_new."""

    def __init__(self, new_id="new-doc-id"):
        self.new_id = new_id
        self.created: list[dict] = []

    def files(self):
        return self

    def create(self, *, body, fields=None):
        self.created.append(body)
        return _Wrap({"id": self.new_id})


class _FakeDocs:
    """Stand-in for Docs v1 client used by apply."""

    def __init__(self, doc=None):
        self._doc = doc or {
            "body": {"content": [{"startIndex": 1, "endIndex": 2, "paragraph": {}}]},
            "documentId": "abc",
        }
        self.last_requests: list[dict] = []
        self.batches: list[dict] = []

    def documents(self):
        return self

    def get(self, documentId=None, includeTabsContent=None):
        return _Wrap(self._doc)

    def batchUpdate(self, *, documentId, body):
        self.last_requests = body["requests"]
        self.batches.append({"documentId": documentId, "requests": body["requests"]})
        return _Wrap({"documentId": documentId, "replies": []})


class _Wrap:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


# --- push_new ------------------------------------------------------------


def _write_minimal_md(tmp_path: Path) -> Path:
    p = tmp_path / "doc.md"
    p.write_text(
        "---\n"
        "title: T\n"
        "doc_id: ''\n"
        "revision_id: ''\n"
        "drive_url: ''\n"
        "captured_at: '2026-05-14T00:00:00+00:00'\n"
        "schema_version: 1\n"
        "last_modifying_user: null\n"
        "source_mode: pull\n"
        "comments_preserved: true\n"
        "suggestions_preserved: true\n"
        "---\n"
        "\n# Hello\n\nWorld.\n"
    )
    return p


def test_push_new_creates_doc_and_applies_ops(tmp_path):
    md = _write_minimal_md(tmp_path)
    drive = _FakeDrive(new_id="abc-123")
    docs = _FakeDocs(doc={
        "documentId": "abc-123",
        "body": {"content": [{"startIndex": 1, "endIndex": 2, "paragraph": {}}]},
    })
    result = push_new(md, title="Test", drive_service=drive, docs_service=docs)
    assert isinstance(result, PushResult)
    assert result.doc_id == "abc-123"
    assert drive.created == [
        {"name": "Test", "mimeType": "application/vnd.google-apps.document"},
    ]
    # Some batchUpdate request was sent.
    assert docs.last_requests
    # Should contain at least one insertText.
    assert any("insertText" in r for r in docs.last_requests)


def test_push_new_emits_paragraph_style_for_headings(tmp_path):
    md = _write_minimal_md(tmp_path)
    drive = _FakeDrive()
    docs = _FakeDocs()
    push_new(md, title="T", drive_service=drive, docs_service=docs)
    # Heading "Hello" should trigger updateParagraphStyle: HEADING_1.
    style_req = next(
        (r for r in docs.last_requests if "updateParagraphStyle" in r), None,
    )
    assert style_req is not None
    assert style_req["updateParagraphStyle"]["paragraphStyle"] == {
        "namedStyleType": "HEADING_1",
    }


# --- push_force ----------------------------------------------------------


def test_push_force_applies_against_existing(tmp_path):
    md = _write_minimal_md(tmp_path)
    # Make the fake docs payload pretend to be empty.
    docs = _FakeDocs(doc={
        "documentId": "existing",
        "title": "Existing",
        "body": {"content": [{"startIndex": 1, "endIndex": 2, "paragraph": {
            "elements": [],
        }}]},
        "revisionId": "rev1",
    })
    result = push_force(md, doc_id="existing", docs_service=docs)
    assert result.doc_id == "existing"
    assert docs.batches
    assert docs.batches[0]["documentId"] == "existing"


# --- push_merge ----------------------------------------------------------


def _docs_payload_with_single_heading(text="Hello", revision_id="rev1"):
    """A minimal Docs API payload representing a single H1 paragraph.

    Hand-crafted so build_document produces a Paragraph/Heading with the
    right shape — used as both the remote and (when written to the
    sidecar) the base for push_merge tests.
    """
    return {
        "documentId": "merge-doc",
        "title": "Merge Doc",
        "revisionId": revision_id,
        "namedStyles": {"styles": [{"namedStyleType": "HEADING_1"}]},
        "body": {"content": [
            {"startIndex": 1, "endIndex": 7, "paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_1"},
                "elements": [{"textRun": {"content": text + "\n"}}],
            }},
            {"startIndex": 7, "endIndex": 8, "paragraph": {
                "elements": [{"textRun": {"content": "\n"}}],
            }},
        ]},
    }


def _write_pulled_md(tmp_path: Path, md: str, docs_json: dict) -> Path:
    """Write a md + sidecar pair (the shape `gdoc pull` produces)."""
    import json
    p = tmp_path / "merged.md"
    p.write_text(md)
    state = tmp_path / "merged.md.pull-state.json"
    state.write_text(json.dumps({
        "doc_id": docs_json["documentId"],
        "revision_id": docs_json.get("revisionId", ""),
        "docs_json": docs_json,
    }))
    return p


def test_push_merge_no_remote_changes_applies_local_edits(tmp_path):
    """Local edited, remote unchanged — merge takes local, applies the diff."""
    payload = _docs_payload_with_single_heading(text="Hello")
    # Local md mirrors what `gdoc pull` would produce, then edited.
    md = (
        "---\n"
        "title: Merge Doc\n"
        "doc_id: merge-doc\n"
        "revision_id: rev1\n"
        "drive_url: ''\n"
        "captured_at: '2026-05-14T00:00:00+00:00'\n"
        "schema_version: 1\n"
        "last_modifying_user: null\n"
        "source_mode: pull\n"
        "comments_preserved: true\n"
        "suggestions_preserved: true\n"
        "---\n"
        "\n# Hello edited {#p-0-0}\n"
    )
    md_path = _write_pulled_md(tmp_path, md, payload)
    docs = _FakeDocs(doc=payload)
    result = push_merge(md_path, doc_id="merge-doc", docs_service=docs)
    assert result.conflicts == []
    assert docs.batches  # apply called
    # Verify the request mentions the edited text.
    text_inserts = [r for r in docs.last_requests if "insertText" in r]
    assert any("edited" in r["insertText"]["text"] for r in text_inserts)


def test_push_merge_with_conflict_writes_markers_and_skips_apply(tmp_path):
    """Both sides edited the same block — push_merge writes markers, doesn't apply."""
    base_payload = _docs_payload_with_single_heading(text="Hello", revision_id="rev1")
    remote_payload = _docs_payload_with_single_heading(
        text="Hello from remote", revision_id="rev2",
    )
    md = (
        "---\n"
        "title: Merge Doc\n"
        "doc_id: merge-doc\n"
        "revision_id: rev1\n"
        "drive_url: ''\n"
        "captured_at: '2026-05-14T00:00:00+00:00'\n"
        "schema_version: 1\n"
        "last_modifying_user: null\n"
        "source_mode: pull\n"
        "comments_preserved: true\n"
        "suggestions_preserved: true\n"
        "---\n"
        "\n# Hello from local {#p-0-0}\n"
    )
    md_path = _write_pulled_md(tmp_path, md, base_payload)
    docs = _FakeDocs(doc=remote_payload)
    result = push_merge(md_path, doc_id="merge-doc", docs_service=docs)
    assert len(result.conflicts) == 1
    assert docs.batches == []  # no apply
    rewritten = md_path.read_text()
    assert ".gd-conflict" in rewritten
    assert "Hello from local" in rewritten
    assert "Hello from remote" in rewritten


# --- push_dry_run --------------------------------------------------------


def test_push_dry_run_does_not_call_batch_update(tmp_path):
    md = _write_minimal_md(tmp_path)
    plan = push_dry_run(md)  # no doc_id => diff against empty
    assert plan.summary()  # at least one op
    # No services were passed; no calls to make. Just confirm structure.


# --- JSON serialization --------------------------------------------------


def test_plan_to_json_has_ops_and_summary(tmp_path):
    md = _write_minimal_md(tmp_path)
    plan = push_dry_run(md)
    out = plan_to_json(plan)
    assert "ops" in out and "summary" in out
    assert isinstance(out["ops"], list)
    assert out["summary"]


def test_write_plan_json_round_trip(tmp_path):
    md = _write_minimal_md(tmp_path)
    plan = push_dry_run(md)
    out_path = tmp_path / "plan.json"
    write_plan_json(plan, out_path)
    assert out_path.exists()
    import json
    body = json.loads(out_path.read_text())
    assert body["summary"]
