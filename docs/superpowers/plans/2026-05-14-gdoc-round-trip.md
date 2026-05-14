# gdoc round-trip Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `gdoc push` — symmetric round-trip from a single-file Markdown back into a Google Doc, including create-from-scratch.

**Architecture:** Approach 2 from the spec — rich AST as the in-memory representation; an `OpPlan` IR mediates between AST diff and the write backends; Docs API `batchUpdate` is the primary write channel with `/save` as a future fallback.

**Tech Stack:** Python 3.13, uv, pytest, existing google-doc-diff package layout, google-api-python-client (already a dep).

**Spec:** [`docs/superpowers/specs/2026-05-14-gdoc-round-trip-design.md`](../specs/2026-05-14-gdoc-round-trip-design.md).

**Overnight scope cuts.** This plan deliberately defers parts of the spec to keep the overnight build reachable. Deferred (each becomes a follow-up plan):

- Three-way merge against the remote (`merge/`). v1 of push is **`--force` only**: diff(base, local) → apply, no remote fetch-and-merge.
- The `/save` backend (`apply/kix_save.py`). Docs API only for now.
- Authoring comments and suggestions. Reading them stays as v1.
- Chip authoring.
- Conflict UX (`--continue`, `--abort`, `.gd-conflict` divs).
- Live end-to-end tests against a real doc.

What lands in this plan:

- Extended AST (typed `ParagraphProperties`, fuller `StyleDescriptor`, typed chip nodes).
- Round-trippable `emit/markdown.py` extensions (frontmatter `gdoc:` namespace, `--ot-*` custom properties, paragraph IDs).
- A working `parse/markdown.py` for prose, headings, lists, basic tables, links, inline formatting.
- `ops/diff.py` producing `OpPlan` for the supported primitive set.
- `apply/docs_api.py` translating OpPlan to `batchUpdate`.
- `cli/push.py` with `--new --title`, `--force`, `--dry-run`, `--plan-only`.
- Property tests proving `ast → emit → parse → ast'` is identity for fixture docs.

---

## Chunk 1: AST extensions (the foundation)

**Files:**
- Modify: `src/google_doc_diff/ast/nodes.py`
- Create: `tests/unit/ast/test_paragraph_properties.py`
- Create: `tests/unit/ast/test_paragraph_id.py`

### Task 1.1: Add `ParagraphProperties` dataclass

- [ ] **Step 1: Write failing test**

```python
# tests/unit/ast/test_paragraph_properties.py
from google_doc_diff.ast.nodes import ParagraphProperties

def test_default_is_all_inherit():
    p = ParagraphProperties()
    assert p.line_height is None
    assert p.space_before_pt is None
    assert p.keep_with_next is None
    assert p.heading_depth is None

def test_frozen_and_hashable():
    a = ParagraphProperties(line_height=1.15)
    b = ParagraphProperties(line_height=1.15)
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}
```

- [ ] **Step 2: Run test to confirm it fails**

`uv run pytest tests/unit/ast/test_paragraph_properties.py -v` → ImportError.

- [ ] **Step 3: Implement minimal `ParagraphProperties`**

Add to `src/google_doc_diff/ast/nodes.py`:

```python
@dataclass(frozen=True)
class ParagraphProperties:
    """Paragraph-level OT properties (the ps_* namespace).

    None on a field means 'inherit / not set' — matches StyleDescriptor."""

    line_height: float | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    indent_left_pt: float | None = None
    indent_right_pt: float | None = None
    indent_first_line_pt: float | None = None
    alignment: str | None = None         # 'left'|'right'|'center'|'justify'
    heading_depth: int | None = None     # 1..6, or None for body text
    keep_with_next: bool | None = None
    keep_lines_together: bool | None = None
    page_break_before: bool | None = None
    direction: str | None = None         # 'ltr' | 'rtl'
```

- [ ] **Step 4: Run test, expect PASS**

`uv run pytest tests/unit/ast/test_paragraph_properties.py -v`.

- [ ] **Step 5: Add `paragraph_properties` field to `Paragraph` and `Heading`**

In `nodes.py`, on both `Paragraph` and `Heading` dataclasses, add:

```python
paragraph_properties: ParagraphProperties | None = None
```

- [ ] **Step 6: Run the full unit test suite to confirm nothing else broke**

`uv run pytest tests/unit -v` — all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/google_doc_diff/ast/nodes.py tests/unit/ast/test_paragraph_properties.py
git commit -m "ast: add typed ParagraphProperties + field on Paragraph/Heading"
```

### Task 1.2: Add `paragraph_id` to `Paragraph` and `Heading`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/ast/test_paragraph_id.py
from google_doc_diff.ast.nodes import Paragraph, Heading, Run

def test_paragraph_has_optional_id():
    p = Paragraph(runs=[Run(text="hi")], paragraph_id="p-abc123")
    assert p.paragraph_id == "p-abc123"

def test_paragraph_id_defaults_to_none():
    p = Paragraph(runs=[Run(text="hi")])
    assert p.paragraph_id is None
```

- [ ] **Step 2: Run, expect fail (no field).**

- [ ] **Step 3: Add field to `Paragraph` and `Heading`**

```python
paragraph_id: str | None = None
```

- [ ] **Step 4: Run, expect pass; run full unit suite.**

- [ ] **Step 5: Commit**

```bash
git commit -am "ast: add paragraph_id to Paragraph and Heading"
```

### Task 1.3: Extend `StyleDescriptor` for fuller OT `ts_*` coverage

- [ ] **Step 1: Write failing test**

```python
# tests/unit/ast/test_style_descriptor.py
from google_doc_diff.ast.nodes import StyleDescriptor

def test_new_text_style_fields_default_none():
    s = StyleDescriptor()
    assert s.vertical_alignment is None
    assert s.small_caps is None
    assert s.weight is None
    assert s.language is None
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Extend `StyleDescriptor`**

Add fields after the existing ones:

```python
vertical_alignment: str | None = None   # 'normal'|'super'|'sub'
small_caps: bool | None = None
weight: int | None = None               # 100..900 numeric weight
language: str | None = None             # BCP-47 tag
```

- [ ] **Step 4: Run, expect pass; full suite still green.**

- [ ] **Step 5: Commit**

```bash
git commit -am "ast: extend StyleDescriptor with vertical_alignment / small_caps / weight / language"
```

### Task 1.4: Add typed `VotingChip` AST node

- [ ] **Step 1: Write failing test**

```python
# tests/unit/ast/test_voting_chip.py
from google_doc_diff.ast.nodes import VotingChip, Voter

def test_voting_chip_construction():
    chip = VotingChip(
        chip_id="kix.escg9h9fzc85",
        emoji="➕",
        voters=[Voter(obfuscated_id="113538…")],
        current_user_voted=True,
        signature="AastPo9...",
    )
    assert chip.emoji == "➕"
    assert chip.current_user_voted is True
    assert chip.voters[0].obfuscated_id == "113538…"
```

- [ ] **Step 2: Run, expect fail (ImportError).**

- [ ] **Step 3: Add `Voter` and `VotingChip`**

```python
@dataclass(frozen=True)
class Voter:
    obfuscated_id: str

@dataclass
class VotingChip:
    """A doc-tag voting chip with full per-voter state.

    Captured at pull time from the OT stream; the public Docs API hides this
    as a U+E907 placeholder."""

    chip_id: str
    emoji: str
    voters: list[Voter] = field(default_factory=list)
    current_user_voted: bool = False
    signature: str = ""
```

- [ ] **Step 4: Run, expect pass.**

- [ ] **Step 5: Commit**

```bash
git commit -am "ast: add typed VotingChip + Voter nodes"
```

---

## Chunk 2: emit/markdown round-trippability

**Goal:** make `emit/markdown.py` carry every field needed for byte-identical round-trip. Each task adds one round-trippable property and a fixture-based test proving emit is deterministic.

**Files:**
- Modify: `src/google_doc_diff/emit/markdown.py`
- Modify: `src/google_doc_diff/styles/css.py`
- Modify: `src/google_doc_diff/styles/classes.py`
- Create: `tests/unit/emit/test_frontmatter_gdoc_namespace.py`
- Create: `tests/unit/emit/test_ot_custom_properties.py`
- Create: `tests/unit/emit/test_paragraph_id_attribute.py`

### Task 2.1: Frontmatter `gdoc:` namespace

- [ ] **Step 1: Write failing test**

```python
# tests/unit/emit/test_frontmatter_gdoc_namespace.py
import yaml
from google_doc_diff.ast.nodes import Document, Tab
from google_doc_diff.emit.markdown import emit_document_md
from datetime import datetime, timezone

def _doc(**kw):
    return Document(
        doc_id="d1", title="t", revision_id="r1", drive_url="u",
        captured_at=datetime(2026,1,1,tzinfo=timezone.utc),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="t", level=0)],
        **kw,
    )

def test_gdoc_namespace_contains_base_revision():
    doc = _doc()
    doc.gdoc_state = {"base_revision": 71, "model_version": 142}
    out = emit_document_md(doc)
    fm_block = out.split("---", 2)[1]
    fm = yaml.safe_load(fm_block)
    assert fm["gdoc"]["base_revision"] == 71
    assert fm["gdoc"]["model_version"] == 142
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

In `ast/nodes.py`, add to `Document`:

```python
gdoc_state: dict = field(default_factory=dict)
```

In `emit/markdown.py`, in `_emit_frontmatter` (or wherever the frontmatter dict is assembled), add at the end:

```python
if doc.gdoc_state:
    fm["gdoc"] = doc.gdoc_state
```

- [ ] **Step 4: Run, expect pass; full suite green.**

- [ ] **Step 5: Commit**

```bash
git commit -am "emit: add gdoc: namespace under frontmatter for round-trip state"
```

### Task 2.2: Emit `paragraph_id` as pandoc attribute

- [ ] **Step 1: Write failing test**

```python
# tests/unit/emit/test_paragraph_id_attribute.py
from google_doc_diff.ast.nodes import Document, Tab, Paragraph, Run
from google_doc_diff.emit.markdown import emit_document_md
from datetime import datetime, timezone

def test_paragraph_id_emitted_as_anchor():
    doc = Document(
        doc_id="d", title="t", revision_id="r", drive_url="u",
        captured_at=datetime(2026,1,1,tzinfo=timezone.utc),
        schema_version=1, last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t1", title="t", level=0, blocks=[
            Paragraph(runs=[Run(text="Hello")], paragraph_id="p-abc"),
        ])],
    )
    out = emit_document_md(doc)
    # The id should appear on the paragraph in a pandoc div or attribute
    assert "#p-abc" in out or 'id="p-abc"' in out
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

In `emit/markdown.py`, when emitting a paragraph that has a `paragraph_id` (or classes), wrap it in a fenced div with `{#p-id .class1 .class2}`. If only an id and no classes, prefer the compact `paragraph text {#p-id}` trailing attribute form (pandoc-friendly).

- [ ] **Step 4: Run, expect pass; full suite green.**

- [ ] **Step 5: Commit**

```bash
git commit -am "emit: paragraph_id emitted as pandoc anchor attribute"
```

### Task 2.3: Emit `--ot-*` custom properties for `ParagraphProperties`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/emit/test_ot_custom_properties.py
from google_doc_diff.styles.css import paragraph_props_to_css

def test_keep_with_next_emitted_as_ot_custom_property():
    css = paragraph_props_to_css({"line_height": 1.15, "keep_with_next": True})
    assert "--ot-line-height: 1.15" in css
    assert "--ot-keep-with-next: true" in css
```

- [ ] **Step 2: Run, expect ImportError fail.**

- [ ] **Step 3: Implement**

Add to `src/google_doc_diff/styles/css.py`:

```python
_OT_PROP_NAMES = {
    "line_height": "--ot-line-height",
    "space_before_pt": "--ot-space-before",
    "space_after_pt": "--ot-space-after",
    "indent_left_pt": "--ot-indent-left",
    "indent_right_pt": "--ot-indent-right",
    "indent_first_line_pt": "--ot-indent-first-line",
    "alignment": "--ot-alignment",
    "heading_depth": "--ot-heading-depth",
    "keep_with_next": "--ot-keep-with-next",
    "keep_lines_together": "--ot-keep-lines-together",
    "page_break_before": "--ot-page-break-before",
    "direction": "--ot-direction",
}

def _format_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)

def paragraph_props_to_css(props: dict) -> str:
    items = []
    for k in sorted(props):
        if k not in _OT_PROP_NAMES or props[k] is None:
            continue
        items.append(f"  {_OT_PROP_NAMES[k]}: {_format_value(props[k])};")
    return "\n".join(items)
```

- [ ] **Step 4: Run, expect pass.**

- [ ] **Step 5: Wire into existing class builder**

In `styles/classes.py` where blocks get bucketed into classes, include `ParagraphProperties` in the descriptor hash so paragraphs with the same property bag share a class. Emit the class body with `paragraph_props_to_css(asdict(p.paragraph_properties))`.

- [ ] **Step 6: Run full unit suite to confirm no regression.**

- [ ] **Step 7: Commit**

```bash
git commit -am "styles: emit --ot-* custom properties for ParagraphProperties bag"
```

### Task 2.4: Round-trippable suggestion + comment attributes (sanity)

Just one regression test: today's v1 already emits these; add a fixture-based identity test to lock the current shape so the parser in Chunk 3 can rely on it.

- [ ] **Step 1: Write a regression test**

```python
# tests/unit/emit/test_suggestion_comment_attributes_stable.py
def test_emit_is_deterministic_for_suggestion_doc():
    # Load a fixture AST with one suggestion + one comment; emit it.
    # Assert exact byte equality with a previously-saved expected.md.
    ...
```

Use a small hand-rolled fixture in `tests/fixtures/round_trip/suggestion_simple.json` (Document AST as JSON via `dataclasses.asdict`).

- [ ] **Step 2: Run** — generate the expected.md the first time by running with `--write-fixtures` (a tiny pytest config flag pattern). After it's saved, the test asserts identity.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/round_trip/suggestion_simple.{json,md}
git commit -am "tests: lock suggestion + comment emit byte shape for parser to target"
```

---

## Chunk 3: parse/markdown.py — minimum viable invertibility

**Goal:** `parse(emit(ast)) == ast` for fixture ASTs covering paragraphs, headings, lists, inline formatting (bold/italic/strike/underline), links, and the frontmatter `gdoc:` block.

**Files:**
- Modify: `src/google_doc_diff/parse/markdown.py` (currently stubbed)
- Create: `tests/round_trip/test_markdown_round_trip.py`
- Create: `tests/fixtures/round_trip/plain_paragraphs.{json,md}`
- Create: `tests/fixtures/round_trip/with_headings.{json,md}`
- Create: `tests/fixtures/round_trip/with_lists.{json,md}`

### Task 3.1: Parse frontmatter

- [ ] **Step 1: Write failing test**

```python
# tests/unit/parse/test_parse_frontmatter.py
from google_doc_diff.parse.markdown import parse_frontmatter

def test_parses_doc_metadata_and_gdoc_namespace():
    md = """---
title: My Doc
doc_id: abc
revision_id: r1
drive_url: https://docs.google.com/document/d/abc/edit
captured_at: '2026-05-14T00:00:00+00:00'
schema_version: 1
last_modifying_user: null
source_mode: pull
comments_preserved: true
suggestions_preserved: true
gdoc:
  base_revision: 7
---

# Heading
"""
    fm, body = parse_frontmatter(md)
    assert fm["title"] == "My Doc"
    assert fm["gdoc"]["base_revision"] == 7
    assert body.startswith("# Heading")
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement `parse_frontmatter`**

In `parse/markdown.py`:

```python
def parse_frontmatter(md: str) -> tuple[dict, str]:
    if not md.startswith("---\n"):
        return {}, md
    end = md.find("\n---\n", 4)
    if end == -1:
        return {}, md
    fm = yaml.safe_load(md[4:end])
    body = md[end + 5:]
    return fm or {}, body
```

- [ ] **Step 4: Run, expect pass.**

- [ ] **Step 5: Commit**

### Task 3.2: Parse paragraphs and headings (no inline formatting yet)

Use `markdown-it-py` (already used elsewhere in Pandoc-flavored work) or `mistune` — adopt whichever already exists in the project's deps. Otherwise add `markdown-it-py` to pyproject.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/parse/test_parse_basic_blocks.py
from google_doc_diff.parse.markdown import parse_body
from google_doc_diff.ast.nodes import Heading, Paragraph

def test_parses_h1_followed_by_paragraph():
    body = "# Title\n\nHello world.\n"
    blocks = parse_body(body)
    assert isinstance(blocks[0], Heading) and blocks[0].level == 1
    assert blocks[0].runs[0].text == "Title"
    assert isinstance(blocks[1], Paragraph)
    assert blocks[1].runs[0].text == "Hello world."
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement `parse_body`**

Walk markdown-it tokens; emit `Heading` / `Paragraph` / etc. AST nodes. Pandoc attribute parsing (`{#id .class key=val}`) extracts `paragraph_id` and class names.

- [ ] **Step 4: Run, expect pass.**

- [ ] **Step 5: Commit**

### Task 3.3: Parse inline formatting

- [ ] **Step 1: Write failing tests for each: bold, italic, underline, strike, link.**

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement.** Map markdown-it inline tokens to `Run` + `StyleDescriptor`.

- [ ] **Step 4: Run, expect pass; commit.**

### Task 3.4: Parse pandoc attribute syntax

- [ ] **Step 1: Write failing tests for `{#id}`, `{.class1 .class2}`, `{key=value}`.**

- [ ] **Step 2: Implement a small attribute parser.**

- [ ] **Step 3: Commit.**

### Task 3.5: Parse lists (bulleted + ordered)

- [ ] **Step 1: Write failing tests for one-level and two-level lists.**

- [ ] **Step 2: Implement.** Emit `ListItem` blocks with `level`, `kind`, `list_id`.

- [ ] **Step 3: Commit.**

### Task 3.6: The round-trip property test

- [ ] **Step 1: Write the harness**

```python
# tests/round_trip/test_markdown_round_trip.py
import json, pathlib, pytest
from google_doc_diff.ast.nodes import Document  # plus all the nodes
from google_doc_diff.emit.markdown import emit_document_md
from google_doc_diff.parse.markdown import parse_document_md
from tests.support.ast_io import ast_from_json  # tiny helper to rebuild AST from JSON

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "round_trip"

@pytest.mark.parametrize("name", sorted(p.stem for p in FIXTURES.glob("*.json")))
def test_emit_parse_emit_byte_identical(name):
    ast = ast_from_json(json.loads((FIXTURES / f"{name}.json").read_text()))
    md1 = emit_document_md(ast)
    ast2 = parse_document_md(md1)
    md2 = emit_document_md(ast2)
    assert md1 == md2, f"second-emit differs for {name}"
```

- [ ] **Step 2: Add 3 fixture pairs** (`plain_paragraphs`, `with_headings`, `with_lists`).

- [ ] **Step 3: Run; iterate until all three pass.**

- [ ] **Step 4: Commit.**

---

## Chunk 4: OpPlan IR + diff

**Files:**
- Create: `src/google_doc_diff/ops/__init__.py`
- Create: `src/google_doc_diff/ops/primitives.py`
- Create: `src/google_doc_diff/ops/diff.py`
- Create: `tests/unit/ops/test_primitives.py`
- Create: `tests/unit/ops/test_diff_structural.py`
- Create: `tests/unit/ops/test_diff_content.py`

### Task 4.1: Define primitives

- [ ] **Step 1: Write tests covering construction of each primitive.**

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement.** A `primitives.py` with frozen dataclasses:

```python
@dataclass(frozen=True)
class InsertText:
    block_id: str
    offset: int
    text: str
    run_style: StyleDescriptor | None = None

@dataclass(frozen=True)
class DeleteRange:
    block_id: str
    start: int
    end: int

@dataclass(frozen=True)
class ApplyStyle:
    scope: str          # 'text' | 'paragraph' | 'heading'
    block_id: str
    start: int
    end: int
    style: dict         # serialized StyleDescriptor / ParagraphProperties

@dataclass(frozen=True)
class InsertBlock:
    after_id: str | None     # None = at top
    block: object            # the AST node

@dataclass(frozen=True)
class DeleteBlock:
    block_id: str

@dataclass(frozen=True)
class MoveBlock:
    block_id: str
    after_id: str | None

Op = (InsertText | DeleteRange | ApplyStyle |
      InsertBlock | DeleteBlock | MoveBlock)

@dataclass
class OpPlan:
    ops: list[Op] = field(default_factory=list)
```

- [ ] **Step 4: Run, pass, commit.**

### Task 4.2: Structural diff (block-tree)

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/ops/test_diff_structural.py
def test_no_change_yields_empty_plan(): ...
def test_paragraph_inserted(): ...
def test_paragraph_deleted(): ...
def test_paragraph_moved(): ...
def test_paragraph_modified_text(): ...
```

Each fixture is a tiny hand-built `Document` AST pair.

- [ ] **Step 2: Run, expect ImportError.**

- [ ] **Step 3: Implement `ops/diff.py`:**

```python
def diff(base: Document, target: Document) -> OpPlan:
    """Two-phase diff: block-tree first, then per-block content."""
    plan = OpPlan()
    base_blocks_by_id = _index(base)
    target_blocks_by_id = _index(target)
    # 1. Detect deletes / inserts / moves at block level
    # 2. For each block id present in both, run _diff_block
    ...
    return plan
```

Block identity by `paragraph_id` / `heading.anchor_id` / `list_item.list_id` / `table.classes[*]` (we'll synthesize IDs at pull time later — for now require IDs to be present).

- [ ] **Step 4: Run, iterate, pass; commit.**

### Task 4.3: Content diff (text+style within a block)

- [ ] **Step 1: Write failing tests** — insert in middle, delete range, style toggled on a sub-range, run-replace.

- [ ] **Step 2: Implement `_diff_block`** using `difflib.SequenceMatcher` over the flattened text, plus a separate pass for style-only changes (runs with identical text but different `formatting`).

- [ ] **Step 3: Run, iterate, pass; commit.**

---

## Chunk 5: apply/docs_api.py

**Files:**
- Create: `src/google_doc_diff/apply/__init__.py`
- Create: `src/google_doc_diff/apply/policy.py`
- Create: `src/google_doc_diff/apply/docs_api.py`
- Create: `tests/unit/apply/test_policy.py`
- Create: `tests/unit/apply/test_docs_api_translate.py`

### Task 5.1: `policy.channel_for`

- [ ] **Step 1: Write failing tests** for each primitive's expected channel.

- [ ] **Step 2: Implement single dispatcher.** Per spec section "Channel selection — one function, one table". For the overnight scope, **all primitives route to DOCS_API**. The dispatcher exists so future ops can route to KIX_SAVE without churning callers.

- [ ] **Step 3: Run, pass; commit.**

### Task 5.2: Translate primitives to `batchUpdate` request shape

- [ ] **Step 1: Write failing tests** — for each primitive, assert the generated `Request` dict matches the Docs API schema.

```python
def test_insert_text_translates_to_insertText_request():
    op = InsertText(block_id="p-1", offset=0, text="Hello")
    # We need a mapping from paragraph_id -> doc-wide index; tests provide a fake index.
    reqs = translate([op], block_index={"p-1": 100})
    assert reqs == [{"insertText": {"location": {"index": 100}, "text": "Hello"}}]
```

- [ ] **Step 2: Implement `apply/docs_api.py`**

The translate function is a `match` statement on op type. The tricky part is the `block_index` map (which paragraph starts at which character index in the doc). At apply time the index is rebuilt from a fresh fetch of the doc.

- [ ] **Step 3: Run, pass; commit.**

### Task 5.3: Apply path against `batchUpdate`

- [ ] **Step 1: Write failing test** using a mocked Docs service.

```python
def test_apply_calls_batchUpdate(mock_docs_service):
    apply(plan, doc_id="abc", service=mock_docs_service)
    assert mock_docs_service.documents.return_value.batchUpdate.called
```

- [ ] **Step 2: Implement `apply()`.** Build the requests via `translate`, group by channel via `policy.channel_for`, call `documents().batchUpdate(documentId=doc_id, body={"requests": reqs}).execute()`.

- [ ] **Step 3: Run, pass; commit.**

---

## Chunk 6: cli/push.py

**Files:**
- Modify: `src/google_doc_diff/cli.py` (add `push` subcommand wiring)
- Create: `src/google_doc_diff/cli_push.py` (the heavy lifting)
- Create: `tests/test_cli_push.py`

### Task 6.1: `gdoc push --new --title TITLE PATH.md`

- [ ] **Step 1: Write failing test using a fake Docs service** that records calls.

```python
def test_push_new_creates_doc_and_applies_ops(tmp_path, fake_drive, fake_docs):
    p = tmp_path / "doc.md"
    p.write_text("# Hello\n\nWorld.\n")
    result = run_push_new(p, title="Hello", drive=fake_drive, docs=fake_docs)
    assert result.exit_code == 0
    assert fake_drive.created_titles == ["Hello"]
    assert any(r.get("insertText") for r in fake_docs.last_requests)
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement `cli_push.run_push_new`**

1. Parse markdown to AST.
2. Create empty doc via Drive (`files().create(body={"name": title, "mimeType": "application/vnd.google-apps.document"})`).
3. Build base = empty Document AST.
4. `plan = diff(base, local_ast)`.
5. `apply(plan, doc_id, service=docs)`.
6. Refetch + rewrite local md with new `doc_id`, `revision_id`, `base_revision`.

- [ ] **Step 4: Run, pass; commit.**

### Task 6.2: `gdoc push --force PATH.md DOC`

- [ ] **Step 1: Write failing test** — pull a doc, edit the md, run push --force, assert the api calls match.

- [ ] **Step 2: Implement `run_push_force`**

1. Parse local md.
2. Fetch live doc → remote AST (via existing v1 `api/` code).
3. `plan = diff(remote_ast, local_ast)`. (No three-way merge; remote is the base.)
4. Apply.

- [ ] **Step 3: Run, pass; commit.**

### Task 6.3: `--dry-run` and `--plan-only PATH`

- [ ] **Step 1: Tests.** `--dry-run` prints summary, exits 0. `--plan-only` writes JSON.

- [ ] **Step 2: Implement.** A `Plan.to_json()` method on `OpPlan` + a simple terminal renderer that counts ops per primitive type.

- [ ] **Step 3: Commit.**

### Task 6.4: Wire into `cli.py`

- [ ] **Step 1: Add the `push` subparser** with args: optional `DOC` positional, `--new`, `--title`, `--force`, `--dry-run`, `--plan-only PATH`.

- [ ] **Step 2: Tests via `subprocess` against a tmp_path doc** (covered by cli test harness in v1).

- [ ] **Step 3: Commit.**

---

## Chunk 7: End-to-end round-trip property test

**Goal:** prove (against fixture data + mock services) that `pull → emit → parse → diff → apply` reconstructs the same AST.

**Files:**
- Create: `tests/round_trip/test_full_pipeline_mock.py`

### Task 7.1: Wire it together

- [ ] **Step 1: Write the property test**

```python
def test_full_pipeline_round_trips_via_mock_api(fixture_ast):
    md = emit_document_md(fixture_ast)
    parsed = parse_document_md(md)
    plan = diff(Document.empty(), parsed)
    fake_docs = FakeDocsService()
    apply(plan, doc_id="DOC", service=fake_docs)
    rebuilt = fake_docs.materialize_ast()
    assert rebuilt == fixture_ast
```

`FakeDocsService` is a small in-memory model of a Docs document — receives `batchUpdate` requests, applies them to a string + style runs, exposes `.materialize_ast()`.

- [ ] **Step 2: Build `FakeDocsService`.** Smaller than it sounds for the prose-only scope.

- [ ] **Step 3: Run against the fixture set from Chunk 3; iterate until green; commit.**

---

## Final tasks

- [ ] **Update README** with a short "round-trip preview" section pointing at the spec and the new commands.
- [ ] **Run the full suite** (`make test` and `make lint`) to confirm everything is green before the user wakes up.
- [ ] **Print a status summary** to stdout (or leave a `STATUS.md` in the worktree) so the user can pick up the thread quickly.

---

## Stop conditions

Stop and commit what's done if any of:

- A test in an earlier chunk goes red and the fix isn't obvious within a few iterations — skip the remaining tasks of that chunk, document the gap in `STATUS.md`, and continue to the next chunk **only if** it doesn't depend on the broken one.
- A planned dependency (e.g. `markdown-it-py` not in pyproject) blocks; add the dep via `uv add`, run, continue.
- The full suite goes red and a quick triage points at a v1 regression: revert the offending step, commit the salvage, continue.

The bare-minimum "wakes-up-working" state is: chunks 1–3 green, plus chunk 6 task 6.1 (`gdoc push --new`) end-to-end against the mock service. Everything else is a bonus.
