"""Tests for the three-way AST merge in merge/three_way.py."""
from __future__ import annotations

from datetime import UTC, datetime

from google_doc_diff.ast.nodes import (
    Conflict,
    Document,
    Heading,
    Paragraph,
    Run,
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
