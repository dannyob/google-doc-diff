# gdoc

`gdoc` pulls a Google Doc into Markdown (or HTML), keeping comments,
suggestions, voting chips, dates, file/folder/sheet chips, and person mentions
intact. It can also replay the doc's full edit history into a git repo, one
commit per prose change / comment / reply / resolve, with the original
authors and timestamps.

```sh
$ gdoc pull "https://docs.google.com/document/d/1ABC.../edit"
wrote q3-planning-notes.md
warning: 1 image URL(s) may rotate. For archival use, re-run with --extract-assets.

$ head -25 q3-planning-notes.md
---
captured_at: '2026-08-12T14:35:12+00:00'
comments_preserved: true
doc_id: 1ABCDEFGexampledocid
source_mode: pull
suggestions_preserved: true
title: Q3 Planning Notes
---

# Roadmap

The proposal is [unfinished]{.gd-cmt-anchor #c-XYZ1}[^c-XYZ1] in its
current form. We need to add {++the auth section++}[^s-S1] before
shipping. ([Design Doc Folder]{.gd-chip data-kind="richlink-folder"
data-uri="https://drive.google.com/drive/folders/0AC..."})

## Sept 4, 2026 [(➕ 3)]{.gd-chip data-kind="vote-thumbsup" data-count="3"
data-emoji="➕"}

[^c-XYZ1]: ::: {.gd-comment data-author="alice@example.com"
    data-created="2026-08-10T21:07:25Z" data-resolved="false"}
    **alice@** 2026-08-10: which auth do we mean — OAuth or the SSO bridge?

    > **bob@** 2026-08-11: OAuth; the SSO piece is in a separate doc
    :::
```

## Install

```sh
git clone <this-repo> && cd google-doc-diff
make install-dev
source .venv/bin/activate
```

You'll need a Google Cloud project with the Docs and Drive APIs enabled and
an OAuth client of type "Desktop app". Drop the downloaded credentials at
`~/.config/gdoc-diff/credentials.json` and run `gdoc auth login` (browser
opens once; refresh token is cached).

If you already use [`gog`](https://github.com/danny/gogcli) you can skip
the OAuth dance:

```sh
gog auth tokens export <your-email> --out /tmp/gogtoken.json
gdoc auth login --import-gog-token /tmp/gogtoken.json
\rm /tmp/gogtoken.json
```

## Replay: HEAD = history, working tree = the live doc

The unusual command is `gdoc replay`. It walks every revision and every
comment / reply / resolve / reopen event in chronological order and makes
one git commit per event, preserving the original author and timestamp.
After the loop, it overwrites the file one more time with the rich live
state from the Docs API but leaves it **uncommitted**. So:

```
HEAD                = the historical replay (committed)
working tree        = the live doc as it is right now
git diff HEAD       = what's changed since the last replayed event
gdoc fetch          = refresh the working tree without re-walking history
```

Sample run on a heavily-commented planning doc, scoped to the last 36 hours:

```sh
$ gdoc replay $DOC --out doc.md --since 2026-08-10T00:00:00Z --squash-by-author 5m
  prose_change   2026-08-10T00:05:39+00:00  961bfac...
  comment_create 2026-08-10T15:11:03+00:00  93ad269...
  reply_create   2026-08-10T15:11:14+00:00  4a5e9bc...
  ...
replayed 32 event(s); state: ./.gdoc-replay-state.json
head state (live doc, suggestions, full chip metadata) written uncommitted to
doc.md; `git diff HEAD` to see what's changed since the last replayed event.

$ git log --pretty="%h %an %s" | head -5
4a72132 carol      prose: revision 9245
1938f6d bob        reply: c-XYZ7 r-R12
9235fb9 alice      reply: c-XYZ7 r-R11
b7c20ca dave       prose: revision 9201
56836c5 bob        comment: c-XYZ6
```

Suggestions live only in the working tree — they're in-progress, not
historical, so they don't get back-attributed to past replay points.

## Other commands

```
gdoc pull <doc> [--out FILE.md] [--html-out FILE.html] [--extract-assets]
gdoc fetch [<doc>]                 refresh the working tree (reads
                                   .gdoc-replay-state.json if present)
gdoc diff <doc> [PATH.md]          unified diff vs local (exit 0/1/2)
gdoc revisions <doc>               list Drive revisions
gdoc auth login | logout | status
```

A `<doc>` argument is a bare doc ID or any
`https://docs.google.com/document/d/<id>/edit?...` URL.

### Large multi-tab docs

Google's `documents.get?includeTabsContent=true` returns HTTP 500 on large
multi-tab documents, and there is no per-tab variant of that call. When `pull`
hits that 500 it falls back to exporting each tab's markdown separately and
re-attaching comments from the Drive API. Force it with `--per-tab`, or
disable the fallback with `--no-per-tab`.

The fallback is lossy: suggestions and paragraph ids are gone, comments are
re-anchored by text matching, and no `.pull-state.json` is written, so `gdoc
push` cannot three-way merge these files. Nested tabs are also flattened —
the per-tab export path has no confirmed reverse-engineered op shape for
child tabs, so every tab comes back at the top level. It is also slow — the
export endpoint rate-limits, so tabs are fetched one per second.

## Inline widgets

Docs encodes some chips as structured types and others as opaque
`U+E907` placeholders with no metadata in the API. `gdoc` extracts what
it can:

| Widget | API form | Output |
|---|---|---|
| Person mention | `person` element | `[Alice]{.gd-chip data-kind="person" data-email="..."}` |
| File / folder / sheet / slides | `richLink` | `[Title]{.gd-chip data-kind="richlink-folder" data-uri="..."}` (kind from MIME) |
| Date | `dateElement` | `[May 5, 2026]{.gd-chip data-kind="date" data-timestamp="2026-05-05T12:00:00Z"}` |
| Vote / reaction (`➕`, `❤️`, `👍`, `🚀`) | `U+E907` placeholder | `[(➕ 3)]{.gd-chip data-kind="vote-thumbsup" data-count="3"}` (recovered via markdown export) |
| Dropdown | `U+E907` placeholder | `[Standard White (#FFFFFF)]{.gd-chip data-kind="dropdown-color"}` |
| Other / unrenderable | `U+E907` placeholder | `[?]{.gd-chip data-kind="reaction"}` if cross-reference fails |

The vote / reaction recovery costs one extra Drive markdown-export call
per pull. Disable it with `--no-chip-counts` if you don't need counts.

## Round-trip preview (this branch)

This branch (`worktree-round-trip`) adds `gdoc push` — write a markdown
file back to a Google Doc. Prose, headings, lists, and inline formatting
go end-to-end; three-way merge against a moving remote is a follow-up.
See [`STATUS.md`](STATUS.md) for what landed, what's deferred, and the
OAuth-scope caveat before running against a live doc.

```
$ gdoc push doc.md --new --title "Quick draft"     # create new doc
$ gdoc push doc.md DOC --force                     # overwrite remote
$ gdoc push doc.md DOC --dry-run                   # show planned ops
$ gdoc push doc.md DOC --plan-only plan.json       # JSON dump
```

Design spec is at
[`docs/superpowers/specs/2026-05-14-gdoc-round-trip-design.md`](docs/superpowers/specs/2026-05-14-gdoc-round-trip-design.md).

## What it doesn't do (v1)

- **Push edits back to the Doc.** v1 is one-way; this branch lifts that.
- **Render Drawings.** No Google API for them.
- **Recover every vote count.** Google's markdown export sometimes omits
  chip renderings (mid-table dropdowns, in particular). Affected chips
  fall back to `[?]{.gd-chip data-kind="reaction"}` rather than guess
  wrong.
- **Reach revisions older than Drive's compaction window.** Drive truncates
  the revision list for frequently-edited Docs. You get whatever Drive
  decides to keep, which is usually the named/auto-saved milestones.
- **Use stable image URLs.** Drive image URLs rotate within hours. Pass
  `--extract-assets` to download images locally and rewrite links.

## Development

```sh
make test     # 209 tests
make lint     # ruff
make check    # both
```

Design spec: [`docs/superpowers/specs/2026-05-09-google-doc-diff-design.md`](docs/superpowers/specs/2026-05-09-google-doc-diff-design.md).

Built one weekend with Claude Code on the user side. Tested live against a
long meeting-notes doc with 80+ comment events; the chip / comment / replay
parts were rough until they ran on real Doc-shaped chaos.

## License

AGPL-3.0-or-later.
