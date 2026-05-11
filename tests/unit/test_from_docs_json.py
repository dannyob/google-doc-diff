"""Tests for ast/from_docs_json.py.

Uses small handcrafted Docs API JSON snippets (the shape of real responses,
trimmed). These exercise the walker without needing a live API.
"""

from datetime import UTC, datetime

from google_doc_diff.ast.from_docs_json import build_document
from google_doc_diff.ast.nodes import (
    Heading,
    ListItem,
    Paragraph,
    SuggestionDel,
    SuggestionIns,
    Table,
)


def _docs_json_minimal():
    return {
        "documentId": "DOCID",
        "title": "Test Doc",
        "revisionId": "rev1",
        "namedStyles": {"styles": [
            {
                "namedStyleType": "HEADING_1",
                "textStyle": {"bold": True, "fontSize": {"magnitude": 20.0}},
            }
        ]},
        "body": {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_1", "headingId": "HEAD1"},
                "elements": [{"textRun": {"content": "Hello\n"}}],
            }},
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": "World\n"}}],
            }},
        ]},
    }


def test_build_simple_doc_with_heading_and_paragraph():
    doc = build_document(_docs_json_minimal())
    assert doc.doc_id == "DOCID"
    assert doc.title == "Test Doc"
    assert doc.revision_id == "rev1"
    assert doc.source_mode == "pull"
    assert len(doc.tabs) == 1
    blocks = doc.tabs[0].blocks
    assert len(blocks) == 2
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 1
    assert blocks[0].anchor_id == "h-HEAD1"
    assert blocks[0].runs[0].text == "Hello"
    assert isinstance(blocks[1], Paragraph)
    assert blocks[1].runs[0].text == "World"


def test_named_styles_extracted_to_doc():
    doc = build_document(_docs_json_minimal())
    assert "HEADING_1" in doc.named_styles
    assert doc.named_styles["HEADING_1"]["bold"] is True
    assert doc.named_styles["HEADING_1"]["font_size_pt"] == 20.0


def test_title_paragraph_emits_with_title_class():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "TITLE"},
                "elements": [{"textRun": {"content": "Big Title\n"}}],
            }},
        ]},
    }
    doc = build_document(j)
    h = doc.tabs[0].blocks[0]
    assert isinstance(h, Heading)
    assert h.level == 1
    assert "gd-title" in h.classes


def test_subtitle_paragraph_emits_with_subtitle_class():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "SUBTITLE"},
                "elements": [{"textRun": {"content": "Sub\n"}}],
            }},
        ]},
    }
    doc = build_document(j)
    p = doc.tabs[0].blocks[0]
    assert isinstance(p, Paragraph)
    assert "gd-subtitle" in p.classes


def test_bulleted_list_item_with_nesting_level():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "lists": {"L1": {"listProperties": {"nestingLevels": [
            {"glyphType": "GLYPH_TYPE_UNSPECIFIED", "glyphSymbol": "●"},
        ]}}},
        "body": {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "bullet": {"listId": "L1", "nestingLevel": 0},
                "elements": [{"textRun": {"content": "item\n"}}],
            }},
        ]},
    }
    doc = build_document(j)
    item = doc.tabs[0].blocks[0]
    assert isinstance(item, ListItem)
    assert item.kind == "bulleted"
    assert item.level == 0
    assert item.list_id == "L1"


def test_ordered_list_via_glyph_type_decimal():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "lists": {"L1": {"listProperties": {"nestingLevels": [
            {"glyphType": "DECIMAL"},
        ]}}},
        "body": {"content": [
            {"paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "bullet": {"listId": "L1", "nestingLevel": 0},
                "elements": [{"textRun": {"content": "first\n"}}],
            }},
        ]},
    }
    doc = build_document(j)
    assert doc.tabs[0].blocks[0].kind == "ordered"


def test_table_with_colspan_rowspan():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [{"table": {
            "tableRows": [{"tableCells": [
                {"tableCellStyle": {"columnSpan": 2}, "content": [{"paragraph": {
                    "elements": [{"textRun": {"content": "merged\n"}}],
                }}]},
            ]}],
        }}]},
    }
    doc = build_document(j)
    t = doc.tabs[0].blocks[0]
    assert isinstance(t, Table)
    assert t.rows[0].cells[0].colspan == 2


def test_text_run_inline_overrides_register_css_class():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [{"paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [{"textRun": {
                "content": "fancy\n",
                "textStyle": {
                    "weightedFontFamily": {"fontFamily": "Source Code Pro"},
                    "foregroundColor": {"color": {"rgbColor": {"red": 1.0}}},
                },
            }}],
        }}]},
    }
    doc = build_document(j)
    assert any(k.startswith("gd-style-") for k in doc.css_classes)
    # The descriptor body should mention font-family + color
    body = next(v for k, v in doc.css_classes.items() if k.startswith("gd-style-"))
    assert "Source Code Pro" in body
    assert "#FF0000" in body


def test_comments_built_into_document():
    j = _docs_json_minimal()
    comments = [
        {
            "id": "AAA1",
            "createdTime": "2026-05-01T12:00:00.000Z",
            "modifiedTime": "2026-05-01T12:00:00.000Z",
            "author": {"emailAddress": "alice@example.com"},
            "content": "needs work",
            "quotedFileContent": {"value": "phrase"},
            "resolved": False,
            "deleted": False,
            "replies": [
                {
                    "id": "R1",
                    "createdTime": "2026-05-02T12:00:00.000Z",
                    "modifiedTime": "2026-05-02T12:00:00.000Z",
                    "author": {"emailAddress": "bob@example.com"},
                    "content": "agreed",
                    "action": "resolve",
                },
            ],
        },
    ]
    doc = build_document(j, comments_json=comments)
    assert "c-AAA1" in doc.comments
    cmt = doc.comments["c-AAA1"]
    assert cmt.author == "alice@example.com"
    assert cmt.quoted_text == "phrase"
    assert len(cmt.replies) == 1
    assert cmt.replies[0].action == "resolve"
    assert cmt.replies[0].author == "bob@example.com"


def test_suggestion_insertion_wraps_run():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [{"paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"textRun": {"content": "kept "}},
                {"textRun": {
                    "content": "added",
                    "suggestedInsertionIds": ["SUG1"],
                }},
                {"textRun": {"content": " more\n"}},
            ],
        }}]},
    }
    doc = build_document(j)
    runs = doc.tabs[0].blocks[0].runs
    assert any(isinstance(r, SuggestionIns) and r.suggestion_id == "SUG1" for r in runs)
    assert "SUG1" in doc.suggestions
    assert doc.suggestions["SUG1"].kind == "insertion"


def test_suggestion_deletion_wraps_run():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [{"paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {"textRun": {
                    "content": "removed",
                    "suggestedDeletionIds": ["SUG2"],
                }},
            ],
        }}]},
    }
    doc = build_document(j)
    assert isinstance(doc.tabs[0].blocks[0].runs[0], SuggestionDel)
    assert doc.suggestions["SUG2"].kind == "deletion"


def test_multi_tab_structure():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "tabs": [
            {
                "tabProperties": {"tabId": "T1", "title": "First"},
                "documentTab": {"body": {"content": [{"paragraph": {
                    "elements": [{"textRun": {"content": "tab one\n"}}],
                }}]}},
            },
            {
                "tabProperties": {"tabId": "T2", "title": "Second"},
                "documentTab": {"body": {"content": [{"paragraph": {
                    "elements": [{"textRun": {"content": "tab two\n"}}],
                }}]}},
                "childTabs": [
                    {
                        "tabProperties": {"tabId": "T2A", "title": "Child A"},
                        "documentTab": {"body": {"content": [{"paragraph": {
                            "elements": [{"textRun": {"content": "child\n"}}],
                        }}]}},
                    },
                ],
            },
        ],
    }
    doc = build_document(j)
    assert len(doc.tabs) == 2
    assert doc.tabs[0].tab_id == "t-T1"
    assert doc.tabs[1].tab_id == "t-T2"
    assert doc.tabs[1].children[0].tab_id == "t-T2A"
    assert doc.tabs[1].children[0].level == 1


def test_drive_url_computed_when_missing():
    doc = build_document(_docs_json_minimal())
    assert doc.drive_url == "https://docs.google.com/document/d/DOCID/edit"


def test_date_chip_extracted_with_display_text():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [{"paragraph": {
            "elements": [
                {"textRun": {"content": "Meeting: "}},
                {"dateElement": {
                    "dateId": "kix.dt1",
                    "dateElementProperties": {
                        "timestamp": "2026-04-30T12:00:00Z",
                        "locale": "en",
                        "dateFormat": "DATE_FORMAT_MONTH_DAY_YEAR_ABBREVIATED",
                        "timeFormat": "TIME_FORMAT_DISABLED",
                        "displayText": "Apr 30, 2026",
                    }}},
            ],
        }}]},
    }
    doc = build_document(j)
    runs = doc.tabs[0].blocks[0].runs
    chip = next(r for r in runs if hasattr(r, "kind") and r.kind == "date")
    assert chip.display_text == "Apr 30, 2026"
    assert chip.data["timestamp"] == "2026-04-30T12:00:00Z"
    assert chip.data["date_id"] == "kix.dt1"


def test_rich_link_classified_by_mime_type():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [{"paragraph": {
            "elements": [
                {"richLink": {
                    "richLinkId": "kix.rl1",
                    "richLinkProperties": {
                        "title": "My Folder",
                        "uri": "https://drive.google.com/drive/u/0/folders/abc",
                        "mimeType": "application/vnd.google-apps.folder",
                    }}},
                {"richLink": {
                    "richLinkId": "kix.rl2",
                    "richLinkProperties": {
                        "title": "Sheet",
                        "uri": "https://docs.google.com/spreadsheets/d/x",
                        "mimeType": "application/vnd.google-apps.spreadsheet",
                    }}},
                {"richLink": {
                    "richLinkId": "kix.rl3",
                    "richLinkProperties": {
                        "title": "Doc",
                        "uri": "https://docs.google.com/document/d/y",
                        "mimeType": "application/vnd.google-apps.kix",
                    }}},
            ],
        }}]},
    }
    doc = build_document(j)
    runs = doc.tabs[0].blocks[0].runs
    kinds = [r.kind for r in runs if hasattr(r, "kind")]
    assert "richlink-folder" in kinds
    assert "richlink-spreadsheet" in kinds
    assert "richlink-doc" in kinds


def test_unknown_rich_link_mime_falls_back_to_generic_kind():
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "body": {"content": [{"paragraph": {
            "elements": [{"richLink": {
                "richLinkId": "kix.rl",
                "richLinkProperties": {
                    "title": "Mystery",
                    "uri": "https://example.com",
                    "mimeType": "application/x-something-new",
                }}}],
        }}]},
    }
    doc = build_document(j)
    chip = doc.tabs[0].blocks[0].runs[0]
    assert chip.kind == "richlink"


def test_inline_objects_resolved_from_per_tab_map():
    """Multi-tab docs nest inlineObjects under each documentTab, not at the
    top level. Regression for: 49 images on a real doc were all silently
    suppressed because the walker only checked top-level inlineObjects."""
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "inlineObjects": {},   # empty at top level — typical for tabbed docs
        "tabs": [{
            "tabProperties": {"tabId": "T1", "title": "Notes"},
            "documentTab": {
                "inlineObjects": {
                    "kix.img1": {"inlineObjectProperties": {"embeddedObject": {
                        "imageProperties": {"contentUri": "https://x/img1.png"},
                        "size": {
                            "width": {"magnitude": 200},
                            "height": {"magnitude": 100},
                        },
                    }}},
                },
                "body": {"content": [{"paragraph": {
                    "elements": [
                        {"textRun": {"content": "Before "}},
                        {"inlineObjectElement": {"inlineObjectId": "kix.img1"}},
                        {"textRun": {"content": " after"}},
                    ],
                }}]},
            },
        }],
    }
    doc = build_document(j)
    runs = doc.tabs[0].blocks[0].runs
    images = [r for r in runs if hasattr(r, "image_id")]
    assert len(images) == 1
    assert images[0].image_id == "kix.img1"
    assert images[0].src == "https://x/img1.png"


def test_dangling_inline_object_element_is_suppressed():
    """When an inlineObjectElement points to no inlineObject (decorative chip
    icon), it should be dropped from the AST, not crash or emit a broken
    image."""
    j = {
        "documentId": "X", "title": "T", "revisionId": "r",
        "inlineObjects": {},   # empty — no real objects
        "body": {"content": [{"paragraph": {
            "elements": [
                {"textRun": {"content": "Before "}},
                {"inlineObjectElement": {"inlineObjectId": "nope"}},
                {"textRun": {"content": "after"}},
            ],
        }}]},
    }
    doc = build_document(j)
    runs = doc.tabs[0].blocks[0].runs
    assert all(not isinstance(r, type(None)) for r in runs)
    text = "".join(r.text for r in runs if hasattr(r, "text"))
    assert text == "Before after"


def test_captured_at_defaults_to_now():
    before = datetime.now(UTC)
    doc = build_document(_docs_json_minimal())
    after = datetime.now(UTC)
    assert before <= doc.captured_at <= after
