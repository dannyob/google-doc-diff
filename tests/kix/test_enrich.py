"""Tests for kix.enrich — post-processing AST enrichment from OT ops."""

from datetime import UTC, datetime

from google_doc_diff.ast.nodes import (
    Comment,
    Document,
    Paragraph,
    Run,
    SmartChip,
    Suggestion,
    Tab,
    VotingChip,
)
from google_doc_diff.kix.enrich import build_kix_anchor_map, enrich_from_kix
from google_doc_diff.kix.model import KixModel


def _make_doc(
    *,
    tabs=None,
    suggestions=None,
    comments=None,
) -> Document:
    """Build a minimal Document for testing."""
    return Document(
        doc_id="test-doc",
        title="Test",
        revision_id="r1",
        drive_url="https://docs.google.com/document/d/test-doc/edit",
        captured_at=datetime.now(UTC),
        schema_version=1,
        last_modifying_user=None,
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=True,
        tabs=tabs or [Tab(tab_id="t.0", title="Tab 1", level=0, blocks=[])],
        suggestions=suggestions or {},
        comments=comments or {},
    )


def _make_model(ops, *, revision=1, model_version=1, suggestion_colors=None) -> KixModel:
    return KixModel(
        ops=ops,
        revision=revision,
        model_version=model_version,
        suggestion_colors=suggestion_colors or {},
    )


class TestSuggestionColors:
    def test_patches_color_onto_matching_suggestion(self):
        doc = _make_doc(
            suggestions={
                "suggest.abc123": Suggestion(
                    suggestion_id="suggest.abc123",
                    author="user@example.com",
                    created_time=datetime.now(UTC),
                    kind="insertion",
                ),
            }
        )
        ops = [
            {"ty": "is", "ibi": 1, "s": "hello"},
            {"ty": "iss", "sugid": "suggest.abc123", "ibi": 5, "s": " world"},
        ]
        model = _make_model(ops, suggestion_colors={"suggest.abc123": "#ff9900"})

        enrich_from_kix(doc, model)
        assert doc.suggestions["suggest.abc123"].color == "#ff9900"

    def test_ignores_unknown_suggestion_ids(self):
        doc = _make_doc(
            suggestions={
                "suggest.abc123": Suggestion(
                    suggestion_id="suggest.abc123",
                    author="user@example.com",
                    created_time=datetime.now(UTC),
                    kind="insertion",
                ),
            }
        )
        model = _make_model([], suggestion_colors={"suggest.unknown": "#00ff00"})

        enrich_from_kix(doc, model)
        assert doc.suggestions["suggest.abc123"].color is None

    def test_no_suggestion_colors_is_noop(self):
        doc = _make_doc()
        model = _make_model([])
        enrich_from_kix(doc, model)

    def test_multiple_suggestions_each_get_color(self):
        doc = _make_doc(
            suggestions={
                "suggest.a": Suggestion(
                    suggestion_id="suggest.a",
                    author="a@x.com",
                    created_time=datetime.now(UTC),
                    kind="insertion",
                ),
                "suggest.b": Suggestion(
                    suggestion_id="suggest.b",
                    author="b@x.com",
                    created_time=datetime.now(UTC),
                    kind="deletion",
                ),
            }
        )
        model = _make_model(
            [],
            suggestion_colors={
                "suggest.a": "#ff0000",
                "suggest.b": "#00ff00",
            },
        )
        enrich_from_kix(doc, model)
        assert doc.suggestions["suggest.a"].color == "#ff0000"
        assert doc.suggestions["suggest.b"].color == "#00ff00"


class TestBuildKixAnchorMap:
    def test_maps_te_ops_to_byte_offsets(self):
        ops = [
            {"ty": "is", "ibi": 0, "s": "First paragraph text. "},
            {"ty": "is", "ibi": 22, "s": "Second paragraph text."},
            {"ty": "te", "id": "kix.abc123", "spi": 5},
            {"ty": "te", "id": "kix.def456", "spi": 25},
        ]
        anchor_map = build_kix_anchor_map(ops)
        assert anchor_map["kix.abc123"] == 5
        assert anchor_map["kix.def456"] == 25

    def test_empty_ops(self):
        assert build_kix_anchor_map([]) == {}


class TestCommentAnchorEnrichment:
    def test_enrichment_uses_kix_resolver(self):
        tab = Tab(
            tab_id="t.0",
            title="Tab 1",
            level=0,
            blocks=[
                Paragraph(runs=[Run(text="Hello world")]),
                Paragraph(runs=[Run(text="Goodbye world")]),
            ],
        )
        doc = _make_doc(
            tabs=[tab],
            comments={
                "c1": Comment(
                    comment_id="c1",
                    author="a@x.com",
                    created_time=datetime.now(UTC),
                    modified_time=datetime.now(UTC),
                    content="Nice!",
                    quoted_text="Hello",
                    anchor="kix.anchor1",
                ),
            },
        )
        # te op places kix.anchor1 at spi=0, which is in block 0
        # block 0 has "Hello world" (11 chars + 1 newline = offset 0..11)
        ops = [
            {"ty": "is", "ibi": 0, "s": "Hello world\nGoodbye world"},
            {"ty": "te", "id": "kix.anchor1", "spi": 0},
        ]
        model = _make_model(ops)

        enrich_from_kix(doc, model)
        # The comment should have been anchored (not orphaned)
        assert not doc.comments["c1"].orphaned


class TestVotingChipEnrichment:
    def test_enriches_smartchip_with_voting_data(self):
        tab = Tab(
            tab_id="t.0",
            title="Tab 1",
            level=0,
            blocks=[
                Paragraph(
                    runs=[
                        Run(text="Vote here: "),
                        SmartChip(
                            kind="voting", data={"rendered": "(➕ 2)"}, display_text="(➕ 2)"
                        ),
                    ]
                ),
            ],
        )
        doc = _make_doc(tabs=[tab])
        ops = [
            {"ty": "is", "ibi": 0, "s": "Vote here: "},
            {"ty": "ae", "et": "emoji-voting", "id": "kix.chip1", "epm": {}},
            {"ty": "te", "id": "kix.chip1", "spi": 11},
            {
                "ty": "nm",
                "nmr": ["dtvc", "kix.chip1", False],
                "nmc": [
                    "voting-chip-populate",
                    "➕",
                    [{"ui": {"ui_oi": "voter1"}}, {"ui": {"ui_oi": "voter2"}}],
                    True,
                    "sig123",
                ],
            },
        ]
        model = _make_model(ops)

        result = enrich_from_kix(doc, model)
        assert result.voting_chips_enriched == 1

        block = doc.tabs[0].blocks[0]
        chip = block.runs[1]
        assert isinstance(chip, VotingChip)
        assert chip.emoji == "➕"
        assert len(chip.voters) == 2
        assert chip.voters[0].obfuscated_id == "voter1"
        assert chip.current_user_voted is True
        assert chip.signature == "sig123"

    def test_no_voting_ops_is_noop(self):
        tab = Tab(
            tab_id="t.0",
            title="Tab 1",
            level=0,
            blocks=[Paragraph(runs=[Run(text="no chips")])],
        )
        doc = _make_doc(tabs=[tab])
        model = _make_model([])

        result = enrich_from_kix(doc, model)
        assert result.voting_chips_enriched == 0

    def test_multiple_chips_in_same_paragraph(self):
        tab = Tab(
            tab_id="t.0",
            title="Tab 1",
            level=0,
            blocks=[
                Paragraph(
                    runs=[
                        SmartChip(kind="voting", data={}, display_text="(👍 1)"),
                        Run(text=" and "),
                        SmartChip(kind="voting", data={}, display_text="(🚀 3)"),
                    ]
                ),
            ],
        )
        doc = _make_doc(tabs=[tab])
        ops = [
            {"ty": "is", "ibi": 0, "s": " and "},
            {"ty": "ae", "et": "emoji-voting", "id": "kix.c1", "epm": {}},
            {"ty": "te", "id": "kix.c1", "spi": 0},
            {
                "ty": "nm",
                "nmr": ["dtvc", "kix.c1", False],
                "nmc": ["voting-chip-populate", "👍", [{"ui": {"ui_oi": "v1"}}], False, "s1"],
            },
            {"ty": "ae", "et": "emoji-voting", "id": "kix.c2", "epm": {}},
            {"ty": "te", "id": "kix.c2", "spi": 6},
            {
                "ty": "nm",
                "nmr": ["dtvc", "kix.c2", False],
                "nmc": [
                    "voting-chip-populate",
                    "🚀",
                    [{"ui": {"ui_oi": "v2"}}, {"ui": {"ui_oi": "v3"}}, {"ui": {"ui_oi": "v4"}}],
                    True,
                    "s2",
                ],
            },
        ]
        model = _make_model(ops)

        result = enrich_from_kix(doc, model)
        assert result.voting_chips_enriched == 2
