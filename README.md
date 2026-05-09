# gdoc — google-doc-diff

Pull Google Docs into high-fidelity Pandoc-flavor Markdown (and parallel
HTML), with stable IDs, comments, and suggestions preserved as round-trip-
ready metadata. Designed for storing Docs in git, feeding them to AI tools,
and producing readable diffs across edits.

v1 is one-way (Doc → local). The on-disk format preserves enough metadata
that a future v2 can push edits back into the Doc.

See `docs/superpowers/specs/2026-05-09-google-doc-diff-design.md` for the
full design.

## Installation

```bash
make install-dev          # creates .venv, installs in editable mode + dev deps
source .venv/bin/activate
gdoc --version
```

## Setup (Google API access)

You need a Google Cloud project with the Docs and Drive APIs enabled, plus
an OAuth client of type "Desktop app". Two paths:

### Option A — Reuse `gog` credentials (recommended if you already have it)

If you use `gog` (Danny's Google CLI), you can import its existing
authorization without redoing the OAuth dance:

```bash
gog auth tokens export <your-email> --out /tmp/gogtoken.json
gdoc auth login --import-gog-token /tmp/gogtoken.json
\rm /tmp/gogtoken.json
```

Note: gog's default scopes (`drive`, `sheets`) are sufficient for revision
walking and exportLinks fetches but **not** for the structured Docs API
JSON path. If you see a 403 from `gdoc pull` complaining about missing
`documents.readonly` scope, re-authorize gog with that scope added, or use
Option B with a fresh credentials.json.

### Option B — Fresh OAuth flow

```bash
# After downloading credentials.json from console.cloud.google.com:
mkdir -p ~/.config/gdoc-diff
mv ~/Downloads/credentials.json ~/.config/gdoc-diff/credentials.json
gdoc auth login                # opens a browser; caches token.json
```

Required scopes:

| Scope | Used for |
|---|---|
| `https://www.googleapis.com/auth/documents.readonly` | Structured Docs JSON (current revision; full fidelity) |
| `https://www.googleapis.com/auth/drive.readonly` | Revision listing, exportLinks fetches, comments |

## Commands

```bash
gdoc pull <doc-id-or-url> [--out FILE.md] [--html-out FILE.html] [--extract-assets]
    Fetch the current revision and write Markdown (and optionally HTML).

gdoc revisions <doc-id-or-url> [--format table|json]
    List the doc's Drive revisions.

gdoc diff <doc-id-or-url> [PATH.md]
    Pull current state, show a unified diff against the local file.
    Exits 0 if identical, 1 if different, 2 on error.

gdoc auth login [--credentials-file PATH] [--import-gog-token PATH]
gdoc auth logout
gdoc auth status
```

The doc argument accepts either a bare doc ID or any
`https://docs.google.com/document/d/<id>/edit?...` URL.

## What v1 does and doesn't do

**Does:**
- Pull a current Doc to deterministic Markdown + HTML
- Preserve comments (with replies, resolved state) as Pandoc footnotes
- Preserve suggestions as CriticMarkup with metadata sidecars
- Handle multi-tab documents
- Synthesize stable CSS classes from inline-override styling
- Embed a `<style>` block in the markdown so styling round-trips

**Doesn't (yet):**
- Push edits back to the Doc (v2 — parsers are stubbed)
- Replay revision history into git commits (deferred until a heavily-edited
  test doc is available)
- Pull historical revisions (`--revision`) — needs `from_google_md` parser
- Render Drawings (no API access)
- Extract images on every pull (needs `--extract-assets`; URL rot otherwise)

## Development

```bash
make test                # pytest
make lint                # ruff
make check               # lint + tests
```

148+ tests covering AST construction, both serializers, cross-emitter ID
parity, and the Docs JSON → AST builder against handcrafted fixtures.

## License

AGPL-3.0-or-later. See `LICENSE`.
