# Per-tab pull for large multi-tab docs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `gdoc pull` succeed on large multi-tab docs whose `documents.get?includeTabsContent=true` call returns HTTP 500, by exporting each tab's markdown separately and re-attaching Drive comments.

**Architecture:** A new `tabs.py` recovers the tab list (id, title, order) from the `/edit` payload, which the OAuth bearer alone can fetch. A new `per_tab.py` exports each tab as markdown through the existing backoff-wrapped HTTP helper, runs each through `build_from_google_md`, assembles one multi-tab `Document`, and anchors Drive comments onto it. `pull` falls back to this path automatically when the bulk call 500s.

**Tech Stack:** Python ≥3.11, Click, `requests`, `markdown-it-py`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-20-per-tab-pull-design.md`

## Global Constraints

- Python ≥3.11, `uv` build backend, `src/` layout. Everything assumes the `.venv` is active.
- Ruff: line-length 100, rules `E,F,W,I,B,UP`, `E501`/`B008` ignored. Gate on `ruff check`, not `ruff format`.
- Use `from datetime import UTC`, not `timezone.utc`.
- Tests are mocked against fixtures. No network and no live Google auth in the test suite.
- Never use Python's built-in `hash()` for anything persisted or emitted; use `hashlib`.
- AST tab IDs are kind-prefixed: `from_docs_json.py` builds them as `"t-" + <Google tabId>`, so a Google tab `t.abc123` becomes AST tab id `t-t.abc123`. Match that exactly — `kix/enrich.py` strips the `t-` prefix to pair tabs back up.
- After unit tests pass, rebuild the `gdoc` binary and smoke-test it end-to-end before calling anything done.

---

### Task 1: Tab enumeration from the `/edit` payload

**Files:**
- Create: `src/google_doc_diff/tabs.py`
- Create: `tests/unit/test_tabs.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TabRef` (frozen dataclass with `tab_id: str`, `title: str`, `index: int`) and `parse_tab_refs(html: str) -> list[TabRef]`, sorted by `index`.

Background: the `/edit` HTML embeds one op per tab, of the form
`{"ty":"ac","d":["t.av9h4hz2va7o",[1,"2026-05-06"],[10]]}` — tab id, title, and
display order. Titles are JSON-escaped (`&` for `&`), so they must be
decoded rather than regex-captured raw. Parsing uses `raw_decode` from the
start of each op rather than a non-greedy regex, because a title containing
`]}` would truncate a regex match.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tabs.py
from google_doc_diff.tabs import TabRef, parse_tab_refs


def _ac(tab_id, title, index):
    return '{"ty":"ac","d":["%s",[1,"%s"],[%d]]}' % (tab_id, title, index)


def test_parses_id_title_and_order():
    html = "junk" + _ac("t.bbb", "2026-05-06", 1) + "junk" + _ac("t.aaa", "Overview", 0)
    assert parse_tab_refs(html) == [
        TabRef(tab_id="t.aaa", title="Overview", index=0),
        TabRef(tab_id="t.bbb", title="2026-05-06", index=1),
    ]


def test_unescapes_json_in_titles():
    html = _ac("t.aaa", "WBR \\u0026 ORR", 0)
    assert parse_tab_refs(html)[0].title == "WBR & ORR"


def test_title_containing_bracket_brace_is_not_truncated():
    html = _ac("t.aaa", "weird]} title", 0)
    assert parse_tab_refs(html)[0].title == "weird]} title"


def test_duplicate_ops_for_one_tab_collapse():
    html = _ac("t.aaa", "Old", 0) + _ac("t.aaa", "New", 0)
    refs = parse_tab_refs(html)
    assert len(refs) == 1
    assert refs[0].title == "New"


def test_malformed_ops_are_skipped():
    html = '{"ty":"ac","d":["t.aaa"]}' + '{"ty":"ac","d":[' + _ac("t.bbb", "Good", 3)
    assert parse_tab_refs(html) == [TabRef(tab_id="t.bbb", title="Good", index=3)]


def test_no_tabs_returns_empty_list():
    assert parse_tab_refs("<html>nothing here</html>") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_tabs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'google_doc_diff.tabs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/google_doc_diff/tabs.py
"""Tab enumeration for documents whose bulk `documents.get` call fails.

`documents.get?includeTabsContent=true` 500s on large multi-tab docs, and it
is the only API route to the tab list -- there is no per-tab variant and a
field mask does not help. The `/edit` payload, which the OAuth bearer alone
can fetch, embeds the same information as one op per tab:

    {"ty":"ac","d":["t.av9h4hz2va7o",[1,"2026-05-06"],[10]]}
                     tab id            title            order

`kix/model.py` parses the same payload for its OT stream, but authenticates
with Chrome cookies and lives behind the optional [kix] extra. This module
shares the idiom, not the dependency.
"""

import json
import re
from dataclasses import dataclass

_AC_START = re.compile(r'\{"ty":"ac","d":\[')
_DECODER = json.JSONDecoder()


@dataclass(frozen=True)
class TabRef:
    """A tab's identity as advertised by the editor payload."""

    tab_id: str          # Google's raw id, e.g. 't.av9h4hz2va7o'
    title: str
    index: int           # display order


def parse_tab_refs(html: str) -> list[TabRef]:
    """Extract the document's tabs from an `/edit` payload, in display order."""
    refs: dict[str, TabRef] = {}
    for m in _AC_START.finditer(html):
        try:
            obj, _end = _DECODER.raw_decode(html, m.start())
        except ValueError:
            continue
        d = obj.get("d")
        if not isinstance(d, list) or len(d) < 3:
            continue
        tab_id, title_field, index_field = d[0], d[1], d[2]
        if not isinstance(tab_id, str) or not tab_id.startswith("t."):
            continue
        title = ""
        if isinstance(title_field, list) and len(title_field) > 1:
            title = str(title_field[1])
        index = 0
        if isinstance(index_field, list) and index_field:
            index = int(index_field[0])
        refs[tab_id] = TabRef(tab_id=tab_id, title=title, index=index)
    return sorted(refs.values(), key=lambda r: r.index)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_tabs.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint**

Run: `ruff check src/google_doc_diff/tabs.py tests/unit/test_tabs.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/google_doc_diff/tabs.py tests/unit/test_tabs.py
git commit -m "tabs: recover the tab list from the /edit payload"
```

---

### Task 2: API methods for per-tab export

**Files:**
- Modify: `src/google_doc_diff/api.py` (add three methods to `GdocAPI`, after `fetch_revision_export` at line 126)
- Create: `tests/unit/test_api_per_tab.py`

**Interfaces:**
- Consumes: existing `GdocAPI._do_get`, `GdocAPI._with_backoff_http`, `GdocAPI._with_backoff`.
- Produces: `GdocAPI.fetch_edit_html(doc_id: str) -> str`, `GdocAPI.export_tab_markdown(doc_id: str, tab_id: str) -> str`, `GdocAPI.get_document_metadata(doc_id: str) -> dict`.

`_do_get` already raises `_Transient` on 429 and 5xx, and `_with_backoff_http`
already retries it with exponential backoff — which is exactly the failure the
export endpoint produces under load. Reuse both rather than adding a second
retry path. `get_document_metadata` uses `includeTabsContent=False`, which
returns in well under a second on the same document whose `=true` call 500s,
and supplies the title and revision id the assembled `Document` needs.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_api_per_tab.py
from google_doc_diff.api import GdocAPI


class _FakeCreds:
    token = "fake-token"


def _api_without_building_clients() -> GdocAPI:
    """GdocAPI.__init__ builds live discovery clients; bypass it."""
    api = GdocAPI.__new__(GdocAPI)
    api._creds = _FakeCreds()
    return api


def test_fetch_edit_html_hits_the_edit_url_and_decodes():
    api = _api_without_building_clients()
    seen = []
    api._do_get = lambda url: seen.append(url) or b"<html>hi</html>"

    assert api.fetch_edit_html("DOC123") == "<html>hi</html>"
    assert seen == ["https://docs.google.com/document/d/DOC123/edit"]


def test_export_tab_markdown_passes_the_tab_parameter():
    api = _api_without_building_clients()
    seen = []
    api._do_get = lambda url: seen.append(url) or "# tab\n".encode()

    assert api.export_tab_markdown("DOC123", "t.abc") == "# tab\n"
    assert seen == [
        "https://docs.google.com/document/d/DOC123/export?format=md&tab=t.abc"
    ]


def test_export_tab_markdown_retries_through_the_backoff_helper():
    """The export endpoint 429s under load; retries must go through
    _with_backoff_http rather than a second ad-hoc retry path."""
    api = _api_without_building_clients()
    calls = []

    def fake_backoff(fn, *args):
        calls.append((fn, args))
        return b"# tab\n"

    api._with_backoff_http = fake_backoff
    api.export_tab_markdown("DOC123", "t.abc")
    assert len(calls) == 1
    assert calls[0][0] == api._do_get


def test_get_document_metadata_asks_for_no_tab_content():
    api = _api_without_building_clients()
    captured = {}

    def fake_backoff(factory, **kwargs):
        captured.update(kwargs)
        return {"title": "Doc", "revisionId": "rev9"}

    class _FakeDocs:
        def documents(self):
            return self

        def get(self, **kwargs):  # never executed; _with_backoff is stubbed
            raise AssertionError("should go through _with_backoff")

    api._docs = _FakeDocs()
    api._with_backoff = fake_backoff
    assert api.get_document_metadata("DOC123")["revisionId"] == "rev9"
    assert captured["includeTabsContent"] is False
    assert captured["documentId"] == "DOC123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_per_tab.py -v`
Expected: FAIL — `AttributeError: 'GdocAPI' object has no attribute 'fetch_edit_html'`

- [ ] **Step 3: Write minimal implementation**

Insert into `class GdocAPI` in `src/google_doc_diff/api.py`, immediately after
`fetch_revision_export`:

```python
    def fetch_edit_html(self, doc_id: str) -> str:
        """Fetch the /edit payload, which carries the tab list.

        Used by the per-tab pull path: `documents.get?includeTabsContent=true`
        is the only API route to the tab list and it 500s on large docs, but
        /edit serves 200 to the OAuth bearer alone (no browser cookies).
        """
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        return self._with_backoff_http(self._do_get, url).decode("utf-8", errors="replace")

    def export_tab_markdown(self, doc_id: str, tab_id: str) -> str:
        """Export a single tab as markdown.

        The `tab=` parameter is undocumented and could change. Note that an
        unrecognised tab id does NOT error -- it returns the default tab's
        content with status 200 -- so callers must check for duplicates.
        """
        url = (
            f"https://docs.google.com/document/d/{doc_id}"
            f"/export?format=md&tab={tab_id}"
        )
        return self._with_backoff_http(self._do_get, url).decode("utf-8", errors="replace")

    def get_document_metadata(self, doc_id: str) -> dict:
        """Fetch title/revisionId without tab content.

        Returns in under a second on documents whose includeTabsContent=true
        call 500s, so the per-tab path can still label its output correctly.
        """
        return self._with_backoff(
            self._docs.documents().get,
            documentId=doc_id,
            includeTabsContent=False,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_api_per_tab.py -v`
Expected: 4 passed

- [ ] **Step 5: Check nothing else regressed**

Run: `pytest tests/ -q`
Expected: all pass (400+ tests)

- [ ] **Step 6: Commit**

```bash
git add src/google_doc_diff/api.py tests/unit/test_api_per_tab.py
git commit -m "api: add per-tab markdown export and tab-less metadata fetch"
```

---

### Task 3: Export safeguards

**Files:**
- Create: `src/google_doc_diff/per_tab.py`
- Create: `tests/unit/test_per_tab.py`

**Interfaces:**
- Consumes: `TabRef` from Task 1.
- Produces: `PerTabError(Exception)` and `validate_tab_exports(exports: dict[str, str], refs: list[TabRef]) -> None`, which raises `PerTabError` on a bad export set and returns `None` otherwise.

Two failure modes were observed live and both must abort rather than warn:

1. An unrecognised tab id returns another tab's content with status 200. Hashing
   each export catches this for free — the bogus tab's bytes collide with the
   genuine tab it was served. This also guards the `t.0` alias case: `t.0`
   exports content belonging to a tab already in the list.
2. A throttled request returns an HTML error page. `_do_get` rejects it on
   status, but a body check costs nothing and covers any 200-with-HTML case.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_per_tab.py
import pytest

from google_doc_diff.per_tab import PerTabError, validate_tab_exports
from google_doc_diff.tabs import TabRef

REFS = [
    TabRef(tab_id="t.aaa", title="Overview", index=0),
    TabRef(tab_id="t.bbb", title="2026-05-06", index=1),
]


def test_distinct_exports_pass():
    validate_tab_exports({"t.aaa": "# One\n", "t.bbb": "# Two\n"}, REFS)


def test_identical_exports_abort_naming_both_titles():
    """A bad tab id silently returns another tab's content; the hashes collide."""
    with pytest.raises(PerTabError) as exc:
        validate_tab_exports({"t.aaa": "# Same\n", "t.bbb": "# Same\n"}, REFS)
    assert "Overview" in str(exc.value)
    assert "2026-05-06" in str(exc.value)


def test_html_error_page_aborts_naming_the_tab():
    html = '<!DOCTYPE html><html lang="en"><head><script nonce="x">'
    with pytest.raises(PerTabError) as exc:
        validate_tab_exports({"t.aaa": "# One\n", "t.bbb": html}, REFS)
    assert "2026-05-06" in str(exc.value)


def test_markdown_containing_inline_html_is_not_mistaken_for_an_error_page():
    validate_tab_exports(
        {"t.aaa": "# One\n\n<div>inline</div>\n", "t.bbb": "# Two\n"}, REFS
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_per_tab.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'google_doc_diff.per_tab'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/google_doc_diff/per_tab.py
"""Per-tab pull for documents whose bulk `documents.get` call returns 500.

See docs/superpowers/specs/2026-07-20-per-tab-pull-design.md. This path is
lossy by necessity: Google exposes no high-fidelity route to these documents
at any granularity, so content arrives as markdown (no suggestions, no stable
paragraph ids) and comments are re-attached from the Drive API, which is
unaffected by document size.
"""

import hashlib

from google_doc_diff.tabs import TabRef


class PerTabError(Exception):
    """A per-tab pull could not be completed safely."""


def _looks_like_html(text: str) -> bool:
    return text.lstrip()[:200].lower().startswith(("<!doctype", "<html"))


def validate_tab_exports(exports: dict[str, str], refs: list[TabRef]) -> None:
    """Abort on export sets that would produce silently wrong content.

    Raises PerTabError if any export is an HTML error page, or if two tabs
    exported identical bytes -- the signature of a tab id the export endpoint
    did not recognise, since it answers those with the default tab's content
    and a 200.
    """
    titles = {r.tab_id: r.title for r in refs}

    def label(tab_id: str) -> str:
        return f"{titles.get(tab_id, '?')!r} ({tab_id})"

    for tab_id, text in exports.items():
        if _looks_like_html(text):
            raise PerTabError(
                f"tab {label(tab_id)} returned an HTML error page, not markdown"
            )

    by_hash: dict[str, list[str]] = {}
    for tab_id, text in exports.items():
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        by_hash.setdefault(digest, []).append(tab_id)

    for tab_ids in by_hash.values():
        if len(tab_ids) > 1:
            names = ", ".join(label(t) for t in sorted(tab_ids))
            raise PerTabError(
                f"tabs exported identical content: {names}. The export endpoint "
                "answers an unrecognised tab id with the default tab's content, "
                "so this is either a stale tab id or two genuinely identical tabs."
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_per_tab.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/google_doc_diff/per_tab.py tests/unit/test_per_tab.py
git commit -m "per-tab: reject silently-wrong export sets"
```

---

### Task 4: Assemble the multi-tab document

**Files:**
- Modify: `src/google_doc_diff/per_tab.py` (add the builder below the validator)
- Modify: `tests/unit/test_per_tab.py` (append)

**Interfaces:**
- Consumes: `parse_tab_refs`, `TabRef` (Task 1); `fetch_edit_html`, `export_tab_markdown`, `get_document_metadata` (Task 2); `validate_tab_exports`, `PerTabError` (Task 3).
- Produces: `build_per_tab_document(api, doc_id: str, *, delay: float = 1.0, sleep=time.sleep, on_progress=None) -> Document`.

`on_progress` is `Callable[[TabRef, int, int], None]` — the tab, its 1-based
position, and the total — so the CLI can report progress during a pull that
takes minutes. `sleep` is injected so tests do not wait.

Existing pieces this reuses: `build_from_google_md` parses one tab's markdown
into a `Document` whose single tab holds the blocks; `_build_comments` converts
Drive comment JSON into `Comment` nodes; `anchor_comments` wraps the quoted text
in `CommentAnchor` nodes by text matching.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_per_tab.py
from google_doc_diff.per_tab import build_per_tab_document

EDIT_HTML = (
    '{"ty":"ac","d":["t.aaa",[1,"Overview"],[0]]}'
    '{"ty":"ac","d":["t.bbb",[1,"2026-05-06"],[1]]}'
)


class _FakeAPI:
    def __init__(self, exports, comments=()):
        self._exports = exports
        self._comments = list(comments)
        self.export_calls = []

    def fetch_edit_html(self, doc_id):
        return EDIT_HTML

    def export_tab_markdown(self, doc_id, tab_id):
        self.export_calls.append(tab_id)
        return self._exports[tab_id]

    def get_document_metadata(self, doc_id):
        return {"title": "Big Doc", "revisionId": "rev9"}

    def list_comments(self, doc_id):
        return self._comments


def _exports():
    return {"t.aaa": "# Overview\n\nIntro prose.\n", "t.bbb": "# Week\n\nWeek prose.\n"}


def test_builds_one_tab_per_ref_in_order_with_prefixed_ids():
    api = _FakeAPI(_exports())
    doc = build_per_tab_document(api, "DOC123", sleep=lambda _s: None)

    assert [t.title for t in doc.tabs] == ["Overview", "2026-05-06"]
    assert [t.tab_id for t in doc.tabs] == ["t-t.aaa", "t-t.bbb"]
    assert api.export_calls == ["t.aaa", "t.bbb"]


def test_carries_metadata_and_records_lost_fidelity():
    doc = build_per_tab_document(_FakeAPI(_exports()), "DOC123", sleep=lambda _s: None)
    assert doc.title == "Big Doc"
    assert doc.revision_id == "rev9"
    assert doc.source_mode == "pull"
    assert doc.suggestions_preserved is False
    assert doc.comments_preserved is True


def test_attaches_drive_comments():
    comments = [{
        "id": "cmt1",
        "author": {"emailAddress": "a@example.com"},
        "createdTime": "2026-05-06T10:00:00.000Z",
        "modifiedTime": "2026-05-06T10:00:00.000Z",
        "content": "a note",
        "quotedFileContent": {"value": "Intro prose."},
    }]
    doc = build_per_tab_document(
        _FakeAPI(_exports(), comments), "DOC123", sleep=lambda _s: None
    )
    assert "c-cmt1" in doc.comments
    assert doc.comments["c-cmt1"].content == "a note"


def test_reports_progress_per_tab():
    seen = []
    build_per_tab_document(
        _FakeAPI(_exports()), "DOC123", sleep=lambda _s: None,
        on_progress=lambda ref, n, total: seen.append((ref.title, n, total)),
    )
    assert seen == [("Overview", 1, 2), ("2026-05-06", 2, 2)]


def test_delays_between_exports_but_not_before_the_first():
    slept = []
    build_per_tab_document(
        _FakeAPI(_exports()), "DOC123", delay=2.5, sleep=slept.append
    )
    assert slept == [2.5]


def test_no_tabs_found_is_an_error():
    api = _FakeAPI(_exports())
    api.fetch_edit_html = lambda doc_id: "<html>no ops here</html>"
    with pytest.raises(PerTabError, match="no tabs"):
        build_per_tab_document(api, "DOC123", sleep=lambda _s: None)


def test_duplicate_exports_abort_the_whole_pull():
    api = _FakeAPI({"t.aaa": "# Same\n", "t.bbb": "# Same\n"})
    with pytest.raises(PerTabError):
        build_per_tab_document(api, "DOC123", sleep=lambda _s: None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_per_tab.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_per_tab_document'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `src/google_doc_diff/per_tab.py`:

```python
import time
from datetime import UTC, datetime

from google_doc_diff.api import drive_url_for
from google_doc_diff.ast.anchor_comments import anchor_comments
from google_doc_diff.ast.from_docs_json import _build_comments
from google_doc_diff.ast.from_google_md import build_from_google_md
from google_doc_diff.ast.nodes import Document, Tab
from google_doc_diff.tabs import TabRef, parse_tab_refs
```

Append to `src/google_doc_diff/per_tab.py`:

```python
def build_per_tab_document(
    api,
    doc_id: str,
    *,
    delay: float = 1.0,
    sleep=time.sleep,
    on_progress=None,
) -> Document:
    """Build a Document by exporting each tab separately.

    Tabs are fetched sequentially with `delay` seconds between requests: the
    export endpoint rate-limits hard enough that parallel fetching exhausts
    the quota and poisons subsequent serial requests too.
    """
    refs = parse_tab_refs(api.fetch_edit_html(doc_id))
    if not refs:
        raise PerTabError(
            f"no tabs found in the /edit payload for {doc_id}; "
            "the document may have no tabs, or the payload format may have changed"
        )

    exports: dict[str, str] = {}
    for n, ref in enumerate(refs, start=1):
        if n > 1:
            sleep(delay)
        exports[ref.tab_id] = api.export_tab_markdown(doc_id, ref.tab_id)
        if on_progress:
            on_progress(ref, n, len(refs))

    validate_tab_exports(exports, refs)

    tabs = [_tab_from_markdown(ref, exports[ref.tab_id], doc_id) for ref in refs]

    meta = api.get_document_metadata(doc_id)
    document = Document(
        doc_id=doc_id,
        title=meta.get("title") or "(untitled)",
        revision_id=meta.get("revisionId", ""),
        drive_url=drive_url_for(doc_id),
        captured_at=datetime.now(UTC),
        schema_version=1,
        last_modifying_user=None,
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=False,
        tabs=tabs,
        comments=_build_comments(api.list_comments(doc_id)),
    )
    return anchor_comments(document)


def _tab_from_markdown(ref: TabRef, markdown: str, doc_id: str) -> Tab:
    """Parse one tab's exported markdown into a Tab node.

    build_from_google_md returns a whole Document with a single placeholder
    tab; we keep its blocks and restore the tab's real identity. The `t-`
    prefix matches from_docs_json's convention (kix/enrich strips it again).
    """
    parsed = build_from_google_md(markdown, doc_id=doc_id)
    blocks = parsed.tabs[0].blocks if parsed.tabs else []
    return Tab(tab_id="t-" + ref.tab_id, title=ref.title, level=0, blocks=blocks)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_per_tab.py -v`
Expected: 11 passed

- [ ] **Step 5: Lint**

Run: `ruff check src/google_doc_diff/per_tab.py tests/unit/test_per_tab.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/google_doc_diff/per_tab.py tests/unit/test_per_tab.py
git commit -m "per-tab: assemble a multi-tab Document from per-tab exports"
```

---

### Task 5: Wire the fallback into `gdoc pull`

**Files:**
- Modify: `src/google_doc_diff/cli.py:127-214` (the `pull` command)
- Create: `tests/unit/test_cli_pull_per_tab.py`

**Interfaces:**
- Consumes: `build_per_tab_document`, `PerTabError` (Tasks 3-4).
- Produces: `--per-tab/--no-per-tab` on `gdoc pull`, and `_is_bulk_tabs_500(exc) -> bool` in `cli.py`.

Behaviour:

- Default (`per_tab is None`): try the rich bulk call; on a 500 from it, warn
  loudly and retry via the per-tab path.
- `--per-tab`: skip the bulk call entirely.
- `--no-per-tab`: never fall back; the 500 surfaces as the existing error exit.

Two consequences of the lossy path that the code must handle explicitly:

- No `docs_json`, so no `.pull-state.json` sidecar can be written. `gdoc push`'s
  three-way merge has no base to diff against; say so on stderr rather than
  writing a sidecar that would silently misrepresent the doc.
- kix enrichment is skipped: it pairs chips against the rich AST's index space,
  which the markdown path does not produce.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_pull_per_tab.py
from unittest.mock import patch

from googleapiclient.errors import HttpError

from google_doc_diff.cli import _is_bulk_tabs_500, cli


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "Internal error"


def _http_error(status):
    return HttpError(_Resp(status), b'{"error": {"message": "Internal error"}}')


def test_recognises_the_bulk_500():
    assert _is_bulk_tabs_500(_http_error(500)) is True
    assert _is_bulk_tabs_500(_http_error(403)) is False
    assert _is_bulk_tabs_500(ValueError("nope")) is False


def _run(runner, tmp_path, args, rich_side_effect, per_tab_doc):
    out = tmp_path / "doc.md"
    with patch("google_doc_diff.cli.load_credentials", return_value=object()), \
         patch("google_doc_diff.cli.GdocAPI", return_value=object()), \
         patch("google_doc_diff.cli._pull_rich_document_with_raw",
               side_effect=rich_side_effect) as rich, \
         patch("google_doc_diff.cli.build_per_tab_document",
               return_value=per_tab_doc) as per_tab, \
         patch("google_doc_diff.cli.emit_document_md", return_value="# doc\n"):
        result = runner.invoke(cli, ["pull", "DOC123", "--out", str(out), *args])
    return result, rich, per_tab, out


def test_falls_back_to_per_tab_on_500(cli_runner, temp_dir, minimal_document):
    result, rich, per_tab, out = _run(
        cli_runner, temp_dir, [], _http_error(500), minimal_document
    )
    assert result.exit_code == 0
    assert rich.called and per_tab.called
    assert "degraded" in result.output.lower()
    assert out.read_text() == "# doc\n"


def test_no_sidecar_is_written_on_the_per_tab_path(cli_runner, temp_dir, minimal_document):
    result, _rich, _per_tab, out = _run(
        cli_runner, temp_dir, [], _http_error(500), minimal_document
    )
    assert result.exit_code == 0
    assert not out.with_suffix(".md.pull-state.json").exists()


def test_per_tab_flag_skips_the_bulk_call(cli_runner, temp_dir, minimal_document):
    result, rich, per_tab, _out = _run(
        cli_runner, temp_dir, ["--per-tab"], None, minimal_document
    )
    assert result.exit_code == 0
    assert not rich.called
    assert per_tab.called


def test_no_per_tab_flag_lets_the_500_fail(cli_runner, temp_dir, minimal_document):
    result, _rich, per_tab, _out = _run(
        cli_runner, temp_dir, ["--no-per-tab"], _http_error(500), minimal_document
    )
    assert result.exit_code == 2
    assert not per_tab.called
```

The `minimal_document` fixture does not exist yet. Add it to
`tests/conftest.py` (which already provides `cli_runner` and `temp_dir`):

```python
@pytest.fixture
def minimal_document():
    from datetime import UTC, datetime

    from google_doc_diff.ast.nodes import Document, Tab

    return Document(
        doc_id="DOC123",
        title="Big Doc",
        revision_id="rev9",
        drive_url="https://docs.google.com/document/d/DOC123/edit",
        captured_at=datetime(2026, 7, 21, tzinfo=UTC),
        schema_version=1,
        last_modifying_user=None,
        source_mode="pull",
        comments_preserved=True,
        suggestions_preserved=False,
        tabs=[Tab(tab_id="t-t.aaa", title="Overview", level=0, blocks=[])],
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_pull_per_tab.py -v`
Expected: FAIL — `ImportError: cannot import name '_is_bulk_tabs_500'`

- [ ] **Step 3: Add the import and helper to `cli.py`**

Add to the imports at the top of `src/google_doc_diff/cli.py`:

```python
from googleapiclient.errors import HttpError

from google_doc_diff.per_tab import PerTabError, build_per_tab_document
```

Add beside the other private helpers in `cli.py` (near `_pull_rich_document`):

```python
def _is_bulk_tabs_500(exc: BaseException) -> bool:
    """True for the 500 that documents.get?includeTabsContent=true returns on
    large multi-tab docs. There is no per-tab variant of that call and a field
    mask does not help, so the only recourse is the per-tab export path."""
    if not isinstance(exc, HttpError):
        return False
    status = getattr(exc, "status_code", None) or exc.resp.status
    return status == 500
```

- [ ] **Step 4: Add the option and rework the fetch block**

Add this option to `pull`, after the `--revision` option at `cli.py:134`:

```python
@click.option("--per-tab/--no-per-tab", "per_tab", default=None,
              help="Pull each tab separately via markdown export. Default: "
                   "auto, used only when the full-document fetch fails on a "
                   "large doc. Lossy: no suggestions, no paragraph ids.")
```

Add `per_tab` to the `def pull(...)` parameter list, after `revision`.

Replace the fetch block at `cli.py:166-170`:

```python
    try:
        document, docs_json = _pull_rich_document_with_raw(api, doc_id, chip_counts=chip_counts)
    except Exception as e:
        click.echo(f"api: {e}", err=True)
        sys.exit(2)
```

with:

```python
    def _pull_per_tab():
        return build_per_tab_document(
            api, doc_id,
            on_progress=lambda ref, n, total: click.echo(
                f"  tab {n}/{total}: {ref.title}", err=True
            ),
        )

    docs_json = None
    try:
        if per_tab:
            document = _pull_per_tab()
        else:
            document, docs_json = _pull_rich_document_with_raw(
                api, doc_id, chip_counts=chip_counts
            )
    except PerTabError as e:
        click.echo(f"per-tab pull: {e}", err=True)
        sys.exit(2)
    except Exception as e:
        if per_tab is False or not _is_bulk_tabs_500(e):
            click.echo(f"api: {e}", err=True)
            sys.exit(2)
        click.echo(
            "warning: the full-document fetch failed with HTTP 500, which "
            "Google returns for large multi-tab docs. Falling back to per-tab "
            "export -- fidelity is degraded: suggestions and paragraph ids are "
            "lost, comments are re-anchored by text matching. This takes a few "
            "minutes. Use --no-per-tab to fail instead.",
            err=True,
        )
        try:
            document = _pull_per_tab()
        except PerTabError as e2:
            click.echo(f"per-tab pull: {e2}", err=True)
            sys.exit(2)
```

- [ ] **Step 5: Gate kix enrichment and the sidecar on the rich path**

Replace the kix block at `cli.py:172-178` so its condition also requires
`docs_json` (kix pairs chips against the rich AST's index space):

```python
    if not no_kix and docs_json is not None:
        _try_kix_enrichment(
            document, doc_id,
            kix_cookies=str(kix_cookies) if kix_cookies else None,
            kix_profile=kix_profile,
            verbose=verbose,
        )
```

Replace the sidecar block at `cli.py:186-194`:

```python
    # Write the merge-base sidecar so `gdoc push` (default = 3-way merge)
    # has the doc's pull-time state to diff against. The per-tab path has no
    # Docs JSON to record, so push cannot three-way merge these docs.
    if docs_json is None:
        click.echo(
            "note: no .pull-state.json written (per-tab pull has no Docs JSON); "
            "`gdoc push` cannot three-way merge this file.",
            err=True,
        )
    else:
        state_path = out_path.with_suffix(out_path.suffix + ".pull-state.json")
        state_path.write_text(json.dumps({
            "doc_id": doc_id,
            "revision_id": document.revision_id,
            "docs_json": docs_json,
            "base_md": md,
        }, default=str) + "\n")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_cli_pull_per_tab.py -v`
Expected: 5 passed

- [ ] **Step 7: Run the full suite and lint**

Run: `pytest tests/ -q && ruff check .`
Expected: all pass, `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/google_doc_diff/cli.py tests/unit/test_cli_pull_per_tab.py tests/conftest.py
git commit -m "pull: fall back to per-tab export when the bulk fetch 500s"
```

---

### Task 6: Round-trip test through the emitter

**Files:**
- Create: `tests/round_trip/test_per_tab_pipeline.py`

**Interfaces:**
- Consumes: `build_per_tab_document` (Task 4), `emit_document_md`.
- Produces: nothing.

Confirms the assembled document survives emission with its tab structure
intact — the property the monthly-business-review pipeline's downstream
splitters depend on.

- [ ] **Step 1: Write the test**

```python
# tests/round_trip/test_per_tab_pipeline.py
from google_doc_diff.emit.markdown import emit_document_md
from google_doc_diff.per_tab import build_per_tab_document

EDIT_HTML = (
    '{"ty":"ac","d":["t.aaa",[1,"Overview"],[0]]}'
    '{"ty":"ac","d":["t.bbb",[1,"2026-05-06"],[1]]}'
)

EXPORTS = {
    "t.aaa": "# Overview\n\nIntro prose.\n",
    "t.bbb": "# Week\n\nWeek prose worth quoting.\n",
}

COMMENTS = [{
    "id": "cmt1",
    "author": {"emailAddress": "a@example.com"},
    "createdTime": "2026-05-06T10:00:00.000Z",
    "modifiedTime": "2026-05-06T10:00:00.000Z",
    "content": "a note",
    "quotedFileContent": {"value": "Week prose worth quoting."},
}]


class _FakeAPI:
    def fetch_edit_html(self, doc_id):
        return EDIT_HTML

    def export_tab_markdown(self, doc_id, tab_id):
        return EXPORTS[tab_id]

    def get_document_metadata(self, doc_id):
        return {"title": "Big Doc", "revisionId": "rev9"}

    def list_comments(self, doc_id):
        return COMMENTS


def test_per_tab_pull_emits_one_fenced_div_per_tab_in_order():
    doc = build_per_tab_document(_FakeAPI(), "DOC123", sleep=lambda _s: None)
    md = emit_document_md(doc)

    assert md.index('data-title="Overview"') < md.index('data-title="2026-05-06"')
    assert md.count("::: {.gd-tab") == 2
    assert "Intro prose." in md and "Week prose worth quoting." in md


def test_emission_is_deterministic():
    """The merge layer's emit->parse normalisation depends on this."""
    first = emit_document_md(
        build_per_tab_document(_FakeAPI(), "DOC123", sleep=lambda _s: None)
    )
    second = emit_document_md(
        build_per_tab_document(_FakeAPI(), "DOC123", sleep=lambda _s: None)
    )
    assert first == second
```

- [ ] **Step 2: Run it**

Run: `pytest tests/round_trip/test_per_tab_pipeline.py -v`
Expected: 2 passed.

If the `data-title` assertions fail, read the actual emitted markdown and
match the emitter's real fenced-div syntax from `emit/markdown.py` rather than
changing the emitter — the format is load-bearing for downstream splitters.
The `captured_at` timestamp differs between the two builds, so if the
determinism test fails on a frontmatter timestamp, compare
`_strip_volatile_frontmatter(first)` against the same for `second` (helper
already exists in `cli.py`).

- [ ] **Step 3: Commit**

```bash
git add tests/round_trip/test_per_tab_pipeline.py
git commit -m "test: per-tab pull survives emission with tab structure intact"
```

---

### Task 7: Live end-to-end smoke test

**Files:**
- Modify: `README.md` (document the flag)
- Modify: `STATUS.md` (record the limitation)

Per the repo's rule, unit tests alone do not count as verification. This task
runs the real binary against the real document that motivated the issue.

- [ ] **Step 1: Rebuild the binary**

Run: `make install-local`
Expected: `gdoc` on PATH reflects the new code — check with
`gdoc pull --help | grep per-tab`, expecting the new option to appear.

- [ ] **Step 2: Pull the doc that 500s**

Run:

```bash
cd /tmp && time gdoc pull 1gnUazQiQ7KdxcKtQBL190TskZFYN5RFRlchWwKeu50g --out foc-wbr.md
```

Expected: the degraded-fidelity warning on stderr, 24 progress lines, then
`wrote foc-wbr.md` plus the no-sidecar note. Takes several minutes.

- [ ] **Step 3: Verify the output covers every tab**

Run:

```bash
grep -c '^::: {.gd-tab' /tmp/foc-wbr.md
grep -o 'data-title="[^"]*"' /tmp/foc-wbr.md
```

Expected: 24 fenced divs, with titles running `2026-07-22` down to
`[Updated] Template` — matching the tab list confirmed during design.

- [ ] **Step 4: Verify comments came through**

Run: `grep -c 'gd-comment' /tmp/foc-wbr.md`
Expected: non-zero. If zero, check whether the doc has comments at all
(`gdoc` has no comment-count command; use the Drive API or open the doc)
before treating it as a failure.

- [ ] **Step 5: Confirm the non-regression**

Pull a doc that already worked, and confirm it still takes the rich path —
no warning, and a sidecar is written:

```bash
cd /tmp && gdoc pull 1r31NtoyaR7wJC8xQugP5Tb82bZlms9CAHFxi1hvIY4c --out filecoin-wbr.md
ls filecoin-wbr.md.pull-state.json
```

Expected: no degraded-fidelity warning, sidecar present.

- [ ] **Step 6: Document it**

In `README.md`, beside the `pull` documentation, add:

```markdown
### Large multi-tab docs

Google's `documents.get?includeTabsContent=true` returns HTTP 500 on large
multi-tab documents, and there is no per-tab variant of that call. When `pull`
hits that 500 it falls back to exporting each tab's markdown separately and
re-attaching comments from the Drive API. Force it with `--per-tab`, or
disable the fallback with `--no-per-tab`.

The fallback is lossy: suggestions and paragraph ids are gone, comments are
re-anchored by text matching, and no `.pull-state.json` is written, so `gdoc
push` cannot three-way merge these files. It is also slow — the export
endpoint rate-limits, so tabs are fetched one per second.
```

In `STATUS.md`, add a line under the deferred/limitations section recording
that per-tab pulls cannot round-trip through `push`.

- [ ] **Step 7: Commit**

```bash
git add README.md STATUS.md
git commit -m "docs: document the per-tab fallback for large multi-tab docs"
```
