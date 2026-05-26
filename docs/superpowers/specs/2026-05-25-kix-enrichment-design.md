# Kix enrichment layer — design spec

Date: 2026-05-25
Branch: `worktree-round-trip`
Status: approved

## Problem

The public Google Docs API (`documents.get`) provides a good structural
representation of a document but omits several details the editor knows
internally:

- **Comment anchors** are opaque `kix.<id>` strings. The API gives the
  anchor's quoted text but not its structural position, forcing text-match
  heuristics that break on repeated snippets.
- **Voting chips** appear as `U+E907` placeholders with no metadata. The
  emoji, voter list, and per-voter IDs are invisible.
- **Suggestion colors** (the per-author highlight tint) are not exposed.
- **Smart canvas widgets** (dropdowns, etc.) are similarly opaque.

The Google Docs editor ("Kix") bootstraps the full document as an
operational-transformation (OT) op stream embedded in the `/edit` HTML page.
This stream contains all of the above. It is reachable without OAuth — Chrome's
first-party cookies are sufficient.

## Approach

**Kix is an optional read-side enrichment layer.** OAuth remains the primary
read and write path. When Chrome cookies are available, kix decorates the
existing AST with details the Docs API omits. When cookies are unavailable,
the pipeline produces the same output it does today.

Writes continue through the Docs API `batchUpdate` exclusively. Kix `/save`
(for authoring suggestions, voting chips, etc.) is a potential future
enhancement, not in scope here.

## Architecture

```
gdoc pull DOC
  1. OAuth: documents.get → from_docs_json.build_document() → AST
  2. Try: load_kix_session(doc_id)
     → success: extract_ot_ops() → enrich_from_kix(doc, model)
     → failure: debug log, continue with un-enriched AST
  3. Emit markdown / HTML as today
```

The enrichment is a post-processing decorator on the existing AST. It does
not re-derive paragraph text, styles, or structure — it trusts the Docs API
for those and only adds what the API cannot provide.

## New modules

### `src/google_doc_diff/kix/auth.py`

Cookie extraction and session establishment.

```python
@dataclass
class KixSession:
    jar: MozillaCookieJar
    token: str
    ouid: str
    doc_id: str
    role: str           # "viewer" | "commenter" | "editor" | "owner"
    edit_html: str      # raw /edit response, reused for model extraction

def kix_available() -> bool:
    """True if Chrome cookies for docs.google.com can be loaded."""

def load_kix_session(doc_id: str, **overrides) -> KixSession | None:
    """Load cookies, fetch /edit, scrape info_params and role.
    Returns None on any failure (no Chrome, bad cookies, no access)."""
```

Cookie source resolution (checked in priority order):

1. `--kix-cookies PATH` or `--kix-profile NAME` CLI flag
2. `GDOC_KIX_COOKIES` or `GDOC_KIX_PROFILE` env var
3. Auto-detect: most recently modified Chrome profile's Cookies file

Platform paths:
- macOS: `~/Library/Application Support/Google/Chrome/<profile>/Cookies`
- Linux: `~/.config/google-chrome/<profile>/Cookies`

`--kix-cookies` accepts a direct path to any Chromium-family Cookies SQLite
file (Chrome, Brave, Arc, Canary, etc.) or a copied/exported file.

### `src/google_doc_diff/kix/model.py`

OT op extraction from the already-fetched `/edit` HTML.

```python
@dataclass
class KixModel:
    ops: list[dict]       # raw OT ops as dicts
    revision: int         # seed revision from the bootstrap chunk
    model_version: int    # umv watermark from the last op

def extract_ot_ops(session: KixSession) -> KixModel | None:
    """Parse DOCS_modelChunk from session.edit_html.
    Returns None if the chunk is missing (redirect page, etc.)."""
```

No network call — reuses the HTML cached on the session object.

### `src/google_doc_diff/kix/enrich.py`

The decorator that patches kix-derived details onto an existing AST.

```python
def enrich_from_kix(doc: Document, model: KixModel) -> Document:
    """Mutate doc in place with details from the OT stream."""
```

Three independent sub-enrichments:

1. **Comment anchor resolution** — Builds a `kix_id -> block_index` mapping
   from OT ops (correlating `te` text-element placement ops with paragraph
   byte offsets). Feeds the existing `anchor_comments(doc, kix_resolver=...)`
   hook, replacing text-match heuristics when available.

2. **Voting chip details** — Scans for `ae` ops with `et:"emoji-voting"`
   paired with `nm`/`dtvc` sub-model wrappers. Patches emoji, voter list,
   and per-voter IDs onto existing `VotingChip` AST nodes, matched by
   byte-offset position.

3. **Suggestion colors** — Extracts the `suggestionColors` map
   (`suggest.<id> -> #RRGGBB`) from the bootstrap data and patches it onto
   existing suggestion objects in the AST.

Each sub-enrichment is independently skippable (no-op if the relevant data
is absent) and independently testable with fixture ops.

## CLI integration

New flags on `gdoc pull`:

| Flag | Effect |
|------|--------|
| `--kix-cookies PATH` | Use this Cookies SQLite file for kix auth |
| `--kix-profile NAME` | Use this Chrome profile name for kix auth |
| `--no-kix` | Skip kix enrichment entirely |

No flag = auto-detect cookies and enrich if possible.

`--verbose` reports enrichment outcome:
- `kix enrichment: applied (comments: 12 resolved, chips: 3 enriched)`
- `kix enrichment: skipped (no Chrome cookies found)`

## Fallback behavior

| Failure | Behavior |
|---------|----------|
| No Chrome / no Cookies file | `kix_available()` returns False, skip silently |
| Keychain access denied | `load_kix_session()` returns None, debug log |
| Cookies expired or insufficient | /edit returns login page, returns None, debug log |
| No access to doc via Chrome session | /edit returns 403, returns None, debug log |
| `--no-kix` flag | Skip entirely, don't attempt cookie loading |

All failures produce the same output as today (OAuth-only quality).

## Dependency

`browser-cookie3` (already used by the probe scripts) is added as an optional
dependency: `pip install gdoc-diff[kix]`. The `kix_available()` check handles
the import gracefully if the extra is not installed — returns False with no
warning. Users who want enrichment install the extra; users who don't never
see a message about it.

## Future: kix /save for suggestion authoring

The Docs API cannot author suggestions — only direct edits. Kix's `iss`
(insert-string-in-suggestion) op is the only programmatic way to propose a
change as a suggestion. This is out of scope for this design but the
`KixSession` already carries the `role` and auth material needed to POST to
`/save` when this is implemented.

## Testing

- **Unit tests** for each sub-enrichment with fixture OT ops (no network).
- **Unit tests** for cookie source resolution (mocked filesystem).
- **Integration test** (manual, not CI) against a live doc with known chips
  and comments, verifying enrichment matches expected output.

CI cannot run kix tests (no Chrome cookies), so all automated tests use
fixtures. A `tests/kix/` directory keeps them separate.
