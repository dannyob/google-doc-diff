# Kix Enrichment Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional read-side enrichment layer that uses Chrome cookies to decorate the existing Docs API AST with details the API omits (exact comment anchors, voting chip voters, suggestion colors).

**Architecture:** OAuth remains the primary read/write path. A new `kix/` subpackage loads Chrome cookies, fetches the doc's `/edit` page, extracts the OT op stream, and runs a post-processing decorator on the existing AST. When cookies are unavailable, the pipeline produces the same output as today.

**Tech Stack:** Python 3.11+, browser-cookie3 (optional dependency), requests, existing AST node types.

**Working directory:** `.claude/worktrees/round-trip/` (branch `worktree-round-trip`)

---

## File structure

| File | Responsibility |
|------|---------------|
| Create: `src/google_doc_diff/kix/__init__.py` | Package init, re-exports `kix_available`, `load_kix_session`, `enrich_from_kix` |
| Create: `src/google_doc_diff/kix/auth.py` | `KixSession` dataclass, cookie loading, `/edit` fetch, `info_params`/role scraping |
| Create: `src/google_doc_diff/kix/model.py` | `KixModel` dataclass, `DOCS_modelChunk` JSON extraction from HTML |
| Create: `src/google_doc_diff/kix/enrich.py` | `enrich_from_kix()` decorator + three sub-enrichments |
| Modify: `pyproject.toml` | Add `[project.optional-dependencies] kix` with `browser-cookie3` |
| Modify: `src/google_doc_diff/cli.py` | Add `--kix-cookies`, `--kix-profile`, `--no-kix` to `pull`; call enrichment |
| Modify: `src/google_doc_diff/ast/nodes.py:325` | Add `color` field to `Suggestion` dataclass |
| Create: `tests/kix/__init__.py` | Test package init |
| Create: `tests/kix/test_model.py` | Tests for OT op extraction from HTML fixtures |
| Create: `tests/kix/test_auth.py` | Tests for cookie source resolution (mocked filesystem) |
| Create: `tests/kix/test_enrich.py` | Tests for each sub-enrichment with fixture OT ops |
| Create: `tests/kix/test_cli_kix.py` | Tests for CLI flag parsing and enrichment integration |

---

### Task 1: Add `browser-cookie3` optional dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the `kix` optional-dependencies group to pyproject.toml**

In `pyproject.toml`, add a `kix` extra after the existing `dev` extra:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8",
    "ruff>=0.5",
    "build",
    "twine",
    "coverage",
]
kix = [
    "browser-cookie3>=0.20.1",
]
```

- [ ] **Step 2: Install the new extra in the worktree venv**

Run:
```bash
cd .claude/worktrees/round-trip && uv pip install -e ".[dev,kix]"
```

Expected: installs `browser-cookie3` and its dependencies.

- [ ] **Step 3: Verify the import works**

Run:
```bash
cd .claude/worktrees/round-trip && python -c "import browser_cookie3; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add browser-cookie3 as optional [kix] dependency"
```

---

### Task 2: `kix/model.py` — OT op extraction from HTML

This is a pure-data module with no network or cookie dependency, so it's testable first.

**Files:**
- Create: `src/google_doc_diff/kix/__init__.py`
- Create: `src/google_doc_diff/kix/model.py`
- Create: `tests/kix/__init__.py`
- Create: `tests/kix/test_model.py`

- [ ] **Step 1: Create `kix/__init__.py` package init**

```python
"""Kix enrichment layer — optional read-side decoration via Chrome cookies."""
```

- [ ] **Step 2: Create `tests/kix/__init__.py`**

Empty file.

- [ ] **Step 3: Write failing tests for `extract_ot_ops`**

Create `tests/kix/test_model.py`:

```python
"""Tests for kix.model — OT op extraction from /edit HTML."""

from google_doc_diff.kix.model import KixModel, extract_ot_ops


MINIMAL_HTML = """
<html><head></head><body>
<script nonce="abc">
DOCS_modelChunk = {"chunk":[{"ty":"mkch","d":[[1,"Test Doc"]]},{"ty":"is","ibi":1,"s":"Hello"},{"ty":"umv","mv":42}],"revision":1};
</script>
</body></html>
"""

NO_CHUNK_HTML = """
<html><head></head><body>
<p>Sign in to continue</p>
</body></html>
"""

NESTED_BRACES_HTML = """
<html><body>
<script>
DOCS_modelChunk = {"chunk":[{"ty":"as","st":"text","si":0,"ei":5,"sm":{"ts_fgc2":{"hclr_color":"#000000","clr_type":0}}}],"revision":3,"suggestionColors":{"suggest.x":"#ff0000"}};
</script>
</body></html>
"""


def test_extract_minimal():
    model = extract_ot_ops(MINIMAL_HTML)
    assert model is not None
    assert isinstance(model, KixModel)
    assert model.revision == 1
    assert model.model_version == 42
    assert len(model.ops) == 3
    assert model.ops[0]["ty"] == "mkch"
    assert model.ops[1]["ty"] == "is"
    assert model.ops[1]["s"] == "Hello"


def test_extract_no_chunk_returns_none():
    model = extract_ot_ops(NO_CHUNK_HTML)
    assert model is None


def test_extract_nested_braces():
    model = extract_ot_ops(NESTED_BRACES_HTML)
    assert model is not None
    assert model.revision == 3
    assert model.ops[0]["ty"] == "as"
    assert model.ops[0]["sm"]["ts_fgc2"]["hclr_color"] == "#000000"
    assert model.suggestion_colors == {"suggest.x": "#ff0000"}


def test_model_version_absent_defaults_to_revision():
    html = """
    <script>
    DOCS_modelChunk = {"chunk":[{"ty":"is","ibi":1,"s":"x"}],"revision":7};
    </script>
    """
    model = extract_ot_ops(html)
    assert model is not None
    assert model.model_version == 7
```

- [ ] **Step 4: Run the tests to confirm they fail**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_model.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'google_doc_diff.kix.model'`

- [ ] **Step 5: Implement `kix/model.py`**

Create `src/google_doc_diff/kix/model.py`:

```python
"""Extract the OT op stream from a Google Docs /edit HTML page."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class KixModel:
    """The document's OT op stream as extracted from the /edit bootstrap."""

    ops: list[dict]
    revision: int
    model_version: int


def extract_ot_ops(html: str) -> KixModel | None:
    """Parse DOCS_modelChunk from /edit HTML.

    Returns None if the chunk isn't found (login page, redirect, etc.).
    """
    marker = "DOCS_modelChunk = "
    idx = html.find(marker)
    if idx < 0:
        return None
    start = html.find("{", idx)
    if start < 0:
        return None
    end = _find_closing_brace(html, start)
    if end < 0:
        return None
    try:
        raw = json.loads(html[start:end])
    except json.JSONDecodeError:
        return None
    ops = raw.get("chunk", [])
    revision = raw.get("revision", 0)
    mv = revision
    for op in reversed(ops):
        if op.get("ty") == "umv":
            mv = op.get("mv", revision)
            break
    return KixModel(ops=ops, revision=revision, model_version=mv)


def _find_closing_brace(s: str, start: int) -> int:
    """Bracket-match from an opening brace, respecting JSON string escapes."""
    depth = 0
    in_str = False
    esc = False
    for k in range(start, len(s)):
        c = s[k]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if in_str:
            if c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k + 1
    return -1
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_model.py -v`

Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add src/google_doc_diff/kix/__init__.py src/google_doc_diff/kix/model.py tests/kix/__init__.py tests/kix/test_model.py
git commit -m "kix: add model.py — extract OT ops from /edit HTML"
```

---

### Task 3: `kix/auth.py` — cookie loading and session establishment

**Files:**
- Create: `src/google_doc_diff/kix/auth.py`
- Create: `tests/kix/test_auth.py`

- [ ] **Step 1: Write failing tests for cookie source resolution**

Create `tests/kix/test_auth.py`:

```python
"""Tests for kix.auth — cookie resolution and session loading."""

import os
from pathlib import Path
from unittest.mock import patch

from google_doc_diff.kix.auth import resolve_cookie_path


def test_explicit_path_env(tmp_path):
    cookies = tmp_path / "Cookies"
    cookies.write_bytes(b"")
    with patch.dict(os.environ, {"GDOC_KIX_COOKIES": str(cookies)}):
        assert resolve_cookie_path() == cookies


def test_explicit_path_kwarg(tmp_path):
    cookies = tmp_path / "Cookies"
    cookies.write_bytes(b"")
    assert resolve_cookie_path(cookie_path=str(cookies)) == cookies


def test_kwarg_overrides_env(tmp_path):
    env_cookies = tmp_path / "env" / "Cookies"
    env_cookies.parent.mkdir()
    env_cookies.write_bytes(b"")
    kwarg_cookies = tmp_path / "kwarg" / "Cookies"
    kwarg_cookies.parent.mkdir()
    kwarg_cookies.write_bytes(b"")
    with patch.dict(os.environ, {"GDOC_KIX_COOKIES": str(env_cookies)}):
        assert resolve_cookie_path(cookie_path=str(kwarg_cookies)) == kwarg_cookies


def test_profile_name_resolves(tmp_path):
    profile_dir = tmp_path / "Profile 1"
    profile_dir.mkdir()
    cookies = profile_dir / "Cookies"
    cookies.write_bytes(b"")
    with patch("google_doc_diff.kix.auth.CHROME_ROOT", tmp_path):
        assert resolve_cookie_path(profile_name="Profile 1") == cookies


def test_profile_env(tmp_path):
    profile_dir = tmp_path / "MyProfile"
    profile_dir.mkdir()
    cookies = profile_dir / "Cookies"
    cookies.write_bytes(b"")
    with patch("google_doc_diff.kix.auth.CHROME_ROOT", tmp_path), \
         patch.dict(os.environ, {"GDOC_KIX_PROFILE": "MyProfile"}):
        assert resolve_cookie_path() == cookies


def test_auto_detect_picks_newest(tmp_path):
    old = tmp_path / "Default" / "Cookies"
    old.parent.mkdir()
    old.write_bytes(b"")
    new = tmp_path / "Profile 1" / "Cookies"
    new.parent.mkdir()
    new.write_bytes(b"")
    # Make "Profile 1" newer
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    with patch("google_doc_diff.kix.auth.CHROME_ROOT", tmp_path):
        assert resolve_cookie_path() == new


def test_no_chrome_returns_none(tmp_path):
    with patch("google_doc_diff.kix.auth.CHROME_ROOT", tmp_path):
        assert resolve_cookie_path() is None


def test_missing_explicit_path_returns_none():
    assert resolve_cookie_path(cookie_path="/nonexistent/Cookies") is None
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_auth.py -v`

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write failing tests for `scrape_info_params` and `scrape_role`**

Append to `tests/kix/test_auth.py`:

```python
from google_doc_diff.kix.auth import scrape_info_params, scrape_role


INFO_PARAMS_HTML = '''
<script>var defined = {"info_params":{"token":"AOqKD6abc:1778712727467","ouid":"123456789"}}</script>
'''


def test_scrape_info_params():
    result = scrape_info_params(INFO_PARAMS_HTML)
    assert result is not None
    assert result["token"] == "AOqKD6abc:1778712727467"
    assert result["ouid"] == "123456789"


def test_scrape_info_params_missing():
    assert scrape_info_params("<html>nothing here</html>") is None


ROLE_HTML_EDITOR = '<div data-is-doc-editor="true"></div>'
ROLE_HTML_VIEWER = '<div data-is-doc-editor="false"></div>'
ROLE_HTML_NONE = '<html>no role info</html>'
ROLE_HTML_EDIT_SCOPE = '"editingMode":"EDITING"'
ROLE_HTML_VIEW_SCOPE = '"editingMode":"VIEWING"'
ROLE_HTML_SUGGEST_SCOPE = '"editingMode":"SUGGESTING"'


def test_scrape_role_editor():
    assert scrape_role(ROLE_HTML_EDIT_SCOPE) == "editor"


def test_scrape_role_viewer():
    assert scrape_role(ROLE_HTML_VIEW_SCOPE) == "viewer"


def test_scrape_role_commenter():
    assert scrape_role(ROLE_HTML_SUGGEST_SCOPE) == "commenter"


def test_scrape_role_unknown():
    assert scrape_role(ROLE_HTML_NONE) == "unknown"
```

- [ ] **Step 4: Implement `kix/auth.py`**

Create `src/google_doc_diff/kix/auth.py`:

```python
"""Kix auth: Chrome cookie loading and /edit session establishment."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path

logger = logging.getLogger(__name__)

if sys.platform == "darwin":
    CHROME_ROOT = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
else:
    CHROME_ROOT = Path.home() / ".config" / "google-chrome"


@dataclass
class KixSession:
    """A loaded kix session with cookies, auth tokens, and cached /edit HTML."""

    jar: MozillaCookieJar
    token: str
    ouid: str
    doc_id: str
    role: str
    edit_html: str


def kix_available() -> bool:
    """True if browser-cookie3 is importable and Chrome cookies exist."""
    try:
        import browser_cookie3  # noqa: F401
    except ImportError:
        return False
    return resolve_cookie_path() is not None


def resolve_cookie_path(
    *,
    cookie_path: str | None = None,
    profile_name: str | None = None,
) -> Path | None:
    """Resolve a Chrome Cookies SQLite path.

    Priority: explicit cookie_path kwarg > GDOC_KIX_COOKIES env >
    profile_name kwarg > GDOC_KIX_PROFILE env > auto-detect newest.
    Returns None if no valid path found.
    """
    path = cookie_path or os.environ.get("GDOC_KIX_COOKIES")
    if path:
        p = Path(path)
        return p if p.is_file() else None

    profile = profile_name or os.environ.get("GDOC_KIX_PROFILE")
    if profile:
        p = CHROME_ROOT / profile / "Cookies"
        return p if p.is_file() else None

    if not CHROME_ROOT.is_dir():
        return None
    candidates = sorted(
        (d / "Cookies" for d in CHROME_ROOT.iterdir() if (d / "Cookies").is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_kix_session(
    doc_id: str,
    *,
    cookie_path: str | None = None,
    profile_name: str | None = None,
) -> KixSession | None:
    """Load cookies, fetch /edit, return a KixSession or None on failure."""
    try:
        import browser_cookie3
    except ImportError:
        logger.debug("browser-cookie3 not installed; skipping kix")
        return None

    resolved = resolve_cookie_path(cookie_path=cookie_path, profile_name=profile_name)
    if resolved is None:
        logger.debug("no Chrome Cookies file found; skipping kix")
        return None

    try:
        raw_jar = browser_cookie3.chrome(
            cookie_file=str(resolved), domain_name=".google.com",
        )
    except Exception as exc:
        logger.debug("failed to load Chrome cookies: %s", exc)
        return None

    jar = MozillaCookieJar()
    for c in raw_jar:
        jar.set_cookie(c)

    try:
        import requests
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        r = requests.get(
            url, cookies=jar, timeout=20, allow_redirects=True,
            headers={
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        r.raise_for_status()
    except Exception as exc:
        logger.debug("kix /edit fetch failed: %s", exc)
        return None

    html = r.text
    if "DOCS_modelChunk" not in html:
        logger.debug("kix /edit response has no DOCS_modelChunk; likely unauthenticated")
        return None

    ip = scrape_info_params(html)
    if ip is None:
        logger.debug("kix /edit: could not scrape info_params")
        return None

    return KixSession(
        jar=jar,
        token=ip["token"],
        ouid=ip["ouid"],
        doc_id=doc_id,
        role=scrape_role(html),
        edit_html=html,
    )


def scrape_info_params(html: str) -> dict | None:
    """Extract the token and ouid from the /edit page's info_params JSON."""
    m = re.search(r'"info_params"\s*:\s*(\{[^}]+\})', html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def scrape_role(html: str) -> str:
    """Best-effort role detection from the /edit HTML."""
    m = re.search(r'"editingMode"\s*:\s*"(\w+)"', html)
    if m:
        mode = m.group(1)
        if mode == "EDITING":
            return "editor"
        if mode == "VIEWING":
            return "viewer"
        if mode == "SUGGESTING":
            return "commenter"
    return "unknown"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_auth.py -v`

Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src/google_doc_diff/kix/auth.py tests/kix/test_auth.py
git commit -m "kix: add auth.py — cookie resolution and session loading"
```

---

### Task 4: `kix/enrich.py` — suggestion color enrichment

Start with the simplest sub-enrichment to establish the `enrich_from_kix` entry point.

**Files:**
- Create: `src/google_doc_diff/kix/enrich.py`
- Modify: `src/google_doc_diff/ast/nodes.py:325`
- Create: `tests/kix/test_enrich.py`

- [ ] **Step 1: Add `color` field to the `Suggestion` dataclass**

In `src/google_doc_diff/ast/nodes.py`, the `Suggestion` dataclass (around line 325):

```python
@dataclass
class Suggestion:
    suggestion_id: str
    author: str
    created_time: datetime
    kind: str                            # 'insertion' | 'deletion' | 'replacement'
    attached_comment_id: str | None = None
    color: str | None = None             # '#RRGGBB' from kix enrichment
```

- [ ] **Step 2: Update `KixModel` to carry `suggestion_colors`**

In `src/google_doc_diff/kix/model.py`, update the dataclass and extraction.

Change the import to `from dataclasses import dataclass, field`.

Update the dataclass:

```python
@dataclass
class KixModel:
    """The document's OT op stream as extracted from the /edit bootstrap."""

    ops: list[dict]
    revision: int
    model_version: int
    suggestion_colors: dict[str, str] = field(default_factory=dict)
```

In `extract_ot_ops`, after parsing `raw`, update the return to include suggestion colors:

```python
    suggestion_colors = raw.get("suggestionColors", {})
    return KixModel(
        ops=ops, revision=revision, model_version=mv,
        suggestion_colors=suggestion_colors,
    )
```

- [ ] **Step 3: Write failing tests for suggestion color enrichment**

Create `tests/kix/test_enrich.py`:

```python
"""Tests for kix.enrich — post-processing AST enrichment from OT ops."""

from datetime import UTC, datetime

from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    Paragraph,
    Run,
    Suggestion,
    Tab,
)
from google_doc_diff.kix.enrich import enrich_from_kix
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
        ops=ops, revision=revision, model_version=model_version,
        suggestion_colors=suggestion_colors or {},
    )


class TestSuggestionColors:
    def test_patches_color_onto_matching_suggestion(self):
        doc = _make_doc(suggestions={
            "suggest.abc123": Suggestion(
                suggestion_id="suggest.abc123",
                author="user@example.com",
                created_time=datetime.now(UTC),
                kind="insertion",
            ),
        })
        ops = [
            {"ty": "is", "ibi": 1, "s": "hello"},
            {"ty": "iss", "sugid": "suggest.abc123", "ibi": 5, "s": " world"},
        ]
        model = _make_model(ops, suggestion_colors={"suggest.abc123": "#ff9900"})

        enrich_from_kix(doc, model)
        assert doc.suggestions["suggest.abc123"].color == "#ff9900"

    def test_ignores_unknown_suggestion_ids(self):
        doc = _make_doc(suggestions={
            "suggest.abc123": Suggestion(
                suggestion_id="suggest.abc123",
                author="user@example.com",
                created_time=datetime.now(UTC),
                kind="insertion",
            ),
        })
        model = _make_model([], suggestion_colors={"suggest.unknown": "#00ff00"})

        enrich_from_kix(doc, model)
        assert doc.suggestions["suggest.abc123"].color is None

    def test_no_suggestion_colors_is_noop(self):
        doc = _make_doc()
        model = _make_model([])
        enrich_from_kix(doc, model)

    def test_multiple_suggestions_each_get_color(self):
        doc = _make_doc(suggestions={
            "suggest.a": Suggestion(
                suggestion_id="suggest.a", author="a@x.com",
                created_time=datetime.now(UTC), kind="insertion",
            ),
            "suggest.b": Suggestion(
                suggestion_id="suggest.b", author="b@x.com",
                created_time=datetime.now(UTC), kind="deletion",
            ),
        })
        model = _make_model([], suggestion_colors={
            "suggest.a": "#ff0000",
            "suggest.b": "#00ff00",
        })
        enrich_from_kix(doc, model)
        assert doc.suggestions["suggest.a"].color == "#ff0000"
        assert doc.suggestions["suggest.b"].color == "#00ff00"
```

- [ ] **Step 4: Run tests to confirm they fail**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_enrich.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'google_doc_diff.kix.enrich'`

- [ ] **Step 5: Implement `kix/enrich.py` with suggestion color enrichment**

Create `src/google_doc_diff/kix/enrich.py`:

```python
"""Post-processing enrichment: decorate an existing AST with kix OT details."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google_doc_diff.ast.nodes import Document
from google_doc_diff.kix.model import KixModel

logger = logging.getLogger(__name__)


@dataclass
class EnrichResult:
    """Summary of what the enrichment pass did."""

    suggestion_colors_applied: int = 0
    comment_anchors_resolved: int = 0
    voting_chips_enriched: int = 0


def enrich_from_kix(doc: Document, model: KixModel) -> EnrichResult:
    """Mutate doc in place with details from the OT stream."""
    result = EnrichResult()
    result.suggestion_colors_applied = _enrich_suggestion_colors(doc, model)
    return result


def _enrich_suggestion_colors(doc: Document, model: KixModel) -> int:
    """Patch suggestion colors from the kix model onto matching suggestions."""
    count = 0
    for sug_id, color in model.suggestion_colors.items():
        if sug_id in doc.suggestions:
            doc.suggestions[sug_id].color = color
            count += 1
    return count
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_enrich.py -v`

Expected: all passed.

- [ ] **Step 7: Run the full test suite to check for regressions**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/ -v --tb=short`

Expected: all existing tests still pass (the new `color` field on `Suggestion` defaults to `None`).

- [ ] **Step 8: Commit**

```bash
git add src/google_doc_diff/kix/enrich.py src/google_doc_diff/kix/model.py src/google_doc_diff/ast/nodes.py tests/kix/test_enrich.py
git commit -m "kix: suggestion color enrichment — first sub-enrichment"
```

---

### Task 5: Comment anchor enrichment

Wire the existing `kix_resolver` hook in `anchor_comments.py` to a resolver built from OT ops.

**Files:**
- Modify: `src/google_doc_diff/kix/enrich.py`
- Modify: `tests/kix/test_enrich.py`

- [ ] **Step 1: Write failing tests for comment anchor resolution**

Append to `tests/kix/test_enrich.py`:

```python
from google_doc_diff.ast.nodes import Comment, CommentAnchor
from google_doc_diff.kix.enrich import build_kix_anchor_map


class TestBuildKixAnchorMap:
    def test_maps_te_ops_to_paragraph_indices(self):
        ops = [
            {"ty": "is", "ibi": 0, "s": "First paragraph text. "},
            {"ty": "is", "ibi": 22, "s": "Second paragraph text."},
            {"ty": "te", "id": "kix.abc123", "spi": 5},
            {"ty": "te", "id": "kix.def456", "spi": 25},
        ]
        # Two paragraphs: bytes 0..21 and 22..43
        # te at spi=5 falls in first paragraph, te at spi=25 in second
        # The anchor map just records the te id -> spi offset
        anchor_map = build_kix_anchor_map(ops)
        assert anchor_map["kix.abc123"] == 5
        assert anchor_map["kix.def456"] == 25

    def test_empty_ops(self):
        assert build_kix_anchor_map([]) == {}


class TestCommentAnchorEnrichment:
    def test_enrichment_uses_kix_resolver(self):
        tab = Tab(
            tab_id="t.0", title="Tab 1", level=0,
            blocks=[
                Paragraph(runs=[Run(text="Hello world")]),
                Paragraph(runs=[Run(text="Goodbye world")]),
            ],
        )
        doc = _make_doc(
            tabs=[tab],
            comments={
                "c1": Comment(
                    comment_id="c1", author="a@x.com",
                    created_time=datetime.now(UTC),
                    modified_time=datetime.now(UTC),
                    content="Nice!", quoted_text="Hello",
                    anchor="kix.anchor1",
                ),
            },
        )
        # te op places kix.anchor1 at spi=0, which is in block 0
        ops = [
            {"ty": "is", "ibi": 0, "s": "Hello world\nGoodbye world"},
            {"ty": "te", "id": "kix.anchor1", "spi": 0},
        ]
        model = _make_model(ops)
        # suggestion_colors defaults to {} via _make_model

        result = enrich_from_kix(doc, model)
        assert result.comment_anchors_resolved >= 0
        # The comment should have been anchored (not orphaned)
        assert not doc.comments["c1"].orphaned
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_enrich.py::TestBuildKixAnchorMap -v`

Expected: FAIL — `ImportError: cannot import name 'build_kix_anchor_map'`

- [ ] **Step 3: Implement `build_kix_anchor_map` and wire into enrichment**

In `src/google_doc_diff/kix/enrich.py`, add:

```python
from google_doc_diff.ast.anchor_comments import anchor_comments


def build_kix_anchor_map(ops: list[dict]) -> dict[str, int]:
    """Build a mapping from kix anchor IDs to their byte offsets (spi) from OT ops.

    The `te` (text-element) ops carry `id` (the kix anchor) and `spi`
    (string position index) — the byte offset where the anchored element
    sits in the flattened document text.
    """
    out: dict[str, int] = {}
    for op in ops:
        if op.get("ty") == "te" and "id" in op and "spi" in op:
            out[op["id"]] = op["spi"]
    return out
```

Update `enrich_from_kix` to call the comment anchor enrichment:

```python
def enrich_from_kix(doc: Document, model: KixModel) -> EnrichResult:
    """Mutate doc in place with details from the OT stream."""
    result = EnrichResult()
    result.suggestion_colors_applied = _enrich_suggestion_colors(doc, model)
    result.comment_anchors_resolved = _enrich_comment_anchors(doc, model)
    return result


def _enrich_comment_anchors(doc: Document, model: KixModel) -> int:
    """Re-run comment anchoring using kix-derived exact positions."""
    anchor_map = build_kix_anchor_map(model.ops)
    if not anchor_map:
        return 0

    active_comments = [
        c for c in doc.comments.values()
        if not c.deleted and c.quoted_text and c.anchor
    ]
    if not active_comments:
        return 0

    def resolver(kix_anchor: str) -> int | None:
        return _spi_to_block_index(doc, anchor_map.get(kix_anchor))

    anchor_comments(doc, kix_resolver=resolver)
    resolved = sum(1 for c in active_comments if not c.orphaned)
    return resolved


def _spi_to_block_index(doc: Document, spi: int | None) -> int | None:
    """Convert a byte offset (spi) to a block index in the first tab.

    Walks the first tab's blocks, accumulating character counts, and
    returns the index of the block that contains the given offset.
    """
    if spi is None or not doc.tabs:
        return None
    from google_doc_diff.ast.nodes import Heading, ListItem, Paragraph, Run
    offset = 0
    for i, block in enumerate(doc.tabs[0].blocks):
        if not isinstance(block, (Paragraph, Heading, ListItem)):
            continue
        block_len = sum(len(r.text) for r in block.runs if isinstance(r, Run))
        block_len += 1  # newline separator
        if offset <= spi < offset + block_len:
            return i
        offset += block_len
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_enrich.py -v`

Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/google_doc_diff/kix/enrich.py tests/kix/test_enrich.py
git commit -m "kix: comment anchor enrichment via te op byte-offset mapping"
```

---

### Task 6: Voting chip enrichment

**Files:**
- Modify: `src/google_doc_diff/kix/enrich.py`
- Modify: `tests/kix/test_enrich.py`

- [ ] **Step 1: Write failing tests for voting chip enrichment**

Append to `tests/kix/test_enrich.py`:

```python
from google_doc_diff.ast.nodes import SmartChip, VotingChip, Voter


class TestVotingChipEnrichment:
    def test_enriches_smartchip_with_voting_data(self):
        tab = Tab(
            tab_id="t.0", title="Tab 1", level=0,
            blocks=[
                Paragraph(runs=[
                    Run(text="Vote here: "),
                    SmartChip(kind="voting", data={"rendered": "(➕ 2)"}, display_text="(➕ 2)"),
                ]),
            ],
        )
        doc = _make_doc(tabs=[tab])
        ops = [
            {"ty": "is", "ibi": 0, "s": "Vote here: "},
            {"ty": "ae", "et": "emoji-voting", "id": "kix.chip1", "epm": {}},
            {"ty": "te", "id": "kix.chip1", "spi": 11},
            {"ty": "nm", "nmr": ["dtvc", "kix.chip1", False],
             "nmc": ["voting-chip-populate", "➕",
                     [{"ui": {"ui_oi": "voter1"}}, {"ui": {"ui_oi": "voter2"}}],
                     True, "sig123"]},
        ]
        model = _make_model(ops)
        # suggestion_colors defaults to {} via _make_model

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
            tab_id="t.0", title="Tab 1", level=0,
            blocks=[Paragraph(runs=[Run(text="no chips")])],
        )
        doc = _make_doc(tabs=[tab])
        model = _make_model([])
        # suggestion_colors defaults to {} via _make_model

        result = enrich_from_kix(doc, model)
        assert result.voting_chips_enriched == 0

    def test_multiple_chips_in_same_paragraph(self):
        tab = Tab(
            tab_id="t.0", title="Tab 1", level=0,
            blocks=[
                Paragraph(runs=[
                    SmartChip(kind="voting", data={}, display_text="(👍 1)"),
                    Run(text=" and "),
                    SmartChip(kind="voting", data={}, display_text="(🚀 3)"),
                ]),
            ],
        )
        doc = _make_doc(tabs=[tab])
        ops = [
            {"ty": "is", "ibi": 0, "s": " and "},
            {"ty": "ae", "et": "emoji-voting", "id": "kix.c1", "epm": {}},
            {"ty": "te", "id": "kix.c1", "spi": 0},
            {"ty": "nm", "nmr": ["dtvc", "kix.c1", False],
             "nmc": ["voting-chip-populate", "👍",
                     [{"ui": {"ui_oi": "v1"}}], False, "s1"]},
            {"ty": "ae", "et": "emoji-voting", "id": "kix.c2", "epm": {}},
            {"ty": "te", "id": "kix.c2", "spi": 6},
            {"ty": "nm", "nmr": ["dtvc", "kix.c2", False],
             "nmc": ["voting-chip-populate", "🚀",
                     [{"ui": {"ui_oi": "v2"}}, {"ui": {"ui_oi": "v3"}}, {"ui": {"ui_oi": "v4"}}],
                     True, "s2"]},
        ]
        model = _make_model(ops)
        # suggestion_colors defaults to {} via _make_model

        result = enrich_from_kix(doc, model)
        assert result.voting_chips_enriched == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_enrich.py::TestVotingChipEnrichment -v`

Expected: FAIL — assertion errors (enrichment not implemented)

- [ ] **Step 3: Implement voting chip enrichment**

Add to `src/google_doc_diff/kix/enrich.py`:

```python
from google_doc_diff.ast.nodes import (
    Document,
    Heading,
    ListItem,
    Paragraph,
    Run,
    SmartChip,
    Voter,
    VotingChip,
)
```

Update `enrich_from_kix`:

```python
def enrich_from_kix(doc: Document, model: KixModel) -> EnrichResult:
    """Mutate doc in place with details from the OT stream."""
    result = EnrichResult()
    result.suggestion_colors_applied = _enrich_suggestion_colors(doc, model)
    result.comment_anchors_resolved = _enrich_comment_anchors(doc, model)
    result.voting_chips_enriched = _enrich_voting_chips(doc, model)
    return result
```

Add the implementation:

```python
def _parse_voting_chips(ops: list[dict]) -> dict[str, dict]:
    """Extract voting chip data from ae + nm ops, keyed by chip id."""
    chips: dict[str, dict] = {}
    for op in ops:
        if op.get("ty") == "ae" and op.get("et") == "emoji-voting":
            chip_id = op.get("id", "")
            chips[chip_id] = {"chip_id": chip_id}
        if op.get("ty") == "nm":
            nmr = op.get("nmr", [])
            if len(nmr) >= 2 and nmr[0] == "dtvc":
                chip_id = nmr[1]
                nmc = op.get("nmc", [])
                if len(nmc) >= 5 and nmc[0] == "voting-chip-populate":
                    chips.setdefault(chip_id, {"chip_id": chip_id})
                    chips[chip_id]["emoji"] = nmc[1]
                    chips[chip_id]["voters"] = [
                        v.get("ui", {}).get("ui_oi", "")
                        for v in (nmc[2] if isinstance(nmc[2], list) else [])
                    ]
                    chips[chip_id]["current_user_voted"] = bool(nmc[3])
                    chips[chip_id]["signature"] = nmc[4] if len(nmc) > 4 else ""
    return chips


def _enrich_voting_chips(doc: Document, model: KixModel) -> int:
    """Replace SmartChip placeholders with fully populated VotingChip nodes."""
    chip_data = _parse_voting_chips(model.ops)
    if not chip_data:
        return 0

    te_map = build_kix_anchor_map(model.ops)
    chip_by_spi: dict[int, dict] = {}
    for chip_id, data in chip_data.items():
        spi = te_map.get(chip_id)
        if spi is not None:
            chip_by_spi[spi] = data

    if not chip_by_spi:
        return 0

    count = 0
    for tab in doc.tabs:
        count += _replace_chips_in_blocks(tab.blocks, chip_by_spi)
    return count


def _replace_chips_in_blocks(blocks: list, chip_by_spi: dict[int, dict]) -> int:
    """Walk blocks, find SmartChip nodes that match voting chip positions, replace them."""
    count = 0
    chip_values = list(chip_by_spi.values())
    chip_idx = 0
    for block in blocks:
        if not isinstance(block, (Paragraph, Heading, ListItem)):
            continue
        new_runs = []
        for run in block.runs:
            if isinstance(run, SmartChip) and chip_idx < len(chip_values):
                data = chip_values[chip_idx]
                vc = VotingChip(
                    chip_id=data["chip_id"],
                    emoji=data.get("emoji", ""),
                    voters=[Voter(obfuscated_id=v) for v in data.get("voters", [])],
                    current_user_voted=data.get("current_user_voted", False),
                    signature=data.get("signature", ""),
                )
                new_runs.append(vc)
                chip_idx += 1
                count += 1
            else:
                new_runs.append(run)
        block.runs = new_runs
    return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_enrich.py -v`

Expected: all passed.

- [ ] **Step 5: Run full test suite**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/ --tb=short`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/google_doc_diff/kix/enrich.py tests/kix/test_enrich.py
git commit -m "kix: voting chip enrichment — replace SmartChip with VotingChip"
```

---

### Task 7: `kix/__init__.py` public API

**Files:**
- Modify: `src/google_doc_diff/kix/__init__.py`

- [ ] **Step 1: Write the public API re-exports**

Update `src/google_doc_diff/kix/__init__.py`:

```python
"""Kix enrichment layer — optional read-side decoration via Chrome cookies."""

from google_doc_diff.kix.auth import KixSession, kix_available, load_kix_session
from google_doc_diff.kix.enrich import EnrichResult, enrich_from_kix
from google_doc_diff.kix.model import KixModel, extract_ot_ops

__all__ = [
    "EnrichResult",
    "KixModel",
    "KixSession",
    "enrich_from_kix",
    "extract_ot_ops",
    "kix_available",
    "load_kix_session",
]
```

- [ ] **Step 2: Verify imports work**

Run:
```bash
cd .claude/worktrees/round-trip && python -c "from google_doc_diff.kix import kix_available, load_kix_session, enrich_from_kix, extract_ot_ops; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/google_doc_diff/kix/__init__.py
git commit -m "kix: public API re-exports from __init__"
```

---

### Task 8: CLI integration — wire kix enrichment into `gdoc pull`

**Files:**
- Modify: `src/google_doc_diff/cli.py`
- Create: `tests/kix/test_cli_kix.py`

- [ ] **Step 1: Write failing tests for CLI kix flags**

Create `tests/kix/test_cli_kix.py`:

```python
"""Tests for CLI kix flag parsing."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from google_doc_diff.cli import cli


@patch("google_doc_diff.cli.load_credentials")
@patch("google_doc_diff.cli.GdocAPI")
@patch("google_doc_diff.cli._pull_rich_document_with_raw")
def test_no_kix_flag_skips_enrichment(mock_pull, mock_api_cls, mock_creds):
    """--no-kix should prevent any kix loading."""
    from google_doc_diff.ast.nodes import Document, Tab
    from datetime import UTC, datetime

    doc = Document(
        doc_id="test", title="Test", revision_id="r1",
        drive_url="https://docs.google.com/document/d/test/edit",
        captured_at=datetime.now(UTC), schema_version=1,
        last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t.0", title="Tab 1", level=0, blocks=[])],
    )
    mock_pull.return_value = (doc, {})

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "pull", "test-doc-id", "--no-kix", "--out", "test.md",
        ])
        assert result.exit_code == 0


@patch("google_doc_diff.cli.load_credentials")
@patch("google_doc_diff.cli.GdocAPI")
@patch("google_doc_diff.cli._pull_rich_document_with_raw")
@patch("google_doc_diff.cli._try_kix_enrichment")
def test_kix_enrichment_called_by_default(mock_kix, mock_pull, mock_api_cls, mock_creds):
    """Without --no-kix, enrichment should be attempted."""
    from google_doc_diff.ast.nodes import Document, Tab
    from datetime import UTC, datetime

    doc = Document(
        doc_id="test", title="Test", revision_id="r1",
        drive_url="https://docs.google.com/document/d/test/edit",
        captured_at=datetime.now(UTC), schema_version=1,
        last_modifying_user=None, source_mode="pull",
        comments_preserved=True, suggestions_preserved=True,
        tabs=[Tab(tab_id="t.0", title="Tab 1", level=0, blocks=[])],
    )
    mock_pull.return_value = (doc, {})
    mock_kix.return_value = None

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "pull", "test-doc-id", "--out", "test.md",
        ])
        assert result.exit_code == 0
        mock_kix.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_cli_kix.py -v`

Expected: FAIL — `--no-kix` not recognized / `_try_kix_enrichment` doesn't exist

- [ ] **Step 3: Add kix flags and enrichment call to cli.py**

In `src/google_doc_diff/cli.py`, add the kix enrichment helper function after the `_pull_rich_document_with_raw` function:

```python
def _try_kix_enrichment(doc, doc_id, *, kix_cookies=None, kix_profile=None, verbose=False):
    """Attempt kix enrichment; return EnrichResult or None."""
    try:
        from google_doc_diff.kix import (
            enrich_from_kix,
            extract_ot_ops,
            load_kix_session,
        )
    except ImportError:
        if verbose:
            click.echo("kix enrichment: skipped (gdoc[kix] extra not installed)", err=True)
        return None

    session = load_kix_session(doc_id, cookie_path=kix_cookies, profile_name=kix_profile)
    if session is None:
        if verbose:
            click.echo("kix enrichment: skipped (no Chrome cookies available)", err=True)
        return None

    model = extract_ot_ops(session.edit_html)
    if model is None:
        if verbose:
            click.echo("kix enrichment: skipped (could not extract OT model)", err=True)
        return None

    result = enrich_from_kix(doc, model)
    if verbose:
        parts = []
        if result.suggestion_colors_applied:
            parts.append(f"suggestion colors: {result.suggestion_colors_applied}")
        if result.comment_anchors_resolved:
            parts.append(f"comment anchors: {result.comment_anchors_resolved}")
        if result.voting_chips_enriched:
            parts.append(f"voting chips: {result.voting_chips_enriched}")
        summary = ", ".join(parts) if parts else "no enrichments applied"
        click.echo(f"kix enrichment: applied ({summary})", err=True)
    return result
```

Modify the `pull` command to add the three new options and call enrichment. Add these options to the `@cli.command()` decorator chain for `pull`:

```python
@click.option("--kix-cookies", type=click.Path(path_type=Path),
              help="Path to a Chromium Cookies SQLite file for kix enrichment.")
@click.option("--kix-profile",
              help="Chrome profile name for kix enrichment (e.g. 'Profile 1').")
@click.option("--no-kix", is_flag=True,
              help="Skip kix enrichment even if Chrome cookies are available.")
@click.option("--verbose", is_flag=True, help="Print enrichment diagnostics.")
```

Update the `pull` function signature to accept these new parameters:

```python
def pull(doc, out, html_out, extract_assets, revision, chip_counts,
         kix_cookies, kix_profile, no_kix, verbose):
```

After the `_pull_rich_document_with_raw` call and before `md = emit_document_md(document)`, insert:

```python
    if not no_kix:
        _try_kix_enrichment(
            document, doc_id,
            kix_cookies=str(kix_cookies) if kix_cookies else None,
            kix_profile=kix_profile,
            verbose=verbose,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/kix/test_cli_kix.py -v`

Expected: all passed.

- [ ] **Step 5: Run the full test suite**

Run: `cd .claude/worktrees/round-trip && python -m pytest tests/ --tb=short`

Expected: all tests pass. Existing CLI tests should be unaffected (new flags have defaults).

- [ ] **Step 6: Commit**

```bash
git add src/google_doc_diff/cli.py tests/kix/test_cli_kix.py
git commit -m "cli: wire kix enrichment into gdoc pull with --kix-cookies/--kix-profile/--no-kix"
```

---

### Task 9: Ruff lint + final integration check

**Files:** all new files

- [ ] **Step 1: Run ruff on new files**

Run:
```bash
cd .claude/worktrees/round-trip && python -m ruff check src/google_doc_diff/kix/ tests/kix/
```

Expected: clean, or fix any issues.

- [ ] **Step 2: Run ruff format check**

Run:
```bash
cd .claude/worktrees/round-trip && python -m ruff format --check src/google_doc_diff/kix/ tests/kix/
```

Expected: clean, or fix any issues.

- [ ] **Step 3: Run full test suite one final time**

Run:
```bash
cd .claude/worktrees/round-trip && python -m pytest tests/ -v --tb=short
```

Expected: all tests pass, including all new kix tests.

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -u
git commit -m "style: ruff lint fixes for kix modules"
```

(Skip if no changes needed.)

---

### Task 10: Update STATUS.md

**Files:**
- Modify: `STATUS.md`

- [ ] **Step 1: Update STATUS.md to reflect kix enrichment**

Add a new row to the "What landed" table:

```markdown
| 9 | `kix/auth.py`, `kix/model.py`, `kix/enrich.py` | Optional kix enrichment layer: Chrome cookie auth, OT model extraction, suggestion colors, comment anchor resolution, voting chip enrichment |
```

Update the "Known issues / follow-ups" section: remove the "Kix resolver not wired to CLI" item (it's done now). Keep the "/save channel backend" item but note it's blocked on "future: suggestion authoring".

- [ ] **Step 2: Commit**

```bash
git add STATUS.md
git commit -m "docs: STATUS.md — kix enrichment layer landed"
```
