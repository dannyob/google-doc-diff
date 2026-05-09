# google-doc-diff v1 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking. Apply @superpowers:test-driven-development for every task — failing test first, minimal code to green, then commit. Apply @superpowers:verification-before-completion before claiming any task done.

**Goal:** Build a Python CLI (`gdoc`) that pulls Google Docs into high-fidelity Pandoc-flavor Markdown and HTML, with stable IDs, comments, and suggestions preserved as round-trip-ready metadata.

**Architecture:** Custom Python AST (dataclasses) sits between two AST builders (Docs API JSON for current revision; Google's exported markdown for historical revisions) and two parallel serializers (Markdown, HTML). Replay merges revision events with Drive Comments API events into one chronological timeline; each event becomes one git commit.

**Tech Stack:** Python ≥3.11, `uv`-managed venv, `click` for CLI, `google-api-python-client` + `google-auth-oauthlib` for Google APIs, `requests` for raw exportLinks fetches, `pytest` for tests, `ruff` for lint+format. Single dependency on Drive API v2 (deprecated but actively maintained — the only path to historical revision content).

**Spec:** `docs/superpowers/specs/2026-05-09-google-doc-diff-design.md` — read it first.

**Coding norms:**
- Per `~/.claude/CLAUDE.md`: prefer `uv` venvs; if not in the project's `.venv`, suggest the user activate and restart Claude.
- Commits: one focused commit per task; first line states what; body says why if non-obvious; AGPL-3.0-or-later license; no destructive `\rm`/`mv`/`cp` aliases.
- Tests are TDD red-green-refactor. Never write implementation before a failing test.
- Comments only when the WHY is non-obvious; never narrate WHAT.

---

## Chunk 1: Project scaffolding + AST core

Goal of this chunk: a working `uv`-managed package with a click CLI skeleton, the full AST dataclass tree, and tests proving equality/serialization of every node type. Nothing yet does any I/O. After this chunk, `make test` passes and `gdoc --version` prints `0.1.0`.

### Task 1.1: Bootstrap project from py-template

**Files:**
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `README.md`
- Create: `src/google_doc_diff/__init__.py`
- Create: `src/google_doc_diff/cli.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Copy template files and substitute placeholders**

`cp` is aliased to `cp -i` in the user's shell — we use `\cp` to bypass the alias. Single-quote any path containing literal `{{...}}` so zsh doesn't try brace-expansion. We do **NOT** copy the template's `.gitignore` because the project already has one committed; we also do **NOT** copy the template's `CLAUDE.md` (it has its own placeholders not relevant here).

```bash
\cp '/Users/danny/Public/src/dob/py-template/template/pyproject.toml' ./pyproject.toml
\cp '/Users/danny/Public/src/dob/py-template/template/Makefile' ./Makefile
\cp '/Users/danny/Public/src/dob/py-template/template/README.md' ./README.md
mkdir -p src/google_doc_diff tests
\cp '/Users/danny/Public/src/dob/py-template/template/src/{{package}}/__init__.py' src/google_doc_diff/__init__.py
\cp '/Users/danny/Public/src/dob/py-template/template/src/{{package}}/cli.py' src/google_doc_diff/cli.py
\cp '/Users/danny/Public/src/dob/py-template/template/tests/__init__.py' tests/__init__.py
\cp '/Users/danny/Public/src/dob/py-template/template/tests/conftest.py' tests/conftest.py
\cp '/Users/danny/Public/src/dob/py-template/template/tests/test_cli.py' tests/test_cli.py
```

Substitute `{{PROJECT_NAME}}` → `gdoc`, `{{PACKAGE_NAME}}` → `google_doc_diff`, `{{DESCRIPTION}}` → `Pull Google Docs into high-fidelity Markdown and HTML.`

```bash
LC_ALL=C find . -type f \( -name '*.py' -o -name '*.toml' -o -name 'Makefile' -o -name 'README.md' \) \
  -not -path './.venv/*' -not -path './.git/*' \
  -exec sed -i '' \
    -e 's/{{PROJECT_NAME}}/gdoc/g' \
    -e 's/{{PACKAGE_NAME}}/google_doc_diff/g' \
    -e 's|{{DESCRIPTION}}|Pull Google Docs into high-fidelity Markdown and HTML.|g' \
    {} +
```

**Fix the template Makefile bug.** The template's `Makefile` line 7 is `PACKAGE_NAME := {{PROJECT_NAME}}` (it should be `{{PACKAGE_NAME}}`). After the sed pass above, that line becomes `PACKAGE_NAME := gdoc`, but the make variable should hold the *python package name*, not the binary name. Fix it:

```bash
sed -i '' 's/^PACKAGE_NAME := gdoc$/PACKAGE_NAME := google_doc_diff/' Makefile
grep '^PACKAGE_NAME' Makefile
```

Expected: `PACKAGE_NAME := google_doc_diff`.

- [ ] **Step 2: Set Python version requirement to 3.11+**

Edit `pyproject.toml` line 5: `requires-python = ">=3.11"`.

- [ ] **Step 3: Replace the template `hello` command with a placeholder root group**

Edit `src/google_doc_diff/cli.py` to:

```python
"""Command-line interface for gdoc."""

import click


@click.group()
@click.version_option()
def cli():
    """Pull Google Docs into high-fidelity Markdown and HTML."""
    pass


if __name__ == "__main__":
    cli()
```

Edit `tests/test_cli.py` to remove the `test_hello` test and keep only:

```python
"""Tests for CLI commands."""

from google_doc_diff.cli import cli


def test_version(runner):
    """Test --version flag."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_no_args_shows_help(runner):
    """With no args, click prints help and exits 0 (or 2 depending on click version — accept either)."""
    result = runner.invoke(cli, [])
    assert "Pull Google Docs" in result.output or "Usage:" in result.output
```

- [ ] **Step 4: Set up venv and install in dev mode**

```bash
make install-dev
```

Expected: a `.venv/` is created, `gdoc` is installed editable, `pytest` is available. If you see "Activate with: source .venv/bin/activate" then **stop and tell the user to activate the venv and restart Claude with `claude -c`** before proceeding (per `~/.claude/CLAUDE.md`).

- [ ] **Step 5: Run tests and verify they pass**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml Makefile README.md src/ tests/
git commit -m "scaffold project from py-template

uv-managed package, click CLI skeleton, pytest. v1 entry point is the
'gdoc' command (placeholder)."
```

---

### Task 1.2: Add dependencies (runtime + dev) and ruff config

**Files:**
- Modify: `pyproject.toml`

Pinning dev deps in `pyproject.toml` rather than ad-hoc Makefile installs makes setups reproducible. Adding a minimal ruff config preempts surprise lint failures in Task 1.10.

- [ ] **Step 1: Add runtime dependencies, dev extras, and ruff config**

Edit `pyproject.toml`. Replace the existing `dependencies = [...]` block with:

```toml
dependencies = [
    "click>=8.0",
    "google-api-python-client>=2.100",
    "google-auth>=2.30",
    "google-auth-oauthlib>=1.2",
    "requests>=2.32",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "ruff>=0.5",
    "build",
    "twine",
    "coverage",
]
```

Append at the bottom of `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]
ignore = [
    "E501",  # line length — already governed by `line-length` for autofix; allow comments to exceed
    "B008",  # function call in default argument — common with click
]
```

- [ ] **Step 2: Install the new deps including the dev extra**

```bash
source .venv/bin/activate && uv pip install -e ".[dev]"
```

Expected: new packages installed without error.

- [ ] **Step 3: Verify imports work**

```bash
source .venv/bin/activate && python -c "import googleapiclient, google.auth, google_auth_oauthlib, requests, yaml; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Verify ruff config is valid**

```bash
source .venv/bin/activate && ruff check . --no-fix
```

Expected: no output, exit 0 (the only Python files are template scaffold which lint clean).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "add Google API + dev dependencies and ruff config

Pin dev tools (pytest, ruff, build, twine, coverage) as
optional-dependencies for reproducible installs. Configure ruff with
target-version py311 and an opinionated rule set."
```

---

### Task 1.3: Define module skeleton (empty packages)

**Files:**
- Create: `src/google_doc_diff/ast/__init__.py`
- Create: `src/google_doc_diff/styles/__init__.py`
- Create: `src/google_doc_diff/emit/__init__.py`
- Create: `src/google_doc_diff/parse/__init__.py`
- Create: `src/google_doc_diff/replay/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/round_trip/__init__.py`
- Create: `tests/fixtures/__init__.py`

- [ ] **Step 1: Create empty packages**

```bash
mkdir -p src/google_doc_diff/{ast,styles,emit,parse,replay}
mkdir -p tests/{unit,round_trip,fixtures}
mkdir -p tests/fixtures/{docs,exported,comments,expected}
for d in src/google_doc_diff/ast src/google_doc_diff/styles src/google_doc_diff/emit \
         src/google_doc_diff/parse src/google_doc_diff/replay \
         tests/unit tests/round_trip tests/fixtures; do
  touch "$d/__init__.py"
done
touch tests/fixtures/docs/.gitkeep tests/fixtures/exported/.gitkeep \
      tests/fixtures/comments/.gitkeep tests/fixtures/expected/.gitkeep
```

- [ ] **Step 2: Verify tests still pass**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: `2 passed`.

- [ ] **Step 3: Commit**

```bash
git add src/google_doc_diff/ tests/
git commit -m "add empty package skeleton (ast, styles, emit, parse, replay)"
```

---

### Task 1.4: AST formatting types (Run + StyleDescriptor)

**Files:**
- Create: `src/google_doc_diff/ast/nodes.py`
- Create: `tests/unit/test_ast_run.py`

These are the smallest building blocks; everything else uses them. `StyleDescriptor` captures a frozen, hashable bundle of inline formatting (bold, italic, font, color, link, etc.). `Run` is text plus its formatting.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ast_run.py`:

```python
"""Tests for Run and StyleDescriptor AST nodes."""

import dataclasses

import pytest

from google_doc_diff.ast.nodes import Run, StyleDescriptor


def test_styledescriptor_is_hashable_and_frozen():
    s1 = StyleDescriptor(bold=True, italic=False, font_family="Arial", font_size_pt=11.0)
    s2 = StyleDescriptor(bold=True, italic=False, font_family="Arial", font_size_pt=11.0)
    assert s1 == s2
    assert hash(s1) == hash(s2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s1.bold = False  # frozen


def test_run_carries_text_and_formatting():
    s = StyleDescriptor(bold=True)
    r = Run(text="hello", formatting=s)
    assert r.text == "hello"
    assert r.formatting.bold is True


def test_run_equality():
    s = StyleDescriptor(bold=True)
    assert Run(text="x", formatting=s) == Run(text="x", formatting=s)
    assert Run(text="x", formatting=s) != Run(text="y", formatting=s)


def test_run_default_formatting_is_empty_descriptor():
    r = Run(text="plain")
    assert r.formatting == StyleDescriptor()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_run.py -v
```

Expected: ImportError or AttributeError — `nodes` doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

Create `src/google_doc_diff/ast/nodes.py`. All imports go at the top now so later tasks just append dataclasses below.

```python
"""AST node dataclasses for google-doc-diff.

The AST mirrors the Google Docs API structure, normalized into Python
dataclasses. Stable IDs (where the API exposes them) are first-class: every
comment, suggestion, footnote, tab, bookmark, named-range, and image carries
its Docs/Drive ID, prefixed by kind. See the spec's "Stable ID strategy"
section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: F401  (used by Comment/Suggestion in Task 1.7)


@dataclass(frozen=True)
class StyleDescriptor:
    """Frozen bundle of inline formatting properties for a Run.

    None on a field means 'inherit / not set'. Equality and hash are by
    field-tuple so identical descriptors collapse to the same downstream
    artifacts.

    NB: Python's built-in hash() randomizes string hashes per process
    (PYTHONHASHSEED). When `styles/classes.py` synthesizes a deterministic
    class name like `gd-style-{hash8}`, it MUST use hashlib (e.g.
    sha256(repr(descriptor).encode()).hexdigest()[:8]) — never the built-in
    hash(). This dataclass's __hash__ is fine for in-process dict keys but
    NOT for cross-process determinism.
    """

    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strikethrough: bool | None = None
    superscript: bool | None = None
    subscript: bool | None = None
    font_family: str | None = None
    font_size_pt: float | None = None
    foreground_color: str | None = None      # "#RRGGBB"
    background_color: str | None = None
    link_url: str | None = None              # if this run is a link
    link_anchor: str | None = None           # bookmark anchor target


@dataclass
class Run:
    """A contiguous span of text with one StyleDescriptor."""

    text: str
    formatting: StyleDescriptor = field(default_factory=StyleDescriptor)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_run.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/google_doc_diff/ast/nodes.py tests/unit/test_ast_run.py
git commit -m "add Run and StyleDescriptor AST nodes"
```

---

### Task 1.5: AST inline (run-level) wrapper nodes

**Files:**
- Modify: `src/google_doc_diff/ast/nodes.py`
- Create: `tests/unit/test_ast_inline.py`

These wrap or annotate `Run`s with cross-cutting concerns: comment anchors, suggestion ranges, footnote markers, bookmarks, smart chips, etc. They reference `Document.comments` / `.suggestions` / `.footnotes` by ID; they do not embed those collections.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ast_inline.py`:

```python
"""Tests for inline (run-level) AST nodes."""

from google_doc_diff.ast.nodes import (
    BookmarkAnchor,
    CommentAnchor,
    FootnoteRef,
    InlineEquation,
    LineBreak,
    NamedRangeAnchor,
    Run,
    SmartChip,
    SuggestionDel,
    SuggestionIns,
    Unsupported,
)


def test_comment_anchor_wraps_runs_and_carries_id():
    a = CommentAnchor(comment_id="c-AAA1", runs=[Run(text="phrase")])
    assert a.comment_id == "c-AAA1"
    assert a.runs[0].text == "phrase"


def test_suggestion_ins_and_del_share_the_id_field_name():
    ins = SuggestionIns(suggestion_id="s-X", runs=[Run(text="new")])
    delete = SuggestionDel(suggestion_id="s-X", runs=[Run(text="old")])
    assert ins.suggestion_id == delete.suggestion_id == "s-X"


def test_footnote_ref_only_carries_id():
    f = FootnoteRef(footnote_id="fn-1")
    assert f.footnote_id == "fn-1"


def test_bookmark_and_named_range_anchors():
    b = BookmarkAnchor(bookmark_id="bm-1")
    n = NamedRangeAnchor(named_range_id="nr-2")
    assert b.bookmark_id == "bm-1"
    assert n.named_range_id == "nr-2"


def test_smart_chip_carries_kind_and_data():
    c = SmartChip(kind="person", data={"email": "alice@example.com"})
    assert c.kind == "person"
    assert c.data["email"] == "alice@example.com"


def test_inline_equation_holds_latex():
    e = InlineEquation(latex="E = mc^2")
    assert e.latex == "E = mc^2"


def test_line_break_singleton_equality():
    assert LineBreak() == LineBreak()


def test_unsupported_inline_carries_kind_and_raw():
    u = Unsupported(kind="weirdElement", raw={"foo": "bar"})
    assert u.kind == "weirdElement"
    assert u.raw == {"foo": "bar"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_inline.py -v
```

Expected: ImportError — these classes don't exist yet.

- [ ] **Step 3: Write minimal implementation**

Append to `src/google_doc_diff/ast/nodes.py`:

```python
# --- Inline (run-level) wrappers --------------------------------------------


@dataclass
class CommentAnchor:
    """Marks a span of inline content as the anchor for a comment.

    The actual comment text + replies live in Document.comments[comment_id].
    """

    comment_id: str
    runs: list[Run] = field(default_factory=list)


@dataclass
class SuggestionIns:
    """An insertion suggestion. May share suggestion_id with a paired
    SuggestionDel for replacement suggestions (see spec)."""

    suggestion_id: str
    runs: list[Run] = field(default_factory=list)


@dataclass
class SuggestionDel:
    """A deletion suggestion. May share suggestion_id with a paired
    SuggestionIns for replacement suggestions."""

    suggestion_id: str
    runs: list[Run] = field(default_factory=list)


@dataclass
class FootnoteRef:
    """A footnote marker in prose; the footnote body lives in
    Document.footnotes[footnote_id]."""

    footnote_id: str


@dataclass
class BookmarkAnchor:
    bookmark_id: str


@dataclass
class NamedRangeAnchor:
    named_range_id: str


@dataclass
class SmartChip:
    """A Docs smart chip (person, file, date, place, calendar event, ...).

    'data' carries kind-specific fields verbatim from the API (e.g.
    {'email': '...'} for a person chip). The serializer renders the chip's
    visible text plus the metadata as data-* attributes.
    """

    kind: str
    data: dict
    display_text: str = ""


@dataclass
class InlineEquation:
    latex: str


@dataclass(frozen=True)
class LineBreak:
    """A soft line break inside a paragraph (Docs <br>-equivalent)."""


@dataclass
class Unsupported:
    """Typed-fallback node for any Docs API element this version doesn't
    understand. Both serializers preserve it as an opaque blob with kind
    and raw JSON in data attributes."""

    kind: str
    raw: dict
```

- [ ] **Step 4: Run test to verify it passes**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_inline.py -v
```

Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/google_doc_diff/ast/nodes.py tests/unit/test_ast_inline.py
git commit -m "add inline AST node types (anchors, suggestions, chips, etc.)"
```

---

### Task 1.6: AST block-level nodes

**Files:**
- Modify: `src/google_doc_diff/ast/nodes.py`
- Create: `tests/unit/test_ast_blocks.py`

These hold runs and other inline content, plus block-level attributes (heading level, list ID, table structure, etc.).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ast_blocks.py`:

```python
"""Tests for block-level AST nodes."""

from google_doc_diff.ast.nodes import (
    Cell,
    CodeBlock,
    EquationBlock,
    Heading,
    HorizontalRule,
    Image,
    ListItem,
    PageBreak,
    Paragraph,
    Row,
    Run,
    SectionBreak,
    Table,
    TableOfContents,
)


def test_heading_carries_level_runs_and_optional_anchor():
    h = Heading(level=2, runs=[Run(text="My H2")], anchor_id="h-AB")
    assert h.level == 2
    assert h.runs[0].text == "My H2"
    assert h.anchor_id == "h-AB"


def test_paragraph_default_classes_empty():
    p = Paragraph(runs=[Run(text="hi")])
    assert p.classes == []


def test_list_item_kind_and_level():
    li = ListItem(level=0, kind="bulleted", list_id="L1", runs=[Run(text="x")])
    assert li.level == 0
    assert li.kind == "bulleted"


def test_table_structure_uses_row_and_cell_wrappers():
    cell = Cell(blocks=[Paragraph(runs=[Run(text="a")])], colspan=1, rowspan=1)
    row = Row(cells=[cell])
    t = Table(rows=[row])
    assert t.rows[0].cells[0].blocks[0].runs[0].text == "a"


def test_image_carries_id_and_dimensions():
    img = Image(image_id="i-IMG1", src="https://...", alt="", width_px=400, height_px=300)
    assert img.image_id == "i-IMG1"


def test_code_block_default_language_none():
    cb = CodeBlock(text="print('hi')\n")
    assert cb.language is None


def test_singleton_block_types_are_equal_to_themselves():
    assert HorizontalRule() == HorizontalRule()
    assert PageBreak() == PageBreak()
    assert SectionBreak() == SectionBreak()
    assert TableOfContents() == TableOfContents()


def test_equation_block_holds_latex():
    eq = EquationBlock(latex="E = mc^2")
    assert eq.latex == "E = mc^2"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_blocks.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write minimal implementation**

Append to `src/google_doc_diff/ast/nodes.py`:

```python
# --- Blocks ----------------------------------------------------------------


@dataclass
class Heading:
    level: int                           # 1..6
    runs: list[Run] = field(default_factory=list)
    anchor_id: str | None = None         # "h-..." when Docs gave us one
    classes: list[str] = field(default_factory=list)


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)


@dataclass
class ListItem:
    level: int
    kind: str                            # 'bulleted' | 'ordered'
    list_id: str
    runs: list[Run] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)


@dataclass
class Cell:
    blocks: list = field(default_factory=list)   # list[Block]
    colspan: int = 1
    rowspan: int = 1
    classes: list[str] = field(default_factory=list)


@dataclass
class Row:
    cells: list[Cell] = field(default_factory=list)


@dataclass
class Table:
    rows: list[Row] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)


@dataclass
class Image:
    image_id: str                         # "i-..."
    src: str                              # URL or relative path (after extract)
    alt: str = ""
    width_px: int | None = None
    height_px: int | None = None


@dataclass
class CodeBlock:
    text: str
    language: str | None = None


@dataclass
class EquationBlock:
    latex: str


@dataclass(frozen=True)
class HorizontalRule:
    pass


@dataclass(frozen=True)
class PageBreak:
    pass


@dataclass(frozen=True)
class SectionBreak:
    pass


@dataclass(frozen=True)
class TableOfContents:
    pass
```

- [ ] **Step 4: Run test to verify it passes**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_blocks.py -v
```

Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/google_doc_diff/ast/nodes.py tests/unit/test_ast_blocks.py
git commit -m "add block-level AST node types"
```

---

### Task 1.7: Cross-cutting collection types (Comment, Suggestion, Footnote)

**Files:**
- Modify: `src/google_doc_diff/ast/nodes.py`
- Create: `tests/unit/test_ast_collections.py`

These live in `Document.comments` / `.suggestions` / `.footnotes`, keyed by stable ID, and are referenced from anchor nodes inside the tree.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ast_collections.py`:

```python
"""Tests for cross-cutting collection AST nodes (comments, suggestions, footnotes)."""

from datetime import datetime, timezone

from google_doc_diff.ast.nodes import (
    Comment,
    CommentReply,
    Footnote,
    Paragraph,
    Run,
    Suggestion,
)


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_comment_holds_thread_and_quoted_text():
    c = Comment(
        comment_id="c-AAA1",
        author="alice@example.com",
        created_time=utc(2026, 5, 1, 12, 0),
        modified_time=utc(2026, 5, 1, 12, 0),
        content="needs the auth section",
        quoted_text="unfinished",
        resolved=False,
        deleted=False,
        replies=[
            CommentReply(
                reply_id="r-1",
                author="bob@example.com",
                created_time=utc(2026, 5, 2),
                modified_time=utc(2026, 5, 2),
                content="agreed",
                action=None,
            ),
            CommentReply(
                reply_id="r-2",
                author="alice@example.com",
                created_time=utc(2026, 5, 3),
                modified_time=utc(2026, 5, 3),
                content="done",
                action="resolve",
            ),
        ],
    )
    assert c.comment_id == "c-AAA1"
    assert c.quoted_text == "unfinished"
    assert len(c.replies) == 2
    assert c.replies[1].action == "resolve"


def test_suggestion_kinds():
    s_ins = Suggestion(
        suggestion_id="s-1", author="a@x", created_time=utc(2026, 5, 2),
        kind="insertion", attached_comment_id=None,
    )
    s_del = Suggestion(
        suggestion_id="s-2", author="a@x", created_time=utc(2026, 5, 2),
        kind="deletion", attached_comment_id=None,
    )
    s_repl = Suggestion(
        suggestion_id="s-3", author="a@x", created_time=utc(2026, 5, 2),
        kind="replacement", attached_comment_id="c-AAA1",
    )
    assert s_ins.kind == "insertion"
    assert s_del.kind == "deletion"
    assert s_repl.kind == "replacement"
    assert s_repl.attached_comment_id == "c-AAA1"


def test_footnote_holds_blocks():
    fn = Footnote(footnote_id="fn-1", blocks=[Paragraph(runs=[Run(text="see also")])])
    assert fn.footnote_id == "fn-1"
    assert len(fn.blocks) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_collections.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write minimal implementation**

Append to `src/google_doc_diff/ast/nodes.py` (the `datetime` import already lives at the top of the file from Task 1.4):

```python
# --- Cross-cutting collections --------------------------------------------


@dataclass
class CommentReply:
    reply_id: str
    author: str                          # email; "unknown@gdoc-diff" if missing
    created_time: datetime
    modified_time: datetime
    content: str                         # plain text; htmlContent stored only if it differs
    action: str | None = None            # None | 'resolve' | 'reopen'
    deleted: bool = False


@dataclass
class Comment:
    comment_id: str
    author: str
    created_time: datetime
    modified_time: datetime
    content: str
    quoted_text: str = ""                # 'quotedFileContent.value' from API
    resolved: bool = False
    deleted: bool = False
    replies: list[CommentReply] = field(default_factory=list)
    orphaned: bool = False               # set by replay re-anchorer when not found


@dataclass
class Suggestion:
    suggestion_id: str
    author: str
    created_time: datetime
    kind: str                            # 'insertion' | 'deletion' | 'replacement'
    attached_comment_id: str | None = None


@dataclass
class Footnote:
    footnote_id: str
    blocks: list = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_collections.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/google_doc_diff/ast/nodes.py tests/unit/test_ast_collections.py
git commit -m "add Comment, Suggestion, Footnote collection types"
```

---

### Task 1.8: AST top-level Tab and Document

**Files:**
- Modify: `src/google_doc_diff/ast/nodes.py`
- Create: `tests/unit/test_ast_document.py`

The roots of the tree.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ast_document.py`:

```python
"""Tests for Tab and Document AST roots."""

from datetime import datetime, timezone

from google_doc_diff.ast.nodes import (
    Comment,
    Document,
    Footnote,
    Heading,
    Paragraph,
    Run,
    Suggestion,
    Tab,
)


def test_tab_has_id_title_level_blocks_and_optional_children():
    inner = Tab(tab_id="t-c1", title="child", level=1, parent_tab_id="t-p", blocks=[])
    outer = Tab(tab_id="t-p", title="parent", level=0, parent_tab_id=None,
                children=[inner], blocks=[Heading(level=1, runs=[Run(text="X")])])
    assert outer.children[0].tab_id == "t-c1"
    assert outer.blocks[0].level == 1


def test_document_minimal_construction():
    d = Document(
        doc_id="1aBc",
        title="My Doc",
        revision_id="rev-1",
        drive_url="https://docs.google.com/document/d/1aBc/edit",
        captured_at=datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc),
        schema_version=1,
        last_modifying_user="alice@example.com",
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=True,
        tabs=[Tab(tab_id="t-1", title="(default)", level=0, blocks=[
            Paragraph(runs=[Run(text="hi")])
        ])],
    )
    assert d.doc_id == "1aBc"
    assert d.tabs[0].blocks[0].runs[0].text == "hi"
    assert d.comments == {}                 # default empty
    assert d.suggestions == {}
    assert d.footnotes == {}
    assert d.css_classes == {}


def test_document_collections_can_be_populated():
    d = Document(
        doc_id="x", title="t", revision_id="r", drive_url="u",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        schema_version=1, last_modifying_user=None,
        source_mode="pull", comments_preserved=True, suggestions_preserved=True,
        tabs=[],
    )
    d.comments["c-1"] = Comment(
        comment_id="c-1", author="a@x",
        created_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        content="x",
    )
    assert "c-1" in d.comments
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_document.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write minimal implementation**

Append to `src/google_doc_diff/ast/nodes.py`:

```python
# --- Tab and Document -----------------------------------------------------


@dataclass
class Tab:
    tab_id: str
    title: str
    level: int                           # 0 = top-level
    parent_tab_id: str | None = None
    children: list["Tab"] = field(default_factory=list)
    blocks: list = field(default_factory=list)


@dataclass
class Document:
    doc_id: str
    title: str
    revision_id: str
    drive_url: str
    captured_at: datetime
    schema_version: int
    last_modifying_user: str | None
    source_mode: str                      # 'pull' | 'replay'
    comments_preserved: bool
    suggestions_preserved: bool
    tabs: list[Tab] = field(default_factory=list)
    comments: dict[str, "Comment"] = field(default_factory=dict)
    suggestions: dict[str, "Suggestion"] = field(default_factory=dict)
    footnotes: dict[str, "Footnote"] = field(default_factory=dict)
    # The next three are typed as dict[str, dict] in v1 (raw API blobs).
    # The spec describes them with named types (StyleDescriptor, ListDescriptor,
    # InlineObject) but those richer types aren't needed until styles/classes.py
    # in Chunk 2 (named_styles), the list emitter (list_definitions), and the
    # image extractor (inline_objects) need to read them. When that work lands,
    # tighten these to typed dataclasses and update tests. See the spec's
    # "Top-level" subsection of "Data Model".
    named_styles: dict[str, dict] = field(default_factory=dict)
    list_definitions: dict[str, dict] = field(default_factory=dict)
    inline_objects: dict[str, dict] = field(default_factory=dict)
    css_classes: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_document.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/google_doc_diff/ast/nodes.py tests/unit/test_ast_document.py
git commit -m "add Document and Tab AST roots"
```

---

### Task 1.9: Public re-exports + AST module smoke test

**Files:**
- Modify: `src/google_doc_diff/ast/__init__.py`
- Create: `tests/unit/test_ast_imports.py`

Surface a single import surface so the rest of the codebase can `from google_doc_diff.ast import Document, Tab, Heading, …` rather than reaching into `nodes`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ast_imports.py`:

```python
"""Verify the public AST import surface exposes every node type."""

import pytest


@pytest.mark.parametrize("name", [
    # Top-level
    "Document", "Tab",
    # Blocks
    "Heading", "Paragraph", "ListItem", "Table", "Row", "Cell",
    "Image", "CodeBlock", "EquationBlock",
    "HorizontalRule", "PageBreak", "SectionBreak", "TableOfContents",
    # Inline
    "Run", "StyleDescriptor",
    "CommentAnchor", "SuggestionIns", "SuggestionDel",
    "FootnoteRef", "BookmarkAnchor", "NamedRangeAnchor",
    "SmartChip", "InlineEquation", "LineBreak",
    "Unsupported",
    # Cross-cutting collections
    "Comment", "CommentReply", "Suggestion", "Footnote",
])
def test_public_export(name):
    import google_doc_diff.ast as ast
    assert hasattr(ast, name), f"google_doc_diff.ast must export {name}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_imports.py -v
```

Expected: all 32 parameterized cases FAIL with `AssertionError: google_doc_diff.ast must export <name>` — the `ast` package's `__init__.py` is currently empty (just a docstring).

- [ ] **Step 3: Write minimal implementation**

Replace `src/google_doc_diff/ast/__init__.py` with:

```python
"""Public AST surface — re-exports from .nodes."""

from google_doc_diff.ast.nodes import (
    BookmarkAnchor,
    Cell,
    CodeBlock,
    Comment,
    CommentAnchor,
    CommentReply,
    Document,
    EquationBlock,
    Footnote,
    FootnoteRef,
    Heading,
    HorizontalRule,
    Image,
    InlineEquation,
    LineBreak,
    ListItem,
    NamedRangeAnchor,
    PageBreak,
    Paragraph,
    Row,
    Run,
    SectionBreak,
    SmartChip,
    StyleDescriptor,
    Suggestion,
    SuggestionDel,
    SuggestionIns,
    Tab,
    Table,
    TableOfContents,
    Unsupported,
)

__all__ = [
    "BookmarkAnchor", "Cell", "CodeBlock", "Comment", "CommentAnchor",
    "CommentReply", "Document", "EquationBlock", "Footnote", "FootnoteRef",
    "Heading", "HorizontalRule", "Image", "InlineEquation", "LineBreak",
    "ListItem", "NamedRangeAnchor", "PageBreak", "Paragraph", "Row", "Run",
    "SectionBreak", "SmartChip", "StyleDescriptor", "Suggestion",
    "SuggestionDel", "SuggestionIns", "Tab", "Table", "TableOfContents",
    "Unsupported",
]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
source .venv/bin/activate && python -m pytest tests/unit/test_ast_imports.py -v
```

Expected: every parameterized case PASSES.

- [ ] **Step 5: Run the full test suite to make sure nothing regressed**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all tests pass — exit code 0, no failures or errors. (The exact count grows each task; just verify everything is green.)

- [ ] **Step 6: Commit**

```bash
git add src/google_doc_diff/ast/__init__.py tests/unit/test_ast_imports.py
git commit -m "expose public AST import surface via ast/__init__.py"
```

---

### Task 1.10: AST module docstring + license headers (cleanup)

**Files:**
- Modify: `src/google_doc_diff/__init__.py`
- Create: `LICENSE`

Per `~/.claude/CLAUDE.md`: AGPL-3.0-or-later.

- [ ] **Step 1: Write the LICENSE file**

Copy the AGPL-3.0 text from a known-good local source rather than fetching from the network (deterministic; doesn't depend on gnu.org availability).

```bash
\cp /Users/danny/Public/src/dob/scrawl2org/LICENSE ./LICENSE
head -3 LICENSE
wc -l LICENSE
```

Expected:

```
                    GNU AFFERO GENERAL PUBLIC LICENSE
                       Version 3, 19 November 2007

     661 LICENSE
```

(If the local source is missing or differs, stop and ask the user to point you at a known-good AGPL-3.0 text — do **not** fetch from the network without their nod, since plan determinism depends on a fixed source.)

- [ ] **Step 2: Add license metadata to pyproject.toml**

Edit `pyproject.toml`'s `[project]` block, adding after `description`:

```toml
license = "AGPL-3.0-or-later"
authors = [{ name = "Danny O'Brien", email = "danny@spesh.com" }]
```

- [ ] **Step 3: Update package __init__ with version + docstring**

Replace `src/google_doc_diff/__init__.py`:

```python
"""Pull Google Docs into high-fidelity Markdown and HTML.

See docs/superpowers/specs/2026-05-09-google-doc-diff-design.md for the
design spec.
"""

__version__ = "0.1.0"
```

- [ ] **Step 4: Run lint and tests**

```bash
source .venv/bin/activate && make lint && make test
```

Expected: lint clean, all tests pass.

- [ ] **Step 5: Commit**

```bash
git add LICENSE pyproject.toml src/google_doc_diff/__init__.py
git commit -m "add AGPL-3.0-or-later license and project metadata"
```

---

End of Chunk 1. After this chunk: full AST tree exists as plain dataclasses, no I/O code yet, all unit tests pass, project is committable. Next chunk wires up the styles → Markdown emitter pipeline against handcrafted ASTs.
