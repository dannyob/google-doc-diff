"""Tests for the three-way AST merge in merge/three_way.py."""
from __future__ import annotations

from datetime import UTC, datetime

from google_doc_diff.ast.nodes import (
    Conflict,
    Document,
    Heading,
    ListItem,
    Paragraph,
    Run,
    StyleDescriptor,
    Tab,
)
from google_doc_diff.merge.three_way import merge


def _doc(blocks) -> Document:
    return Document(
        doc_id="d", title="t", revision_id="r", drive_url="u",
        captured_at=datetime(2026, 5, 14, tzinfo=UTC),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="(default)", level=0, blocks=blocks)],
    )


def _p(text, pid):
    return Paragraph(runs=[Run(text=text)], paragraph_id=pid)


# --- the matrix from the design spec -------------------------------------


def test_no_change_either_side_yields_no_conflict():
    base = _doc([_p("hello", "p-1")])
    local = _doc([_p("hello", "p-1")])
    remote = _doc([_p("hello", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].text == "hello"


def test_local_only_change_takes_local():
    base = _doc([_p("hello", "p-1")])
    local = _doc([_p("hello there", "p-1")])
    remote = _doc([_p("hello", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].text == "hello there"


def test_remote_only_change_takes_remote():
    base = _doc([_p("hello", "p-1")])
    local = _doc([_p("hello", "p-1")])
    remote = _doc([_p("hello world", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].text == "hello world"


def test_same_change_both_sides_is_not_a_conflict():
    base = _doc([_p("hello", "p-1")])
    local = _doc([_p("hello world", "p-1")])
    remote = _doc([_p("hello world", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].text == "hello world"


def test_different_changes_same_block_is_conflict():
    base = _doc([_p("hello", "p-1")])
    local = _doc([_p("hello local", "p-1")])
    remote = _doc([_p("hello remote", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert isinstance(c, Conflict)
    assert c.conflict_id == "c-p-1"
    assert c.local_blocks[0].runs[0].text == "hello local"
    assert c.remote_blocks[0].runs[0].text == "hello remote"
    # The merged AST embeds the Conflict node at the block's position.
    assert isinstance(merged.tabs[0].blocks[0], Conflict)


def test_local_inserted_block_remote_unchanged_takes_local():
    base = _doc([_p("a", "p-1")])
    local = _doc([_p("a", "p-1"), _p("new", "p-2")])
    remote = _doc([_p("a", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["a", "new"]


def test_remote_inserted_block_local_unchanged_takes_remote():
    base = _doc([_p("a", "p-1")])
    local = _doc([_p("a", "p-1")])
    remote = _doc([_p("a", "p-1"), _p("new-r", "p-2")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["a", "new-r"]


def test_local_deleted_remote_unchanged_drops_block():
    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([_p("a", "p-1")])
    remote = _doc([_p("a", "p-1"), _p("b", "p-2")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["a"]


def test_local_deleted_remote_changed_is_conflict():
    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([_p("a", "p-1")])
    remote = _doc([_p("a", "p-1"), _p("b changed", "p-2")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.local_blocks == []
    assert c.remote_blocks[0].runs[0].text == "b changed"


def test_heading_change_uses_block_id():
    base = _doc([Heading(level=1, runs=[Run(text="X")], paragraph_id="h-1")])
    local = _doc([Heading(level=1, runs=[Run(text="X local")], paragraph_id="h-1")])
    remote = _doc([Heading(level=1, runs=[Run(text="X")], paragraph_id="h-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].text == "X local"


def test_empty_base_both_sides_have_same_blocks_no_conflict():
    """Create-from-scratch scenario: empty base, local and remote agree."""
    base = _doc([])
    local = _doc([_p("hello", "p-1")])
    remote = _doc([_p("hello", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].text == "hello"


def test_empty_base_both_sides_have_different_blocks_at_same_id():
    base = _doc([])
    local = _doc([_p("hello local", "p-1")])
    remote = _doc([_p("hello remote", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1


# --- edge cases -----------------------------------------------------------


def _li(text, pid, kind="bulleted"):
    return ListItem(
        level=0, kind=kind, list_id="L1",
        runs=[Run(text=text)], paragraph_id=pid,
    )


def test_both_sides_deleted_same_block_is_clean():
    """Both local and remote deleted p-2 — no conflict, just drop it."""
    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([_p("a", "p-1")])
    remote = _doc([_p("a", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["a"]


def test_remote_deleted_local_deleted_same_block_is_clean():
    """Symmetric: remote also deleted the block local deleted."""
    base = _doc([_p("a", "p-1"), _p("b", "p-2"), _p("c", "p-3")])
    local = _doc([_p("a", "p-1"), _p("c", "p-3")])
    remote = _doc([_p("a", "p-1"), _p("c", "p-3")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["a", "c"]


def test_both_sides_insert_different_blocks_no_conflict():
    """Local inserts p-2, remote inserts p-3 — both kept, no conflict."""
    base = _doc([_p("a", "p-1")])
    local = _doc([_p("a", "p-1"), _p("local new", "p-2")])
    remote = _doc([_p("a", "p-1"), _p("remote new", "p-3")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert "local new" in texts
    assert "remote new" in texts


def test_style_only_conflict():
    """Same text but local bolds it and remote italicizes — conflict."""
    base = _doc([Paragraph(runs=[Run(text="hello")], paragraph_id="p-1")])
    local = _doc([Paragraph(
        runs=[Run(text="hello", formatting=StyleDescriptor(bold=True))],
        paragraph_id="p-1",
    )])
    remote = _doc([Paragraph(
        runs=[Run(text="hello", formatting=StyleDescriptor(italic=True))],
        paragraph_id="p-1",
    )])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1


def test_style_only_local_change_takes_local():
    """Local bolds text, remote unchanged — take local."""
    base = _doc([Paragraph(runs=[Run(text="hello")], paragraph_id="p-1")])
    local = _doc([Paragraph(
        runs=[Run(text="hello", formatting=StyleDescriptor(bold=True))],
        paragraph_id="p-1",
    )])
    remote = _doc([Paragraph(runs=[Run(text="hello")], paragraph_id="p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].formatting.bold is True


def test_heading_level_change_is_a_change():
    """Local changes H1 to H2 (same text) — recognized as a change."""
    base = _doc([Heading(level=1, runs=[Run(text="Title")], paragraph_id="h-1")])
    local = _doc([Heading(level=2, runs=[Run(text="Title")], paragraph_id="h-1")])
    remote = _doc([Heading(level=1, runs=[Run(text="Title")], paragraph_id="h-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].level == 2


def test_heading_level_conflict():
    """Both sides change the heading level differently — conflict."""
    base = _doc([Heading(level=1, runs=[Run(text="T")], paragraph_id="h-1")])
    local = _doc([Heading(level=2, runs=[Run(text="T")], paragraph_id="h-1")])
    remote = _doc([Heading(level=3, runs=[Run(text="T")], paragraph_id="h-1")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1


def test_many_blocks_one_conflict_others_clean():
    """Only the divergent block conflicts; surrounding blocks merge cleanly."""
    base = _doc([_p("a", "p-1"), _p("b", "p-2"), _p("c", "p-3")])
    local = _doc([_p("a-local", "p-1"), _p("b-local", "p-2"), _p("c", "p-3")])
    remote = _doc([_p("a-local", "p-1"), _p("b-remote", "p-2"), _p("c", "p-3")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1
    assert conflicts[0].conflict_id == "c-p-2"
    blocks = merged.tabs[0].blocks
    assert blocks[0].runs[0].text == "a-local"  # same change both sides
    assert isinstance(blocks[1], Conflict)
    assert blocks[2].runs[0].text == "c"  # unchanged


def test_heading_anchor_id_fallback_used_when_no_paragraph_id():
    """Heading with anchor_id but no paragraph_id uses anchor_id as join key."""
    base = _doc([Heading(level=1, runs=[Run(text="X")], anchor_id="h-A")])
    local = _doc([Heading(level=1, runs=[Run(text="X edited")], anchor_id="h-A")])
    remote = _doc([Heading(level=1, runs=[Run(text="X")], anchor_id="h-A")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].text == "X edited"


def test_list_item_text_edit_merges_cleanly():
    """ListItem with paragraph_id: local edits text, remote unchanged — take local."""
    base = _doc([_li("apple", "li-1"), _li("banana", "li-2")])
    local = _doc([_li("apricot", "li-1"), _li("banana", "li-2")])
    remote = _doc([_li("apple", "li-1"), _li("banana", "li-2")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["apricot", "banana"]


def test_list_item_conflict_both_sides_edit():
    """Both sides edit the same list item — conflict."""
    base = _doc([_li("apple", "li-1")])
    local = _doc([_li("apricot", "li-1")])
    remote = _doc([_li("avocado", "li-1")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1


def test_list_item_delete_vs_edit_conflict():
    """Local deletes a list item, remote edits it — conflict."""
    base = _doc([_li("a", "li-1"), _li("b", "li-2")])
    local = _doc([_li("a", "li-1")])
    remote = _doc([_li("a", "li-1"), _li("b edited", "li-2")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1
    assert conflicts[0].remote_blocks[0].runs[0].text == "b edited"
    assert conflicts[0].local_blocks == []


def test_all_sides_empty_is_clean():
    """All three documents are empty — clean merge, no conflicts."""
    base = _doc([])
    local = _doc([])
    remote = _doc([])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks == []


def test_local_reorder_remote_unchanged():
    """Local swaps block order, remote unchanged — take local order."""
    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([_p("b", "p-2"), _p("a", "p-1")])
    remote = _doc([_p("a", "p-1"), _p("b", "p-2")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["b", "a"]


def test_remote_only_insert_preserved_in_merge():
    """Remote adds a block local doesn't have — kept in merge."""
    base = _doc([_p("a", "p-1")])
    local = _doc([_p("a", "p-1")])
    remote = _doc([_p("a", "p-1"), _p("remote added", "p-9")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert "remote added" in texts


def test_local_edit_plus_remote_insert():
    """Local edits existing block, remote inserts new block — both applied."""
    base = _doc([_p("original", "p-1")])
    local = _doc([_p("edited", "p-1")])
    remote = _doc([_p("original", "p-1"), _p("new block", "p-2")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["edited", "new block"]


def test_multi_run_paragraph_equality():
    """Paragraphs with multiple runs are compared run-by-run."""
    runs = [Run(text="hello "), Run(text="world", formatting=StyleDescriptor(bold=True))]
    base = _doc([Paragraph(runs=list(runs), paragraph_id="p-1")])
    local = _doc([Paragraph(runs=list(runs), paragraph_id="p-1")])
    remote = _doc([Paragraph(runs=list(runs), paragraph_id="p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []

    # Now change one run's formatting in local only.
    local_runs = [Run(text="hello "), Run(text="world", formatting=StyleDescriptor(italic=True))]
    local2 = _doc([Paragraph(runs=local_runs, paragraph_id="p-1")])
    merged2, conflicts2 = merge(base, local2, remote)
    assert conflicts2 == []
    assert merged2.tabs[0].blocks[0].runs[1].formatting.italic is True


def test_remote_deleted_local_unchanged_symmetric_case():
    """Remote deletes p-2, local keeps it unchanged → drop p-2.

    This is the reverse-perspective of test_local_deleted_remote_unchanged.
    The merge walks LOCAL order and p-2 is present there; when it looks
    for p-2 in remote and doesn't find it, it must check whether local
    changed p-2 from base. If local == base → drop. If local != base →
    conflict.
    """
    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([_p("a", "p-1"), _p("b", "p-2")])
    remote = _doc([_p("a", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["a"]


def test_local_changed_remote_deleted_is_conflict():
    """Local edits p-2 but remote deleted it — conflict (local side non-empty,
    remote side empty)."""
    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([_p("a", "p-1"), _p("b edited", "p-2")])
    remote = _doc([_p("a", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.local_blocks[0].runs[0].text == "b edited"
    assert c.remote_blocks == []


def test_three_way_with_five_blocks_complex_scenario():
    """Complex: local edits p-1, deletes p-3, remote edits p-2, deletes p-4,
    both keep p-5. Expected: p-1 local, p-2 remote, p-3 dropped (local delete
    + remote unchanged), p-4 dropped (remote delete + local unchanged), p-5
    unchanged."""
    base = _doc([
        _p("one", "p-1"), _p("two", "p-2"), _p("three", "p-3"),
        _p("four", "p-4"), _p("five", "p-5"),
    ])
    local = _doc([
        _p("one-L", "p-1"), _p("two", "p-2"),
        # p-3 deleted
        _p("four", "p-4"), _p("five", "p-5"),
    ])
    remote = _doc([
        _p("one", "p-1"), _p("two-R", "p-2"), _p("three", "p-3"),
        # p-4 deleted
        _p("five", "p-5"),
    ])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["one-L", "two-R", "five"]


def test_merge_output_emits_and_parses_cleanly():
    """The merged AST (even with conflicts) round-trips through emit/parse."""
    from google_doc_diff.emit.markdown import emit_document_md
    from google_doc_diff.parse.markdown import parse_document_md

    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([_p("a-local", "p-1"), _p("b-local", "p-2")])
    remote = _doc([_p("a-local", "p-1"), _p("b-remote", "p-2")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1

    md = emit_document_md(merged)
    reparsed = parse_document_md(md)
    rblocks = reparsed.tabs[0].blocks
    # First block is clean merge (same change both sides).
    assert rblocks[0].runs[0].text == "a-local"
    # Second block is a Conflict that survived the round-trip.
    confs = [b for b in rblocks if isinstance(b, Conflict)]
    assert len(confs) == 1
    assert "b-local" in confs[0].local_blocks[0].runs[0].text
    assert "b-remote" in confs[0].remote_blocks[0].runs[0].text


def test_merge_clean_then_diff_produces_minimal_ops():
    """End-to-end: merge with no conflicts, then diff the merged AST
    against the remote to verify the OpPlan is minimal (only local
    changes, no unnecessary rewrites)."""
    from google_doc_diff.ops import diff

    base = _doc([_p("hello", "p-1"), _p("world", "p-2")])
    local = _doc([_p("hello edited", "p-1"), _p("world", "p-2")])
    remote = _doc([_p("hello", "p-1"), _p("world", "p-2")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []

    plan = diff(remote, merged)
    kinds = {type(o).__name__ for o in plan}
    assert "InsertBlock" not in kinds
    assert "DeleteBlock" not in kinds
    assert kinds & {"InsertText", "DeleteRange"}


def test_remote_inserts_at_start_local_edits_existing():
    """Remote adds a block before p-1, local edits p-1's text. Both should
    appear in the merge."""
    base = _doc([_p("existing", "p-1")])
    local = _doc([_p("existing edited", "p-1")])
    remote = _doc([_p("prepended by remote", "p-0"), _p("existing", "p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert "existing edited" in texts
    assert "prepended by remote" in texts


def test_list_item_kind_change_is_detected():
    """Changing bulleted -> ordered (same text) is a change."""
    base = _doc([_li("item", "li-1", kind="bulleted")])
    local = _doc([_li("item", "li-1", kind="ordered")])
    remote = _doc([_li("item", "li-1", kind="bulleted")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].kind == "ordered"


def test_merge_preserves_heading_anchor_through_conflict():
    """Conflict on a Heading must preserve anchor_id on both sides."""
    base = _doc([Heading(level=1, runs=[Run(text="X")], anchor_id="h-A", paragraph_id="h-1")])
    local = _doc([Heading(level=1, runs=[Run(text="X-local")], anchor_id="h-A", paragraph_id="h-1")])
    remote = _doc([Heading(level=1, runs=[Run(text="X-remote")], anchor_id="h-A", paragraph_id="h-1")])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.local_blocks[0].anchor_id == "h-A"
    assert c.remote_blocks[0].anchor_id == "h-A"


def test_same_style_change_both_sides_no_conflict():
    """Both sides apply the same bold — identical runs, no conflict."""
    fmt = StyleDescriptor(bold=True)
    base = _doc([Paragraph(runs=[Run(text="x")], paragraph_id="p-1")])
    local = _doc([Paragraph(runs=[Run(text="x", formatting=fmt)], paragraph_id="p-1")])
    remote = _doc([Paragraph(runs=[Run(text="x", formatting=fmt)], paragraph_id="p-1")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks[0].runs[0].formatting.bold is True


def test_interleaved_inserts_both_sides():
    """Local inserts after p-1, remote also inserts after p-1 (different IDs).
    Both should appear in merge without conflict."""
    base = _doc([_p("a", "p-1"), _p("b", "p-5")])
    local = _doc([_p("a", "p-1"), _p("local-new", "p-2"), _p("b", "p-5")])
    remote = _doc([_p("a", "p-1"), _p("remote-new", "p-3"), _p("b", "p-5")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert "a" in texts
    assert "b" in texts
    assert "local-new" in texts
    assert "remote-new" in texts


def test_delete_all_blocks_both_sides():
    """Both sides delete everything — clean empty merge."""
    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([])
    remote = _doc([])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    assert merged.tabs[0].blocks == []


def test_local_deletes_all_remote_edits_one_is_conflict():
    """Local deletes all blocks; remote edits one — conflict for the edited one."""
    base = _doc([_p("a", "p-1"), _p("b", "p-2")])
    local = _doc([])
    remote = _doc([_p("a", "p-1"), _p("b edited", "p-2")])
    merged, conflicts = merge(base, local, remote)
    # p-1: local deleted, remote unchanged → drop
    # p-2: local deleted, remote changed → conflict
    assert len(conflicts) == 1
    assert conflicts[0].remote_blocks[0].runs[0].text == "b edited"
    assert conflicts[0].local_blocks == []


def test_full_pipeline_merge_then_diff_translate():
    """End-to-end: merge → diff → translate. Verifies that the ops from a
    cleanly merged AST translate into valid Docs API requests where
    insert indices don't collide or go negative."""
    from google_doc_diff.apply.docs_api import translate
    from google_doc_diff.ops import diff

    base = _doc([_p("alpha", "p-1"), _p("beta", "p-2"), _p("gamma", "p-3")])
    local = _doc([_p("alpha edited", "p-1"), _p("beta", "p-2"), _p("gamma", "p-3")])
    remote = _doc([_p("alpha", "p-1"), _p("beta", "p-2"), _p("gamma updated", "p-3")])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    texts = [b.runs[0].text for b in merged.tabs[0].blocks]
    assert texts == ["alpha edited", "beta", "gamma updated"]

    plan = diff(remote, merged)
    # Only p-1 changed (remote→merged); p-2 and p-3 are already in remote.
    block_index = {"p-1": (1, 7), "p-2": (7, 12), "p-3": (12, 27)}
    reqs = translate(plan, block_index=block_index, end_of_body=27)
    # All requests should have valid (positive) indices.
    for r in reqs:
        if "insertText" in r:
            assert r["insertText"]["location"]["index"] >= 1
        if "deleteContentRange" in r:
            rng = r["deleteContentRange"]["range"]
            assert rng["startIndex"] >= 1
            assert rng["endIndex"] > rng["startIndex"]


def test_emit_parse_round_trip_of_merged_conflicts_and_clean():
    """A doc with a mix of clean blocks and conflict blocks round-trips
    through emit + parse without losing or duplicating anything."""
    from google_doc_diff.emit.markdown import emit_document_md
    from google_doc_diff.parse.markdown import parse_document_md

    base = _doc([
        _p("clean", "p-1"),
        _p("contested", "p-2"),
        _p("also clean", "p-3"),
    ])
    local = _doc([
        _p("clean", "p-1"),
        _p("contested-local", "p-2"),
        _p("also clean", "p-3"),
    ])
    remote = _doc([
        _p("clean", "p-1"),
        _p("contested-remote", "p-2"),
        _p("also clean", "p-3"),
    ])
    merged, conflicts = merge(base, local, remote)
    assert len(conflicts) == 1

    md = emit_document_md(merged)
    reparsed = parse_document_md(md)
    rblocks = reparsed.tabs[0].blocks

    # Should have exactly 3 top-level blocks: clean, Conflict, clean.
    non_empty = [b for b in rblocks if not (isinstance(b, Paragraph)
                 and not b.runs)]
    assert len(non_empty) == 3
    assert isinstance(non_empty[0], Paragraph)
    assert isinstance(non_empty[1], Conflict)
    assert isinstance(non_empty[2], Paragraph)
    assert non_empty[0].runs[0].text == "clean"
    assert non_empty[2].runs[0].text == "also clean"


def test_mixed_identified_and_anonymous_blocks():
    """Anonymous blocks (no paragraph_id) pass through from local side."""
    base = _doc([_p("a", "p-1"), Paragraph(runs=[Run(text="anon")])])
    local = _doc([_p("a edited", "p-1"), Paragraph(runs=[Run(text="anon")])])
    remote = _doc([_p("a", "p-1"), Paragraph(runs=[Run(text="anon")])])
    merged, conflicts = merge(base, local, remote)
    assert conflicts == []
    blocks = merged.tabs[0].blocks
    assert blocks[0].runs[0].text == "a edited"
    assert blocks[1].runs[0].text == "anon"
