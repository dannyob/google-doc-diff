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

### `Unsupported` node (degraded-but-typed fallback)

`Unsupported(kind: str, raw: dict)` may appear at *any* position in the tree
(as a Block or as a run-level Inline). It exists so the AST builder can
handle Docs API features the spec hasn't addressed (new Workspace features,
deprecated edge cases) without crashing. Both serializers render it as:

- Markdown: a fenced div / span with `class="gd-unsupported"` carrying
  `data-kind="..."` and `data-raw="<JSON>"`. Visible to humans; survives
  round-trip.
- HTML: same, as `<div class="gd-unsupported" ...>` or
  `<span class="gd-unsupported" ...>`.

When the AST builder emits an `Unsupported` node, it logs a single line to
stderr: `unsupported element kind=X (will be preserved as opaque blob)`.

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

**Hash collision policy.** `hash8` (8 hex chars, 32 bits) gives ~10⁻⁵ collision
risk at 100 distinct styles per doc — fine in practice. The emitter detects
collisions during CSS generation: if two distinct style descriptors hash to
the same `hash8`, it bumps to `hash10` (40 bits) for the entire doc and logs
a single warning line. No silent corruption.

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

**Replacement suggestions** (one Docs suggestion that both deletes range A and
inserts text B at the same position) are common. The Docs API exposes them as
a `deleteContentRange` and an `insertText` operation sharing one suggestion ID.
They surface in the AST as a `SuggestionDel` and a `SuggestionIns` node, both
keyed to the same `suggestion_id`, occupying adjacent positions. The emitter
detects this pair and renders with CriticMarkup's substitution syntax:

```markdown
We need to ship {~~yesterday~>last week~~}[^s-REPL1] features.

[^s-REPL1]: ::: {.gd-suggestion data-kind="replacement" data-author="alice@" data-created="2026-05-02T09:00:00Z"} :::
```

One footnote definition per `suggestion_id`, regardless of insertion / deletion
/ replacement kind. HTML emits `<del>OLD</del><ins>NEW</ins>` with the same
`data-suggestion-id` on both elements.

### Multi-tab as fenced sections

A multi-tab doc emits one flat `.md` with each tab as a top-level fenced div.
Nesting depth maps to colon count: a depth-0 (top-level) tab uses 3 colons
(`:::`); each level of nesting adds one colon. So a depth-1 child tab uses
`::::`, depth-2 grandchild uses `:::::`, etc. Pandoc requires the close fence
to have ≥ as many colons as the open fence; we always emit close fences with
*exactly* the same count to keep parsing unambiguous and the markup
deterministic.

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
source_mode: pull              # one of: pull | replay
---
```

**Field semantics:**

- `captured_at` — for `pull`, this is wall-clock time of the pull. For
  `replay`, this is the **event timestamp** (not wall-clock), so re-running
  `replay --resume` produces byte-identical files for already-committed
  events.
- `source_mode` — `pull` for current/specific-revision pulls, `replay` for
  files emitted by the replay event walker. Read by `gdoc diff` to warn the
  user when comparing a `pull` artifact against a `replay` artifact (the
  diff will be misleadingly large because `replay` strips suggestions).
- `comments_preserved` / `suggestions_preserved` — read by `diff` (warns on
  mismatched values), by `replay` (refuses to overwrite a `pull` artifact
  with a `replay` one in non-empty target paths without `--force`), and by
  humans skimming the file. Not decorative: emitters set them per-source,
  consumers branch on them.

### Determinism and round-trip readiness

Both serializers enforce two properties in v1:

1. **Determinism.** Same AST → byte-identical output, every run. Class names
   sorted, attributes alphabetized, no random IDs. Critical for clean git diffs.

2. **Round-trip readiness.** A *structural attribute audit* asserts that every
   stable ID and metadata attribute in the AST appears at least once in each
   serializer's output. This is testable in v1 without the v2 parsers and
   catches the failure mode the round-trip tests are guarding against (silent
   attribute drops). Concretely, for each fixture:
   - Every `comment_id`, `suggestion_id`, `footnote_id`, `tab_id`,
     `bookmark_id`, `named_range_id`, `image_id`, and synthesized class name
     in the AST appears in both the `.md` and `.html` outputs.
   - The set of IDs in the `.md` equals the set in the `.html`.
   - For every comment with replies, the reply count and reply authors appear
     in both outputs.

   True equality `parse_md(emit_md(ast)) == ast` is a v2 acceptance test
   gating the round-trip release; v1 tests for it are marked `xfail` so the
   property surfaces as a tracked TODO without breaking the build.

### Image handling

Default: link to Drive's exposed image URL (best-effort; URLs may rotate
within hours). With `--extract-assets`, images are downloaded to
`<slug>.assets/`, named by stable Drive image ID, and links rewritten.

The default is "no extract" — chosen to keep the common one-shot use case
(`gdoc pull` once, share or feed to AI immediately) cheap and clutter-free.
For archival or git-stored use, `--extract-assets` is required because URL
rot makes the no-extract path useless after a few hours. To make this hard
to miss, `gdoc pull` prints a stderr warning when at least one image is
present and `--extract-assets` was not specified:

> Warning: 3 image URLs may rotate. For archival / git-stored use, re-run
> with --extract-assets.

A future config option (`~/.config/gdoc-diff/config.toml`) can flip the
default per user, but is out of scope for v1.

## CLI

### Commands

```
gdoc pull <doc-id-or-url> [--out PATH] [--extract-assets]
                          [--revision REV_ID | --at ISO_TIME]
                          [--color=auto|always|never]
    Pull current (or specific historical) revision; write a single .md.
    When at least one image is present and --extract-assets is not set,
    prints a warning to stderr: "Image URLs may rotate. For archival /
    git-stored use, re-run with --extract-assets."
    Exit 0 on success.

gdoc revisions <doc-id-or-url> [--since ISO] [--until ISO]
                               [--format json|table]
    List revisions: id, modifiedTime, lastModifyingUser. Exit 0.

gdoc diff <doc-id-or-url> [PATH.md] [--revision REV_ID]
                          [--color=auto|always|never]
    Pull current (or given revision); show unified diff against PATH.md.
    Read-only; never writes. Color is on for TTY stdout by default.
    Exit codes: 0 = files match; 1 = differences found; 2 = error
    (network / auth / file missing). Matches the convention of `diff(1)`
    so it's pipeline-friendly.

gdoc replay <doc-id-or-url> --since ISO [--until ISO]
                            [--out PATH] [--extract-assets]
                            [--commit] [--squash-by-author DURATION]
                            [--include-comments | --no-include-comments]
                            [--dry-run] [--resume]
    Walk revisions + comment events, merge into one chronological timeline,
    write the .md for each event. With --commit, create one git commit per
    event in the cwd, authored by the event's user with the event's timestamp.
    DURATION grammar: Go-style, e.g. 5m, 300s, 1h, 2h30m. --resume reads
    .gdoc-replay-state.json and continues from the first uncommitted event;
    fails with a clear error if the timeline hash has changed since the
    interrupted run (use --restart in that case).

gdoc auth login [--credentials-file PATH]
    Run the OAuth flow. Opens browser; caches refresh token at
    ~/.config/gdoc-diff/token.json.

gdoc auth logout
    Delete the cached refresh token. Does not revoke server-side; visit
    https://myaccount.google.com/permissions to do that.

gdoc auth status
    Print: credentials path, token path, granted scopes, account email,
    "expires in N seconds" for the cached access token (if any).
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
1. Determine the prose state: most recent `prose_change` with
   `timestamp ≤ event.timestamp`. Fetch via `exportLinks['text/markdown']`
   once per unique revision; cache.
2. Build a partial AST from that markdown via `ast/from_google_md.py`
   (the v1 lossy reader for Google's native markdown export — distinct
   from the stubbed `parse/markdown.py` round-trip parser).
3. Determine the comment state: all comments + replies with
   `timestamp ≤ event.timestamp`, with current resolved/deleted flags applied.
4. Re-anchor each comment to the prose AST via `quotedFileContent.value`
   substring search (algorithm in next section). If not found, mark
   `orphaned: true` and render at end of tab/doc with a note.
5. Merge comments into the AST; set `Document.suggestions = {}` (always
   empty in replay) and `comments_preserved: true`,
   `suggestions_preserved: false` in the frontmatter.
6. Set `Document.captured_at = event.timestamp` (NOT wall-clock —
   determinism for replay re-runs depends on this; see Frontmatter section).
7. Emit `.md`; write to output path (overwriting prior).
8. With `--commit`: stage the file, commit with event author
   (`lastModifyingUser.emailAddress` for prose events; `author.emailAddress`
   for comment events; falls back to `unknown@gdoc-diff` if missing) and
   timestamp.

Suggestions are absent from all replay events. The frontmatter on each
replayed `.md` carries `suggestions_preserved: false`.

### Replay state file (`.gdoc-replay-state.json`)

`replay` writes its state to `.gdoc-replay-state.json` in the cwd before the
first commit and after every committed event. Schema:

```json
{
  "doc_id": "1aBc...",
  "since": "2026-05-01T00:00:00Z",
  "until": "2026-05-09T00:00:00Z",
  "out_path": "doc.md",
  "extract_assets": false,
  "include_comments": true,
  "timeline_hash": "sha256:...",
  "events": [
    {"id": "rev-1",  "kind": "prose_change",   "timestamp": "...",
     "author": "alice@example.com",         "status": "committed", "git_sha": "abc123..."},
    {"id": "rev-5",  "kind": "prose_change",   "timestamp": "...",
     "author": "alice@example.com",         "status": "committed", "git_sha": "def456..."},
    {"id": "cmt-AAA1-create", "kind": "comment_create", "timestamp": "...",
     "author": "alice@example.com",     "status": "pending"}
  ]
}
```

`timeline_hash` is `sha256` of the canonical JSON of `[event.id, event.kind,
event.timestamp, event.author]` for every event in chronological order.

`--resume` re-fetches revisions and comments, recomputes the timeline and
hash, then:
- If the new hash matches the stored hash: continue from the first event with
  `status != "committed"`.
- If the new hash does NOT match (new comments or revisions appeared, or
  existing ones were edited/deleted since the interrupted run): exit 2 with a
  clear message instructing the user to either delete the state file and
  re-run, or pass `--restart` to discard and start over.

`replay` refuses to start a new run if `.gdoc-replay-state.json` exists with
uncommitted events, unless `--resume` or `--restart` is passed. This prevents
silent overwrites of an interrupted run's state.

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
│   │   ├── from_docs_json.py      # Docs API JSON → AST (current-revision; full fidelity)
│   │   └── from_google_md.py      # Google's NATIVE markdown export → AST (lossy)
│   │                              #   v1 path used ONLY by replay for historical
│   │                              #   revisions. Distinct from parse/markdown.py
│   │                              #   which targets our flavored Pandoc markdown.
│   ├── styles/
│   │   ├── classes.py             # named-style + synthesized class generation
│   │   └── css.py                 # CSS rule emission
│   ├── emit/
│   │   ├── markdown.py            # AST → Markdown text
│   │   └── html.py                # AST → HTML text
│   ├── parse/                     # v2 round-trip parsers; STUBS in v1
│   │   ├── markdown.py            # parses our flavored Pandoc-flavor MD → AST
│   │   └── html.py                # parses our emitted HTML → AST
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

3. **Round-trip readiness tests.** For every fixture, run the structural
   attribute audit defined in "Determinism and round-trip readiness":
   every stable ID and attribute in the AST appears in both serializer
   outputs; ID sets in `.md` and `.html` match; reply counts and authors
   appear in both. These tests run *without* the v2 parsers and catch the
   silent-drop failure mode that round-trip equality would catch.

   True parse-emit equality (`parse_md(emit_md(ast)) == ast`) is the v2
   acceptance gate; v1 has the test scaffolding as `xfail` markers so the
   property is tracked but doesn't block v1.

4. **Determinism tests.** Same input AST → byte-identical output across two
   runs. Catches dict-ordering, hash-randomization, timestamp leaks.

5. **Canary tests against live API.** `make canary` invokes a Python entry
   point that first checks for `~/.config/gdoc-diff/credentials.json` and
   `~/.config/gdoc-diff/token.json`. Missing → exit 0 with `skip: no
   credentials configured`. Present → run the live checks and exit non-zero
   only on real API breakage. CI runs `make canary` and treats skip as pass.
   The live checks verify:
   - Drive v2 `revisions.list` still returns `exportLinks`
   - The `text/html` and `text/markdown` URLs still respond 200
   - The Drive Comments API still returns `quotedFileContent`
   - The set of MIME types returned in `exportLinks` still includes the ones
     we depend on (`text/html`, `text/markdown`).
   The canary test doc ID is configurable via `GDOC_CANARY_DOC_ID` env var.
   Failing canary → file an issue; don't silently break users.

6. **Replay integration test** against a small dedicated test doc with known
   history. Asserts the produced commit graph matches an expected sequence of
   revision-IDs and comment events in chronological order.

## Error handling

| Failure mode | Behavior |
|---|---|
| `429 Too Many Requests` | Exponential backoff with jitter (1s, 2s, 4s, 8s, max 60s), up to 5 retries; then surface a clear error |
| `401 Unauthorized` | "Token expired or revoked. Run `gdoc auth login` to re-authorize." |
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
