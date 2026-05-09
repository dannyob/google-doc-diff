# google-doc-diff — Design Spec

**Status:** Draft, awaiting reviewer pass
**Date:** 2026-05-09
**Author:** Brainstormed with Danny O'Brien

## Summary

A Python CLI that pulls Google Docs into high-fidelity Markdown and HTML on
disk, suitable for storing in git, reading by AI tools, and producing readable
diffs. v1 is one-way (Doc → local), but the on-disk format preserves enough
metadata (stable IDs, named-style classes, comment threads, suggestion ranges)
that a future v2 can push edits back into the Doc.

## Motivation

Google Docs is the canonical surface for a lot of Danny's writing and
collaboration. Existing tools either (a) export lossily and one-way (Google's
own File → Download → Markdown, the `gd2md-html` add-on, `mangini/gdocs2md`,
`AnandChowdhary/docs-markdown`), (b) export to plain text without formatting
(`doc2git`), or (c) capture only revision metadata without content
(`drive.revisions.list`). None do all four of: round-trippable identity,
multi-tab support, first-class diff-visible comments, and AI-friendly markup.

This tool fills that gap.

### Prior art surveyed

| Tool | Direction | Comments | Suggestions | Tabs | Round-trip |
|---|---|---|---|---|---|
| Google native MD export (2024) | one-way | ✗ | ✗ | ✗ | ✗ |
| `gd2md-html` (Docs add-on) | one-way | ✗ | ✗ | ✗ | ✗ |
| `googleworkspace/google-docs-hast` | one-way | ✗ | ✗ | ✗ | ✗ |
| `behdad/gdocs-me-up` | one-way | ✗ | ✗ | ✗ | ✗ |
| `Mr0grog/google-docs-to-markdown` | one-way | ✗ | ✗ | ✗ | ✗ |
| `HartreeWorks/comment-exporter` | one-way | resolved only, text | ✗ | ✗ | ✗ |
| `repography/doc2git` | one-way (history) | ✗ | ✗ | ✗ | ✗ |
| Pandoc `.docx ↔ md` | partial round-trip | partial | partial | n/a | docx only |
| **this tool (v1)** | one-way Doc→local | ✓ first-class | ✓ in current pull | ✓ flat fenced divs | metadata preserved for v2 |

## v1 Scope

**In:**
- Pull a current-revision Doc to a single Pandoc-flavor `.md` with embedded
  `<style>` block.
- Pull historical revisions individually, with author/timestamp metadata.
- `replay` walks revisions plus Drive Comments API events, merges them into a
  chronological event timeline, and (optionally) creates one git commit per event.
- `diff` shows colored unified diff between current Doc state and local `.md`.
- `revisions` lists revision metadata.
- Optional `--extract-assets` writes images to a `<slug>.assets/` directory.

**Out (deliberately deferred):**
- Pushing edits back to Docs (parser stubs exist; no CLI surface).
- Concurrent-edit merge.
- Suggestions in `replay` (no historical API exists).
- Drawings (no API access).
- Equation rendering beyond LaTeX pass-through.
- Real-time sync, daemon, file-watching.
- Sheets, Slides, Forms.
- Multi-account at once.
- Web preview server.
- Heuristic detection of callouts/quotes from 1-cell tables.

## Data Model (AST)

A small Python dataclass tree mirrors what the Docs API delivers, normalized
and with stable IDs as a first-class concern.

### Top-level

```python
@dataclass
class Document:
    doc_id: str               # Drive file ID
    title: str
    revision_id: str          # the revision this AST was built from
    drive_url: str
    captured_at: datetime
    schema_version: int
    last_modifying_user: str | None
    tabs: list[Tab]                       # single-tab docs get one synthesized tab
    comments: dict[str, Comment]          # collection, keyed by stable ID
    suggestions: dict[str, Suggestion]
    footnotes: dict[str, Footnote]
    named_styles: dict[str, StyleDescriptor]   # the 9 fixed Docs styles + their CSS
    list_definitions: dict[str, ListDescriptor]
    inline_objects: dict[str, InlineObject]    # images keyed by Drive object ID
    css_classes: dict[str, str]                # class_name -> CSS rule (derived)
```

### Tabs and blocks

```python
@dataclass
class Tab:
    tab_id: str
    title: str
    level: int                    # 0 = top-level
    parent_tab_id: str | None
    children: list[Tab]
    blocks: list[Block]
```

Block types (`Heading`, `Paragraph`, `ListItem`, `Table`, `Image`, `CodeBlock`,
`HorizontalRule`, `PageBreak`, `SectionBreak`, `EquationBlock`,
`TableOfContents`) carry runs (inline content) plus block-level attributes
(level, list ID, classes).

### Inline (run-level) nodes

`Run(text, formatting)`, `CommentAnchor(comment_id, runs)`,
`SuggestionIns(suggestion_id, runs)`, `SuggestionDel(suggestion_id, runs)`,
`FootnoteRef(footnote_id)`, `BookmarkAnchor(bookmark_id)`,
`NamedRangeAnchor(named_range_id)`, `SmartChip(kind, data)`, `InlineEquation`,
`LineBreak`.

### Cross-cutting collections

Comments, suggestions, and footnotes are not embedded in the tree. They live in
`Document.comments`, `.suggestions`, `.footnotes`, keyed by stable ID and
referenced from anchor nodes in the tree. One source of truth per object; no
duplication across renderings.

### Stable ID strategy

| Object | ID prefix | Source |
|---|---|---|
| Comments | `c-{drive_comment_id}` | Drive Comments API |
| Suggestions | `s-{docs_suggestion_id}` | Docs API |
| Footnotes | `fn-{docs_footnote_id}` | Docs API |
| Tabs | `t-{docs_tab_id}` | Docs API |
| Bookmarks | `bm-{docs_bookmark_id}` | Docs API |
| Named ranges | `nr-{docs_named_range_id}` | Docs API |
| Heading anchors | `h-{docs_heading_id}` | Docs API |
| Images / inline objects | `i-{docs_object_id}` | Docs API |

Tables and paragraphs have no API ID. v1 does not synthesize one. v2 round-trip
will fingerprint by `(parent_tab_id, position_in_tab, content_hash)`. Captured
as a comment in the AST module, not built now.

### CSS class strategy

Google Docs has nine fixed named paragraph styles: Normal, Title, Subtitle,
Heading 1–6. No user-named styles, no user-named character or list styles.

**Default emission is the bare element** (`<h1>`, `<h2>`, `<p>`, `<ul>`, etc.).
A class attaches only when:

1. The named style cannot be expressed by the bare element (Title, Subtitle).
2. The element has inline overrides that diverge from the named style for its
   type — synthesized class `.gd-style-{hash8}` where `hash8` is the first 8
   hex chars of a stable hash of the style descriptor. Identical descriptors
   collapse to the same class across the doc.

CSS rules pair the element selector with the explicit class so either form
gets the same styling:

```css
h1, .gd-heading-1 { font-size: 20pt; color: #1155cc; font-weight: 700; }
h2, .gd-heading-2 { font-size: 16pt; color: #1155cc; font-weight: 700; }
p { font-size: 11pt; line-height: 1.15; }
h1.gd-title    { font-size: 28pt; text-align: center; font-weight: 400; }
p.gd-subtitle  { font-size: 14pt; color: #666666; }
.gd-style-a1b2c3d4 { font-family: "Source Code Pro"; background: #f3f3f3; }
ul.gd-list-7e9f    { list-style-type: square; }
```

Style names like `.gd-heading-1` derive from the doc's *current* `namedStyles`.
Renaming a style in the Doc produces a one-time large diff; that's correct
behavior, not a bug.

## Serializers

Both serializers consume the AST and emit equivalent semantic content. They
share metadata-emission helpers (author/timestamp formatting, ID prefix
namespacing). They are tested as inverses: `parse_md(emit_md(ast)) == ast` and
`parse_html(emit_html(ast)) == ast` for every fixture.

### Feature → emission mapping

| AST element | Markdown | HTML |
|---|---|---|
| `Tab` | `::: {.gd-tab data-tab-id="t-…" data-title="…" data-level="0"} … :::` (nested via `::::`) | `<section class="gd-tab" data-tab-id="t-…" data-title="…">…</section>` |
| `Heading n` | `# Title` (bare; class only on inline override) | `<h1>…</h1>` (bare) |
| `Paragraph` (NORMAL) | plain prose | `<p>…</p>` |
| `Paragraph` (TITLE / SUBTITLE) | `# … {.gd-title}` / `::: gd-subtitle … :::` | `<h1 class="gd-title">…</h1>` / `<p class="gd-subtitle">…</p>` |
| `Run` w/ inline overrides | `[text]{.gd-style-{hash}}` | `<span class="gd-style-{hash}">text</span>` |
| `Run` w/ link | `[text](url){#anchor}` if anchored | `<a href="url" id="anchor">text</a>` |
| `CommentAnchor` (long) | `[anchored phrase]{.gd-cmt-anchor #c-ID}[^c-ID]` + footnote def | `<span class="gd-cmt-anchor" data-comment-id="c-ID">…</span>` + `<aside class="gd-comment">…</aside>` |
| `CommentAnchor` (short, single-graf) | `[phrase]{.gd-cmt-anchor #c-ID}^[**alice@** date: text]` | same as long form |
| `SuggestionIns` | `{++inserted text++}[^s-ID]` + def | `<ins data-suggestion-id="s-ID" data-author="…" data-created="…">…</ins>` |
| `SuggestionDel` | `{--deleted text--}[^s-ID]` + def | `<del data-suggestion-id="s-ID" …>…</del>` |
| `FootnoteRef` | `[^fn-ID]` + def | `<sup><a href="#fn-ID">n</a></sup>` |
| `Image` | `![alt](src){#i-ID width=… height=…}` | `<img id="i-ID" src="…" alt="…" width="…" height="…">` |
| `Table` (simple) | Pandoc pipe table | `<table>…</table>` |
| `Table` (with span/fill) | raw `<table>` HTML inside markdown | same `<table>` |
| `CodeBlock` | fenced `` ```python … ``` `` | `<pre><code class="language-python">…</code></pre>` |
| `SmartChip` (person/date/file) | `[@alice@…]{.gd-chip data-kind="person" data-email="…"}` | `<span class="gd-chip gd-chip-person" data-email="…">@alice@…</span>` |
| `Bookmark` / `NamedRange` | `[]{#bm-ID}` | `<a id="bm-ID"></a>` |
| `EquationInline` / Block | `$…$` / `$$…$$` | `<span class="gd-equation">…</span>` / `<div class="gd-equation-block">…</div>` |

### Comments as Pandoc footnotes

A short single-paragraph comment becomes an inline note at the anchor:

```markdown
The proposal is [unfinished]{.gd-cmt-anchor #c-AAA1}^[**alice@** 2026-05-01: needs the auth section.]
```

A longer or threaded comment becomes a reference-style footnote, definition
placed at end of the containing tab (or end of doc, if not multi-tab):

```markdown
The proposal is [unfinished]{.gd-cmt-anchor #c-AAA1}[^c-AAA1] in its current form.

[^c-AAA1]: ::: {.gd-comment data-author="alice@" data-created="2026-05-01T12:00:00Z" data-resolved="false"}
    **alice@** 2026-05-01: needs the auth section.

    > **bob@** 2026-05-02: agreed, I'll draft it.

    > **alice@** 2026-05-03 (resolved): looks good.
    :::
```

Footnote IDs namespace by kind: `c-…` for comments, `fn-…` for real Doc
footnotes, `s-…` for suggestions. The class on the definition (`.gd-comment` /
`.gd-footnote` / `.gd-suggestion`) tells parsers and stylesheets which kind.

### Suggestions as CriticMarkup + footnote-style metadata sidecar

```markdown
We need to add {++the auth section++}[^s-XYZ1] before {--shipping--}[^s-XYZ2].

[^s-XYZ1]: ::: {.gd-suggestion data-kind="insertion" data-author="alice@" data-created="2026-05-02T09:00:00Z"} :::
[^s-XYZ2]: ::: {.gd-suggestion data-kind="deletion"  data-author="alice@" data-created="2026-05-02T09:00:00Z"} :::
```

In HTML, the same content uses semantic `<ins>` / `<del>` with data attributes
inline, no footnote workaround needed.

### Multi-tab as fenced sections

A multi-tab doc emits one flat `.md` with each tab as a top-level fenced div.
Nested tabs nest via additional colon counts (`::::` outer, `:::` inner).

### Frontmatter

```yaml
---
doc_id: 1aBcDeFGhIjKLMNoPqRsTuVwXyZ
title: My RFC
revision_id: rev_2026-05-09T14:30:00Z_abc
drive_url: https://docs.google.com/document/d/1aBcDeFGhIjKLMNoPqRsTuVwXyZ/edit
captured_at: 2026-05-09T14:35:12Z
schema_version: 1
last_modifying_user: alice@example.com
comments_preserved: true       # false on revisions pulled via exportLinks
suggestions_preserved: true    # false on any historical pull
---
```

### Determinism and round-trip

Both serializers enforce two properties:

1. **Determinism.** Same AST → byte-identical output, every run. Class names
   sorted, attributes alphabetized, no random IDs. Critical for clean git diffs.

2. **Round-trip equivalence.** Test corpus round-trips through both formats;
   diffs against the source AST must be empty. Even though parsers are stubs in
   v1, the round-trip property is enforced now to catch silent attribute drops.

### Image handling

Default: link to Drive's exposed image URL (best-effort; URLs may rotate over
hours). With `--extract-assets`, images are downloaded to `<slug>.assets/`,
named by stable Drive image ID, and links rewritten. Recommended for
git-stored docs because URL rot makes the no-extract path useless after a few
hours of cache expiry.

## CLI

### Commands

```
gdoc pull <doc-id-or-url> [--out PATH] [--extract-assets]
                          [--revision REV_ID | --at ISO_TIME]
    Pull current (or specific historical) revision; write a single .md.

gdoc revisions <doc-id-or-url> [--since ISO] [--until ISO] [--format json|table]
    List revisions: id, modifiedTime, lastModifyingUser.

gdoc diff <doc-id-or-url> [PATH.md] [--revision REV_ID]
    Pull current (or given revision); show colored unified diff against PATH.md.
    Read-only; never writes.

gdoc replay <doc-id-or-url> --since ISO [--until ISO]
                            [--out PATH] [--extract-assets]
                            [--commit] [--squash-by-author DURATION]
                            [--include-comments | --no-include-comments]
                            [--dry-run] [--resume]
    Walk revisions + comment events, merge into one chronological timeline,
    write the .md for each event. With --commit, create one git commit per
    event in the cwd, authored by the event's user with the event's timestamp.
    --squash-by-author 5m coalesces adjacent same-author revision events
    inside a 5-minute window. --resume continues a previously interrupted run.
```

### Auth

Standalone OAuth, no dependency on the `gog` tool.

- User creates a Google Cloud project once (enables Docs + Drive APIs),
  downloads `credentials.json`, drops at `~/.config/gdoc-diff/credentials.json`.
- First command triggers a browser OAuth flow; refresh token cached at
  `~/.config/gdoc-diff/token.json`.
- v1 scopes: `drive.readonly`, `documents.readonly`. Drive scope alone
  suffices for revision walking and `exportLinks`.
- v2 round-trip will require `documents` (write).
- `--credentials-file` flag overrides the default path.

If `gog` is installed and authorized, the README documents how to import its
refresh token via `gog auth tokens export` for users who already have it set
up. The tool does not depend on `gog` at runtime.

### Implementation note: revision export

Per-revision content uses **Drive API v2** (not v3) — verified against a live
doc on 2026-05-09. The `revisions.list(fileId)` call returns each revision
with an `exportLinks` field mapping MIME types to URLs. Available formats
include `text/html`, `text/markdown`, `text/plain`, `application/pdf`,
`application/rtf`, ODT, DOCX, EPUB, ZIP. The URLs accept
`Authorization: Bearer <oauth-token>` and return content directly.

This is a **documented** API surface, not undocumented — the v3 docs simply
don't expose this functionality, so v3 alone is insufficient. Drive v2 is
officially deprecated but actively maintained because v3 has no replacement
for revision content export. The spec accepts this dependency and adds a
canary test.

### Verified limitations of the revision-export path

- **Sparse revisions.** The API returns "kept" revisions only — Google
  compacts most auto-saves away. The test doc (Doc ID `(redacted)`) had
  revision IDs `1, 5, 27, 29, 925, 1531, 9245` with gaps confirming
  compaction. `replay` works on what's kept; missing intermediate edits
  cannot be recovered.
- **Comments and suggestions stripped.** The HTML and Markdown export bodies
  contain no structured comment or suggestion markup. They live only in the
  Docs API's `documents.get` JSON for the *current* revision, plus the Drive
  Comments API for comment history (which `replay` queries separately).
- **Rate limits.** HTTP 429 hit after 5 sequential fetches against the test
  doc. `api.py` implements exponential backoff with jitter (1, 2, 4, 8, max
  60s) up to 5 retries; `replay` adds a configurable inter-request delay
  (default 1s).
- **`lastModifyingUser` flakiness.** Author metadata for the same revision ID
  varied between runs in the sidequest. Treated as best-effort; not used as a
  source of truth beyond commit attribution.
- **Drive v2 sunset risk.** Canary test catches breakage before users do.

### Replay design (the unified event timeline)

Phase 1: in parallel, fetch
- `drive.revisions.list(fileId)` → revision metadata
- `drive.comments.list(fileId)` with full reply expansion → comments + replies

Phase 2: build a unified event timeline:

| Event kind | Source | Author | Timestamp |
|---|---|---|---|
| `prose_change` | revision | `lastModifyingUser` | `modifiedDate` |
| `comment_create` | comment | `author` | `createdTime` |
| `comment_edit` | comment (modifiedTime ≠ createdTime) | `author` | `modifiedTime` |
| `comment_delete` | comment (`deleted=true`) | `author` | `modifiedTime` |
| `reply_create` | reply | `author` | `createdTime` |
| `reply_resolve` | reply (`action=resolve`) | `author` | `createdTime` |
| `reply_reopen` | reply (`action=reopen`) | `author` | `createdTime` |

Sort chronologically. Apply `--squash-by-author` window to coalesce adjacent
same-author `prose_change` events.

Phase 3: for each event in order:
1. Determine the prose state: most recent `prose_change` with `timestamp ≤ event.timestamp`. Fetch via `exportLinks['text/markdown']` (or HTML) once per unique revision; cache.
2. Determine the comment state: all comments + replies with `timestamp ≤ event.timestamp`, with current resolved/deleted flags applied.
3. Re-anchor each comment to the prose state via `quotedFileContent.value`
   substring search. If not found, mark `orphaned: true` and render at end of
   tab/doc with a note.
4. Build the AST; emit `.md`; write to output path (overwriting prior).
5. With `--commit`: stage the file, commit with event author and timestamp.

Suggestions are absent from all replay events. The frontmatter on each
replayed `.md` carries `suggestions_preserved: false`.

### Re-anchoring algorithm

Drive Comments API returns `quotedFileContent.value` — a snippet of the text
the comment was anchored to at creation time. To anchor against a historical
prose state:

1. Search for the snippet as a substring of the prose text.
2. If found exactly once: anchor there.
3. If found multiple times: pick the closest match by index to the position
   recorded in `anchor` (when present); else pick the first.
4. If not found: mark the comment `orphaned: true` and render in the tab's
   end-of-section comment block with `(orphaned: original anchor "…")`.

## Project layout

```
google-doc-diff/
├── pyproject.toml
├── Makefile                       # `make test`, `make lint`, `make canary`
├── README.md
├── docs/
│   └── superpowers/specs/         # this file lives here
├── src/google_doc_diff/
│   ├── __main__.py                # `python -m google_doc_diff`
│   ├── cli.py                     # argparse subcommands
│   ├── auth.py                    # OAuth flow, credential/token storage
│   ├── api.py                     # Docs/Drive wrappers; rate-limit handling
│   ├── ast/
│   │   ├── nodes.py               # dataclasses: Document, Tab, Block, Run, …
│   │   ├── from_docs_json.py      # Docs API JSON → AST  (current-revision)
│   │   └── from_md.py             # parse Google's exported markdown → AST
│   │                              #   (used by replay for historical revisions)
│   ├── styles/
│   │   ├── classes.py             # named-style + synthesized class generation
│   │   └── css.py                 # CSS rule emission
│   ├── emit/
│   │   ├── markdown.py            # AST → Markdown text
│   │   └── html.py                # AST → HTML text
│   ├── parse/                     # stubs for v2 round-trip
│   │   ├── markdown.py
│   │   └── html.py
│   ├── replay/
│   │   ├── timeline.py            # event-merger + sorter
│   │   ├── reanchor.py            # quotedFileContent → position
│   │   └── runner.py              # for-each-event executor
│   └── git.py                     # thin shell-out wrapper for `git add`/`commit`
└── tests/
    ├── fixtures/
    │   ├── docs/                  # captured Docs API JSON
    │   ├── exported/              # captured per-revision HTML and markdown
    │   ├── comments/              # captured Drive Comments API responses
    │   └── expected/              # expected .md and .html outputs
    ├── unit/                      # per-module
    └── round_trip/                # serializers' inverse property
```

Python ≥ 3.11, `uv`-managed venv, build via `uv build`. Per Danny's template
in `~/Public/src/dob/py-template/template/`.

## Testing

1. **Unit tests, per module** — AST builder, classes synthesizer, both
   serializers, timeline merger, re-anchorer, git-commit driver. Hand-built
   fixtures in isolation.

2. **Golden-file fixtures.** `tests/fixtures/docs/` holds sanitized Docs API
   JSON; `tests/fixtures/expected/` holds expected `.md` + `.html`.
   `make capture-fixture DOC_ID=… NAME=…` adds new fixtures from a live doc.

3. **Round-trip property tests.** For every fixture:
   - `parse_md(emit_md(ast)) == ast`
   - `parse_html(emit_html(ast)) == ast`
   - `emit_md(parse_md(emit_md(ast))) == emit_md(ast)` (idempotence)

   Parsers are stubbed in v1 but exercised here so attribute drops surface
   immediately.

4. **Determinism tests.** Same input AST → byte-identical output across two
   runs. Catches dict-ordering, hash-randomization, timestamp leaks.

5. **Canary tests against live API.** `make canary`, manually run, skipped in
   CI without credentials. Verifies:
   - Drive v2 `revisions.list` still returns `exportLinks`
   - The `text/html` and `text/markdown` URLs still respond 200
   - The Drive Comments API still returns `quotedFileContent`
   - Failing canary → file an issue; don't silently break users.

6. **Replay integration test** against a small dedicated test doc with known
   history. Asserts the produced commit graph matches an expected sequence of
   revision-IDs and comment events in chronological order.

## Error handling

| Failure mode | Behavior |
|---|---|
| `429 Too Many Requests` | Exponential backoff with jitter (1s, 2s, 4s, 8s, max 60s), up to 5 retries; then surface a clear error |
| `401 Unauthorized` | "Token expired or revoked. Run `gdoc auth login`." |
| `403 Forbidden` (scope) | "Insufficient OAuth scopes. Need: ___. Current: ___." |
| `403 Forbidden` (role) | "Need writer/owner role on this doc to read its revision history." |
| `404 Not Found` | Distinguish "doc doesn't exist" vs. "doc exists but no access" via Drive metadata probe |
| Drive v2 sunset | Hard fail with migration message + link to issue tracker; canary catches first |
| Malformed Docs JSON | Log offending element, emit AST node `Unsupported(kind, raw_blob)`, continue |
| Network / DNS errors | Retry 3× with backoff; then surface |
| `replay` partial failure | Each event is its own commit; failures past commit N leave 1..N intact, write `_unfinished_replay.txt`, refuse to retry without `--resume` |
| Working tree dirty + `--commit` | Refuse to run; print one-line "commit or stash first" message |

Logging: single `--verbose` flag; structured JSON-line logs to stderr with
`--log-format json` for scripting. Default is human-readable summary.

## Out of scope (v1)

(Repeated here for emphasis.)

- Push back to Docs (write path). Parsers stubbed; no CLI surface. v2 work.
- Concurrent-edit merge. Explicitly v3+ if ever.
- Suggestions in `replay`. No historical API exists.
- Drawings. No API access.
- Equation rendering beyond LaTeX pass-through.
- Real-time sync, daemon, file-watching.
- Sheets, Slides, Forms.
- Multi-account at once.
- Web preview server.
- Heuristic detection of callouts/quotes from 1-cell tables.

## Future work

- **v2: round-trip back to Docs.** Implement `parse_md` and `parse_html` (the
  stubs); add `gdoc push <doc-id> [path.md]` that diffs the local AST against
  the live Doc and emits `documents.batchUpdate` operations to apply the
  changes. The hard part is paragraph and table fingerprinting, since neither
  has stable IDs from the Docs API. Plan: `(parent_tab_id, position_in_tab,
  content_hash)` triple, with fallback to nearest-neighbor matching by content
  similarity.

- **v3+: concurrent-edit merge.** Treat the local AST as one branch and the
  live Doc as another; produce a merge AST. Out of scope for the foreseeable
  future.

- **`gdoc init` + sync state directory.** Track doc-id ↔ path mappings and
  last-known revisions for a "this folder of Docs is mirrored in this git
  repo" workflow without manual scripting.

- **Smart-chip rewriting.** Smart chips currently round-trip as opaque spans.
  A future pass could rewrite them to first-class Markdown links where
  semantically equivalent (e.g. file chips → `[filename](drive-url)`).
