# gdoc — google-doc-diff

Pull Google Docs into high-fidelity Pandoc-flavor Markdown (and parallel
HTML), with stable IDs, comments, suggestions, and inline widgets (votes,
reactions, dates, file/folder/sheet/slides chips, dropdowns, person
mentions) preserved as round-trip-ready metadata. Designed for storing
Docs in git, feeding them to AI tools, and producing readable diffs across
edits.

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

### Option A — Reuse `gog` credentials

If you use `gog` (Danny's Google CLI), import its existing authorization
without redoing the OAuth dance:

```bash
gog auth tokens export <your-email> --out /tmp/gogtoken.json
gdoc auth login --import-gog-token /tmp/gogtoken.json
\rm /tmp/gogtoken.json
```

gog's default `drive` + `sheets` scopes are sufficient. (`documents.readonly`
isn't strictly required — `drive` covers the Docs API call too.)

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
| `https://www.googleapis.com/auth/drive.readonly` | Revision listing, exportLinks fetches, Drive Comments API |

## Commands

```bash
gdoc pull <doc-id-or-url> [--out FILE.md] [--html-out FILE.html]
                          [--extract-assets] [--no-chip-counts]
    Fetch the current revision; write Markdown (and optionally HTML).

gdoc fetch [<doc-id-or-url>] [--out FILE.md]
    Refresh the working tree with a live pull. With no DOC argument,
    reads the doc id and out path from .gdoc-replay-state.json in cwd.
    Designed for the post-replay workflow (see below).

gdoc diff <doc-id-or-url> [PATH.md] [--color=auto|always|never]
    Pull current; show colored unified diff against the local file.
    Exits 0 if identical, 1 if different, 2 on error.

gdoc revisions <doc-id-or-url> [--since ISO] [--until ISO]
                               [--format table|json]
    List Drive revisions: id, modifiedTime, lastModifyingUser.

gdoc replay <doc-id-or-url> --since ISO [--until ISO]
                            [--out FILE.md] [--commit]
                            [--squash-by-author DURATION]
                            [--include-comments | --no-include-comments]
                            [--dry-run] [--resume | --restart]
    Walk revisions + Drive Comments API events into one chronological
    timeline; emit one .md per event (and one git commit per event with
    --commit, with the original author and timestamp). After the loop
    completes, the working tree is overwritten with the live rich state
    (uncommitted) — see workflow below.

gdoc auth login [--credentials-file PATH] [--import-gog-token PATH]
gdoc auth logout
gdoc auth status
```

A doc argument accepts a bare doc ID or any
`https://docs.google.com/document/d/<id>/edit?...` URL.

## Replay workflow: HEAD as history, working tree as the live doc

`gdoc replay --commit` produces a git history that mirrors the social
shape of the Doc — every prose change, comment, reply, resolve, reopen
becomes its own commit, in chronological order, with the original author
and timestamp. After all events are committed, the runner overwrites the
output file ONE more time with the rich live state from the Docs API
(suggestions intact, chips with full structured metadata) and leaves it
**uncommitted**.

```
HEAD..committed history  =  faithful historical replay (lossy text;
                            chips appear as Google's flat markdown
                            renderings since exportLinks is the only
                            historical content path)
working tree             =  the live doc as it is RIGHT NOW
git diff HEAD            =  what's changed since the last replayed event
```

Suggestions naturally fit this model: they're in-progress edits, so they
belong in the working tree (where they don't get back-attributed to past
events). When you later want to refresh the working tree without
re-walking the timeline:

```bash
gdoc fetch                # reads .gdoc-replay-state.json; refetches live
git diff HEAD             # see what's changed since the last replayed event
```

## Inline widget handling

The Docs API exposes some inline widgets as structured types and others
as opaque Private Use Area placeholders. `gdoc pull` (and `gdoc fetch`)
extract everything it can:

| Widget | API representation | Output |
|---|---|---|
| Person mention | `person` element | `[Alice]{.gd-chip data-kind="person" data-email="..."}` |
| Doc / Folder / Sheet / Slides / Form / Drawing chip | `richLink` element | `[Title]{.gd-chip data-kind="richlink-folder" data-uri="..." data-mime_type="..." data-rich_link_id="..."}` (kind suffix derived from MIME) |
| Date chip | `dateElement` | `[May 5, 2026]{.gd-chip data-kind="date" data-timestamp="2026-05-05T12:00:00Z" data-date_format="..."}` |
| Voting / reaction chip (`➕`, `❤️`, `👍`, `🚀`) | PUA `U+E907` (no chip-type or count info in JSON) | `[(➕ 1)]{.gd-chip data-kind="vote-thumbsup" data-count="1" data-emoji="➕"}` (recovered via markdown export cross-reference) |
| Dropdown chip | PUA `U+E907` (resolved value rendered in markdown export) | `[Standard White (#FFFFFF)]{.gd-chip data-kind="dropdown-color" data-rendered="..."}` |
| Other unrenderable widgets | PUA `U+E907` | Generic `[?]{.gd-chip data-kind="reaction"}` if cross-reference fails |
| Decorative chip icons | dangling `inlineObjectElement` | suppressed |

The chip-count cross-reference (`--chip-counts`, on by default) is one
extra Drive markdown-export call per pull. Disable with `--no-chip-counts`
if you don't care about counts and want to skip the call.

## Comments

Comments and replies render as Pandoc footnote definitions, with the
anchored prose wrapped in `[…]{.gd-cmt-anchor #c-…}[^c-…]`. Short
single-paragraph comments use Pandoc's inline `^[…]` form. The Drive
Comments API exposes the anchor as a text snippet (`quotedFileContent`),
so reanchoring against any historical revision (or against the current
revision after edits) is done by substring search; comments whose snippet
can't be found get marked `data-orphaned="true"`.

## What v1 does and doesn't do

**Does:**
- Pull a current Doc to deterministic Markdown + HTML
- Preserve comments (with replies, resolved state) as Pandoc footnotes
- Preserve suggestions (insert / delete / replace) as CriticMarkup with
  metadata sidecars
- Handle multi-tab documents (including nested tabs)
- Extract person, file/folder/sheet/slides/etc. rich-link chips, date
  chips, and recover voting / reaction / dropdown chip renderings via
  markdown-export cross-reference
- Synthesize stable CSS classes from inline-override styling
- Embed a `<style>` block in the markdown so styling round-trips
- Replay full edit history (revisions + comment events) into a chronological
  git history, with original authors and timestamps; leave the live state
  in the working tree uncommitted

**Doesn't (yet):**
- Push edits back to the Doc (v2 — parsers are stubbed)
- Render Drawings (no API access)
- Recover voting counts perfectly when Google's markdown export omits the
  chip rendering (some widgets in some contexts simply don't get rendered
  — they're dropped from the cross-reference)
- Extract images automatically on every pull (needs `--extract-assets`;
  Drive image URLs rotate within hours so the no-extract path is best
  effort)
- Fetch Drive revisions older than Drive's compaction window — the API
  truncates revision lists for frequently-edited Docs

## Development

```bash
make test                # pytest (197 tests)
make lint                # ruff
make check               # lint + tests
```

Tests cover the full AST tree, both serializers, cross-emitter ID parity,
the Docs JSON → AST builder against handcrafted fixtures, the
markdown-export cross-reference for chip recovery, the replay timeline
merger / state file / git wrapper, and the comment re-anchorer.

## License

AGPL-3.0-or-later. See `LICENSE`.
