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

---

## Chunk 2: Styles + Markdown emitter

Goal: a CSS-class generator + a Markdown serializer that round-trip-readiness-tests pass on handcrafted ASTs covering all node types. No I/O yet — the emitter takes a `Document`, returns a string.

### Task 2.1: `styles/classes.py` — class name derivation

**Files:** `src/google_doc_diff/styles/classes.py`, `tests/unit/test_classes.py`

Functions:
- `named_paragraph_class(named_style_type: str) -> str` — maps `HEADING_1`→`gd-heading-1`, `TITLE`→`gd-title`, etc.
- `synthesize_inline_class(descriptor: StyleDescriptor) -> str | None` — returns `gd-style-{hash8}` from `hashlib.sha256(repr(descriptor).encode()).hexdigest()[:8]`; returns `None` for an empty descriptor (no class needed).
- `list_class_for(list_id: str) -> str` — `gd-list-{first 4 hex chars of sha256}`.

Tests assert: stable class names across calls (determinism), empty descriptor → no class, two distinct descriptors → distinct hashes (collision check skipped for now), full set of named-style types covered.

**TDD steps per @superpowers:test-driven-development**: write the failing test, run, implement, run, commit. One commit per function.

### Task 2.2: `styles/css.py` — CSS rule generation

**Files:** `src/google_doc_diff/styles/css.py`, `tests/unit/test_css.py`

Functions:
- `descriptor_to_css(d: StyleDescriptor) -> str` — generates `font-weight: 700; color: #...; ...` body content from a StyleDescriptor.
- `paired_named_rule(tag: str, class_name: str, body: str) -> str` — generates `tag, .class { body }`.
- `build_css(doc: Document) -> str` — walks `doc.css_classes` plus the named-style table (Heading 1..6, Title, Subtitle, Normal) and returns one combined `<style>`-ready string.

Tests assert: `bold=True` → `font-weight: 700`, font/size/color emit correctly, missing fields are omitted entirely (not `font-family: None`).

### Task 2.3: `emit/markdown.py` — basic blocks (headings, paragraphs, lists)

**Files:** `src/google_doc_diff/emit/markdown.py`, `tests/unit/test_emit_md_blocks.py`

Functions:
- `emit_paragraph(p: Paragraph, doc: Document) -> str`
- `emit_heading(h: Heading, doc: Document) -> str` — `# foo` (bare) unless `h.classes` non-empty, then `# foo {.class1 .class2}`.
- `emit_list(items: list[ListItem], doc: Document) -> str` — collapse adjacent same-list_id items; `- ` for bulleted, `1. ` for ordered (Pandoc auto-numbers).
- `emit_run(r: Run, doc: Document) -> str` — text with **bold**, *italic*, ~~strike~~, `code`, [link](url), bracketed-span `[text]{.gd-style-XX}` for inline overrides.

Tests use handcrafted ASTs.

### Task 2.4: `emit/markdown.py` — tables

Add `emit_table(t: Table, doc: Document) -> str`.

Try Pandoc pipe table first; if any cell has `colspan>1` or `rowspan>1`, render the whole table as raw HTML.

Tests cover both code paths.

### Task 2.5: `emit/markdown.py` — tabs, frontmatter, document root

Add:
- `emit_tab(t: Tab, doc: Document, depth: int = 0) -> str` — `:::` count = `3 + depth`.
- `emit_frontmatter(doc: Document) -> str` — YAML block with all fields from spec.
- `emit_document(doc: Document) -> str` — frontmatter + `<style>` raw HTML block + tab fenced divs.

When a doc has exactly one synthesized tab and that tab has no title (or title is "(default)"), the emitter SKIPS the tab fenced div and emits blocks directly. Single-tab docs stay clean.

Tests cover: multi-tab, single-tab degenerate, nested tabs (depth 2).

### Task 2.6: `emit/markdown.py` — comments + suggestions + footnotes

Add the comment / suggestion / footnote handling per the spec:

- Inline comment anchors: short comments (single-graf, no replies) → `^[…]` inline note. Longer / threaded → reference-style `[^c-…]` with definition placed at end of containing tab (or end of doc).
- Suggestions: `{++…++}[^s-…]` and `{--…--}[^s-…]`. Replacement (paired ins+del with same suggestion_id at adjacent positions) → `{~~old~>new~~}[^s-…]`.
- Footnotes: `[^fn-…]` with body emitted at end of containing tab.

Footnote definitions are sorted alphabetically by ID for determinism.

Tests cover each form.

### Task 2.7: Structural attribute audit

**Files:** `src/google_doc_diff/emit/audit.py`, `tests/round_trip/test_md_audit.py`

`audit_md_output(ast: Document, md: str) -> list[str]` — walks the AST, returns a list of missing-attribute violations. Empty list = pass.

Checks: every `comment_id`, `suggestion_id`, `footnote_id`, `tab_id`, `bookmark_id`, `image_id`, every synthesized class, every named-style class on Title/Subtitle/etc. appears in the markdown output.

Tests: build a fixture-rich AST, emit MD, audit returns `[]`. Build the same AST minus a comment ID, audit returns `["missing comment_id c-XYZ"]`.

### Task 2.8: Determinism test

`tests/round_trip/test_md_determinism.py` — emit the same fixture AST twice; `assert md1 == md2` byte-for-byte.

### Task 2.9: Public emit surface

`src/google_doc_diff/emit/__init__.py` re-exports `emit_document_md` (the only public function consumers need).

After Chunk 2: `emit_document_md(ast)` produces deterministic, attribute-complete Markdown for any AST. No I/O. No HTML yet.

---

## Chunk 3: HTML emitter

Goal: parallel HTML serializer producing semantic HTML. Cross-emitter test asserts both serializers preserve the same set of stable IDs.

### Task 3.1: `emit/html.py` — basic blocks

**Files:** `src/google_doc_diff/emit/html.py`, `tests/unit/test_emit_html_blocks.py`

Functions paralleling Markdown ones but emitting HTML: `<h1>`, `<p>`, `<ul>`/`<ol>`/`<li>`, `<a>`, `<span>`, `<strong>`/`<em>`/`<s>`/`<code>`. Attributes always alphabetized.

### Task 3.2: `emit/html.py` — tables

`<table>` / `<thead>` / `<tbody>` / `<tr>` / `<td>` with `colspan` / `rowspan` attributes when > 1.

### Task 3.3: `emit/html.py` — tabs, comments, suggestions, footnotes, document

- Tabs: `<section class="gd-tab" data-tab-id="..." data-title="..." data-level="N">` with nested `<section>`s for child tabs.
- Comments: anchor as `<span class="gd-cmt-anchor" data-comment-id="...">`; thread emitted as `<aside class="gd-comment" id="c-...">…</aside>` at end of containing tab.
- Suggestions: `<ins data-suggestion-id="s-..." data-author="..." data-created="...">` and `<del>`. Replacement → both with same `data-suggestion-id`.
- Footnotes: `<sup><a href="#fn-...">n</a></sup>` in prose; `<aside class="gd-footnote" id="fn-...">…</aside>` at end.
- `emit_document_html(doc) -> str` returns full HTML document with `<head>`, `<title>`, `<meta>` tags mirroring the YAML frontmatter, `<style>` block from `styles/css.py`, then `<body>`.

### Task 3.4: Structural attribute audit (HTML side)

`audit_html_output(ast, html) -> list[str]` — same checks as Markdown audit.

`tests/round_trip/test_html_audit.py` mirrors the MD audit tests.

### Task 3.5: ID-set equality across emitters

`tests/round_trip/test_id_parity.py` — for each fixture AST, extract the set of `c-`, `s-`, `fn-`, `t-`, `bm-`, `i-` IDs from both the MD output and the HTML output; assert the sets are equal. Catches "we emitted the comment in MD but not HTML" bugs.

### Task 3.6: Determinism test (HTML side) + public surface

Mirrors Task 2.8/2.9.

After Chunk 3: both emitters work end-to-end against handcrafted ASTs, both pass attribute audits, both produce identical ID sets.

---

## Chunk 4: Auth + API + Docs JSON → AST builder

Goal: actually fetch a Google Doc and turn it into an AST. After this chunk, an internal Python entry point can `pull(doc_id) -> Document`.

### Task 4.1: `auth.py` — credentials and token storage

**Files:** `src/google_doc_diff/auth.py`, `tests/unit/test_auth.py`

Functions:
- `load_credentials(creds_path: Path | None = None, token_path: Path | None = None) -> Credentials` — reads `~/.config/gdoc-diff/credentials.json` (OAuth client) and `~/.config/gdoc-diff/token.json` (refresh token), refreshes the access token, returns `google.oauth2.credentials.Credentials`. Raises `AuthError` (custom exception) with a clear message if either file is missing.
- `run_oauth_flow(creds_path: Path) -> None` — runs `InstalledAppFlow.run_local_server(port=0)` against `creds_path`, writes the resulting refresh token to `token_path`.
- `import_gog_token(gog_token_path: Path, gog_creds_path: Path, out_path: Path | None = None) -> None` — convenience for users with `gog` already configured: reads the gog-format token + client creds, writes a Google-format authorized-user JSON to `~/.config/gdoc-diff/token.json`.

Tests: use temp directories for creds/token files; mock the OAuth flow; assert auth errors message is informative.

### Task 4.2: `api.py` — base wrapper with backoff

**Files:** `src/google_doc_diff/api.py`, `tests/unit/test_api.py`

`class GdocAPI(creds: Credentials)`:
- Builds Drive v2, Drive v3, and Docs v1 service handles.
- `_with_backoff(callable, *args, **kwargs)` — wraps any API call with exponential-backoff-with-jitter on 429 (retries: 1s, 2s, 4s, 8s, max 60s, up to 5 attempts).
- `get_document(doc_id)` — Docs v1 `documents().get(documentId=doc_id, includeTabsContent=True)`; returns the JSON.
- `list_revisions(doc_id)` — Drive v2 `revisions().list(...)` with the `fields` projection from the spec; auto-paginated.
- `fetch_revision_export(export_url) -> bytes` — raw `requests.get` with `Authorization: Bearer` header; backoff on 429.
- `list_comments(doc_id)` — Drive v3 `comments().list(...)` with `fields=comments(...,replies(...))`.

Tests use `responses` library or mock the underlying service handles; assert backoff retries on 429.

### Task 4.3: `ast/from_docs_json.py` — AST builder for current revision

**Files:** `src/google_doc_diff/ast/from_docs_json.py`, `tests/unit/test_from_docs_json.py`

`build_document(docs_json: dict, comments_json: list[dict]) -> Document` — walks the Docs API response and the Drive Comments response, produces a Document.

Subroutines:
- `_walk_tabs(tabs_list) -> list[Tab]` — recursive (tabs nest).
- `_walk_body(body) -> list[Block]` — paragraph, table, sectionBreak, tableOfContents.
- `_walk_paragraph(p, doc_styles) -> Paragraph | Heading | ListItem` — branches on `paragraphStyle.namedStyleType` and `bullet` presence.
- `_walk_runs(elements, suggestion_id_map) -> list[Run | inline_node]` — handles textRun, inlineObjectElement, person, richLink, footnoteReference, equation, columnBreak (→ LineBreak), pageBreak (only inside paragraphs, otherwise treated as block).
- `_extract_comments(comments_json) -> dict[str, Comment]` — map Drive Comments to our Comment objects, including replies + `quotedFileContent.value`.
- `_extract_suggestions(docs_json) -> dict[str, Suggestion]` — walk `suggestedInsertions`/`suggestedDeletions` IDs across paragraphs; pair adjacent ones with same ID into `kind=replacement`.
- `_extract_footnotes(docs_json) -> dict[str, Footnote]` — top-level `footnotes` map.
- `_named_styles_from(docs_json) -> dict[str, dict]` — walk `namedStyles.styles` and capture per-style descriptors.

Tests use captured Docs JSON fixtures (commit one or two small examples; capture-fixture target adds more later).

### Task 4.4: AST builder integration test (against fixture)

Use one captured Docs JSON from `tests/fixtures/docs/` covering: title, headings, paragraphs, list, table, comment, footnote. Assert `build_document(json, comments) -> Document` produces the expected node tree (compare via dataclasses.asdict equality).

---

## Chunk 5: pull / diff / revisions / auth CLI commands

Goal: a working `gdoc` binary that does end-to-end pulls of real Docs.

### Task 5.1: `cli.py` — `auth login` / `logout` / `status`

Wire `auth.py` functions into a click subgroup `gdoc auth`. Tests use Click's `CliRunner` + temp dirs.

### Task 5.2: `cli.py` — `pull` command

```
gdoc pull <doc-id-or-url> [--out PATH] [--extract-assets] [--revision REV_ID | --at ISO_TIME] [--color=auto|always|never]
```

For current revision: API.get_document → API.list_comments → from_docs_json.build_document → emit_document_md → write file.

For `--revision`/`--at`: API.list_revisions → resolve to a revision id → API.fetch_revision_export(text/markdown URL) → from_google_md.build_document → emit.

`--extract-assets`: download every `Image.src` (Drive URL) into `<slug>.assets/<image_id>.<ext>`, rewrite `Image.src` to `<slug>.assets/<filename>`. If not set, scan AST for any `Image` and print the warning to stderr.

Output path defaults to `<slugify(title)>.md` in cwd.

Tests use mocked API + a fixture doc.

### Task 5.3: `cli.py` — `revisions` command

Lists revisions in table or JSON. Uses `API.list_revisions`. Format: `id  modifiedDate  lastModifyingUser  exportFormats`. Tests with mocked API.

### Task 5.4: `cli.py` — `diff` command

Pull current (or `--revision`); diff against local file; print colored unified diff (use `difflib.unified_diff` + color codes); exit 0/1/2.

If both files have `source_mode` frontmatter and they differ, prepend an informational warning to stderr.

### Task 5.5: URL parser helper

`api.parse_doc_id(s: str) -> str` — accepts a bare doc ID or a full Drive/Docs URL. Strip `?tab=` and other query params. Tests cover several URL shapes (with/without tabs query, with/without `/edit`).

### Task 5.6: End-to-end smoke test (skipped without creds)

`tests/e2e/test_pull_smoke.py` — gated by `os.getenv("GDOC_E2E_DOC_ID")`; if set, runs `gdoc pull <id>` against the live API and asserts the output is non-empty + parses as YAML frontmatter + valid Markdown.

After Chunk 5: `gdoc pull <doc-id>` works against a real Doc and writes a usable `.md` file.

---

## Chunk 6 (partial): `from_google_md.py` parser

Goal: lossy AST builder for Google's native markdown export. Used by `pull --revision` (the historical-revision path). **Replay components are deferred per user instruction — they need a heavily-edited test doc.**

### Task 6.1: `ast/from_google_md.py`

**Files:** `src/google_doc_diff/ast/from_google_md.py`, `tests/unit/test_from_google_md.py`

`build_from_google_md(md: str, *, doc_id: str, revision_id: str, captured_at: datetime, drive_url: str, last_modifying_user: str | None) -> Document`.

Approach: use `markdown-it-py` for parsing (add to deps), walk the token stream, map tokens to AST nodes:
- `heading_open` h1..h6 → Heading
- `paragraph_open` → Paragraph
- `bullet_list_open`/`ordered_list_open` → ListItem stream
- `table_open` → Table
- `text` / `strong` / `em` / `s` / `code_inline` / `link` → Run nodes
- `softbreak` → LineBreak
- `inline html` → preserved as text (no try-to-parse)

What's lost: comments (always empty), suggestions (always empty), footnotes (none in Google's MD export), tab structure (flattened), structural anchors. We populate `Document` with `comments_preserved=False`, `suggestions_preserved=False`, `source_mode='replay'` (this builder is only used by the historical-revision path; current-revision path uses `from_docs_json` which sets `source_mode='pull'`). Actually — pull --revision is still a pull, not a replay. Reframe: the source_mode is determined by the CLI command, not the builder; pass it in as a parameter.

Tests: hand-author a small Google-style markdown, build, assert tree shape.

### (Tasks 6.2–6.5: replay components — deferred)

Per user instruction, deferred to a later session with a heavily-edited test doc:
- `replay/timeline.py` — event-merger + sorter
- `replay/reanchor.py` — re-anchor comments to historical prose via `quotedFileContent`
- `replay/runner.py` — for-each-event executor
- `cli.py replay` subcommand
- `.gdoc-replay-state.json` schema enforcement

`git.py` — even the wrapper is deferred because its only consumer is replay.

---

## Chunk 7: Stubs, canary, README

### Task 7.1: v2 round-trip parser stubs

**Files:** `src/google_doc_diff/parse/markdown.py`, `src/google_doc_diff/parse/html.py`, `tests/round_trip/test_parse_stubs.py`

```python
def parse_markdown(md: str) -> "Document":
    """v2 round-trip parser. Not implemented in v1."""
    raise NotImplementedError(
        "Markdown round-trip parser is v2 work. "
        "v1 enforces round-trip readiness via structural attribute audit; "
        "see docs/superpowers/specs/2026-05-09-google-doc-diff-design.md."
    )
```

Test asserts `pytest.raises(NotImplementedError)` and message includes "v2".

### Task 7.2: Canary entry point

**Files:** `src/google_doc_diff/canary.py`, `Makefile` target

`python -m google_doc_diff.canary`:
1. Check creds + token files exist; if not, print `skip: no credentials configured` and exit 0.
2. Verify Drive v2 `revisions.list` returns `exportLinks` with `text/html` and `text/markdown` keys for `GDOC_CANARY_DOC_ID`.
3. Verify Drive v3 `comments.list` returns at least an empty list (we just want the call to succeed).
4. Print `canary OK` and exit 0; non-zero on real failure.

Add `make canary` target.

### Task 7.3: README

**Files:** `README.md`

Sections:
- What it does (one paragraph)
- Setup: enable Drive + Docs APIs in a Google Cloud project, download `credentials.json`, run `gdoc auth login`. Alternative: `gdoc auth login --import-gog-token` for users who already have `gog` set up.
- Required scopes table
- Commands cheatsheet
- v1 limitations (no replay yet, no push, sparse historical revisions, etc.)
- Pointer to spec for design rationale

### Task 7.4: Final integration: end-to-end pull of the example doc

Run `gdoc pull 1IE_3Fz_0NKiIO0c97W4vpHDl4b1x9t7CsZ3s8BedUf4 --out /tmp/example.md` and inspect the output. Compare to `tests/fixtures/exampledoc/ComprehensiveDigitalProjectManagementGuide.html` (manually). Iterate on emitter for any obvious badness: spacing, list nesting, table rendering, etc.

After Chunk 7: v1 ships. Replay can land in a follow-up.

---

**End of plan.** Replay (timeline merger, re-anchor, runner, git wrapper, `gdoc replay` CLI, state file) is intentionally deferred — it requires a doc with substantial edit history to test meaningfully, which the user will provide in a later session.

