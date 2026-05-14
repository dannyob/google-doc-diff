# gdoc round-trip — Design Spec

**Status:** Draft, awaiting reviewer pass
**Date:** 2026-05-14
**Author:** Brainstormed with Danny O'Brien
**Branch target:** new branch of `google-doc-diff` (parallel to v1, not yet named)
**Builds on:** [`2026-05-09-google-doc-diff-design.md`](2026-05-09-google-doc-diff-design.md) and [`docs/kix-investigation.md`](../../kix-investigation.md)

## Summary

Add symmetric, three-way round-tripping between a Google Doc and a single
Markdown file. v1 is one-way (Doc → local); v2 keeps that direction working
unchanged and adds `gdoc push`, which lets a user edit the `.md` and push the
result back into the live doc — including to docs that have moved on since
the markdown was last pulled.

The on-disk markdown stays self-contained: a single `.md` file carries every
property needed to reconstruct the rich-AST byte-identically and to push that
state to a fresh or existing Google Doc.

Three goals, in priority order:

1. **Pixel-perfect rendering** to Markdown *and* HTML.
2. **Readable, diffable Markdown** that still carries every property needed
   for round-trip.
3. **Round-trip edits**: edit the `.md`, push it back; either incremental
   edits of an existing doc or create-from-scratch.

## Decisions taken during brainstorm

| Question | Decision |
|---|---|
| Source of truth | **Symmetric** — three-way merge between base, local `.md`, and live doc. |
| OT-level properties markdown can't naturally express | **CSS-class indirection** via the inline `<style>` block (extends v1). |
| Merge base storage | **Git HEAD** of the doc's tracking branch; conflicts as `.gd-conflict` pandoc divs. |
| Scope of editable changes | **Anything the AST can represent** — prose, structure, comments, suggestions, chips, styles. |
| Write channel | **Hybrid**: Docs API `batchUpdate` first, fall back to `/save` per op when the API can't express it. |
| Create-from-scratch | **Same pipeline**: create an empty doc, then run normal push (base = ∅). |
| State carrier in `.md` | **YAML frontmatter under a nested `gdoc:` key**, plus inline pandoc attributes and the inline `<style>` block (no separate fenced state block). |

## Pipeline & components

```
                                  PULL                                    PUSH
        live doc                      ┃                          ┃        live doc
           │                          ┃                          ┃          ▲
           │  api/save_fetch          ┃                          ┃  apply   │
           ▼                          ┃                          ┃          │
       rich-AST  ◀──────────────────  ┃                          ┃  OpPlanner (channel selection)
           │                          ┃   ┌────────┐             ┃          ▲
           │  emit/markdown.py        ┃   │ git    │ HEAD        ┃          │  OpPlan IR
           ▼                          ┃   │ history│ (base)      ┃          │
       doc.md  ──── user edits ───────┃───┴────────┘─── parse ──▶ rich-AST  diff/merge
                                                                  ▲   ▲   ▲
                                                                  │   │   │
                                                              base  local  remote
                                                              AST   AST    AST
```

Five new modules over v1:

1. **`parse/markdown.py`** — markdown → rich-AST. Inverse of `emit/markdown.py`
   (currently stubbed in v1).
2. **`merge/`** — three-way AST merge: takes base + local + remote ASTs;
   returns a merged AST and a (possibly empty) list of conflicts. Conflicts
   become `.gd-conflict` pandoc divs in the emitted markdown.
3. **`ops/`** — the OpPlan IR + diff. `diff(old_ast, new_ast) → OpPlan`. The
   OpPlan is a list of channel-agnostic mutation primitives (`InsertText`,
   `DeleteRange`, `ApplyStyle`, `MoveBlock`, `AddElement`,
   `SetSuggestionState`, `MergeComment`, …).
4. **`apply/`** — OpPlan executor. Three backends behind a single dispatcher:
   `apply/docs_api.py` (Docs `batchUpdate`), `apply/drive_api.py` (Drive
   Comments v3), `apply/kix_save.py` (the internal `/save` POST proven out
   in `kix_probes/`).
5. **`cli/push.py`** — `gdoc push [DOC|--new] [PATH.md]`. Orchestrates fetch
   → merge → plan → apply, plus `--continue`, `--abort`, `--dry-run`,
   `--plan-only`.

The AST stays the same dataclass tree from v1, extended in §AST. v1 code
paths (`pull`, `fetch`, `diff`, `revisions`, `replay`) are untouched.

## AST extensions

The v1 AST already has the right *shape* (block tree + run tree +
cross-cutting collections + stable IDs). The gap is *coverage*:
`StyleDescriptor` carries a dozen typography fields; the OT model has a few
hundred.

1. **`ParagraphProperties` and `SectionProperties` dataclasses.** Frozen,
   hashable, typed. Mirror the OT `ps_*` and `sect_*` namespaces:
   line spacing, space-before / space-after, indent left / right / first-line,
   alignment, heading depth, keep-with-next, keep-lines-together,
   page-break-before, direction, bullet definitions. Same
   `None = inherit` convention as `StyleDescriptor`.

2. **`StyleDescriptor` extended to full OT `ts_*` coverage.** Add: vertical
   alignment, small-caps, baseline-shift, language tag, numeric weight
   (separate from bold), underline color, and the color-type discriminator
   (`hclr_color` vs theme reference). Anything we *can* round-trip becomes a
   typed field.

3. **Reify OT-only nodes as typed AST nodes:**
   - `VotingChip(chip_id, emoji, voters: list[Voter], current_user_voted: bool, signature: str)`
   - `Dropdown(chip_id, options: list[str], selected: str)`
   - `NamedSubModel(namespace: str, target_id: str, payload)` — typed
     fallback for unknown `nm` ops, replacing `Unsupported` for those.

Anything still not understood falls through to v1's existing
`Unsupported(kind, raw)`. Principle: any property a user *might* edit gets a
typed field; anything else is preserved opaquely so push doesn't lose it.

CSS-class indirection (next section) builds on these typed fields:
`styles/classes.py` extends to bucket `ParagraphProperties` and emit per-block
CSS into the inline `<style>` block.

## Markdown serialization (single-file state encoding)

The `.md` carries enough state to reconstruct the rich-AST byte-identically.
Three carriers, in decreasing priority order:

1. **Inline pandoc attributes for first-class state.**
   - Stable IDs on every block that has one (`{#h-…}`, `{#tbl-…}`,
     `{#li-…}`, `{#p-…}`).
   - Style refs as class names (`{.gd-p-d2af}`, `{.gd-r-7f31}`), with
     definitions in the inline `<style>` block.
   - Chip / suggestion / comment payload via `data-*` attributes — same
     shape as v1's chip rendering.

2. **The inline `<style>` block as the home for every OT property bag.**
   ```css
   .gd-p-d2af {
     --ot-line-height: 1.15;
     --ot-space-before: 12pt; --ot-space-after: 12pt;
     --ot-keep-with-next: true;
     --ot-heading-depth: 1;
   }
   ```
   The `--ot-*` custom properties carry OT names directly (not CSS
   approximations), so parse → emit round-trips without lossy mapping. The
   browser ignores them; we own the namespace.

3. **YAML frontmatter under a nested `gdoc:` key.**
   ```yaml
   ---
   title: Q3 Planning Notes
   doc_id: 1ABCDEFGexampledocid
   captured_at: '2026-08-12T14:35:12+00:00'
   comments_preserved: true
   suggestions_preserved: true
   gdoc:
     base_revision: 71
     model_version: 142
     signatures:
       kix.escg9h9fzc85: AastPo9fpBGWDoGREyxqSHrnjtJHj0Goa7iuNRwmDU6dZX+uJg==
     unsupported:
       - kind: drawing
         anchor: kix.drw3xyz
         raw: |
           { ... }
   ---
   ```
   YAML block scalars (`|`) handle long base64 signatures and raw JSON.
   Deterministic emission (sorted keys, stable line widths) so the
   frontmatter diffs only when state actually changes.

**Parse precedence:** inline pandoc attributes > `<style>` class refs >
frontmatter `gdoc:` defaults.

**Bloat caveat (acknowledged, not designed-around):** for a doc with many
chip signatures or large `Unsupported` blobs, frontmatter grows. If it
becomes painful in practice (>200 lines on real docs), we add an overflow
rule — not designing for it up front.

## Diff + three-way merge

The diff layer answers: *did anything change between two ASTs?* and *what's
the minimal OpPlan to express that change?* The merge layer combines two
such diffs (`base→local`, `base→remote`) and emits either a merged AST + an
OpPlan to apply, or a conflicted AST when both sides touched the same
region.

**Block identity by stable ID.** Every block in the v1 AST already carries
(or should carry) a stable id; add `Paragraph.paragraph_id` (synthesized at
pull time from a hash of position + revision, persisted in markdown as
`{#p-…}`). The diff key for a block is its id; for a run, `(parent_block_id,
run_index)`. That distinguishes "moved" from "deleted-and-readded" —
essential for clean OpPlans on big restructures.

**Diff is two-phase.**

1. *Structural diff (block tree).* Walk both ASTs in parallel keyed by id.
   Emit `BlockInserted`, `BlockDeleted`, `BlockMoved(old → new)`,
   `BlockReplaced` (id stable, content totally different), `BlockModified`.
2. *Content diff (within a `BlockModified`).* For each modified block, run a
   token-level diff over its runs (text + style), emitting `InsertText`,
   `DeleteRange`, `ApplyStyle`, `ReplaceRun`. Existing libraries (`difflib`
   or `diff-match-patch`) handle the textual part; we wrap them to be
   style-aware.

**Three-way merge runs the same diff twice, then reconciles.**

```
diff(base, local)  → ops_local
diff(base, remote) → ops_remote
```

For each `(block_id, region)` pair:

| local change | remote change | result |
|---|---|---|
| none | none | (skip) |
| change | none | apply local |
| none | change | apply remote |
| same change | same change | apply once |
| different changes, same region | different changes, same region | **conflict** |
| different changes, non-overlapping regions in same block | … | apply both (text-merge within block) |

Conflicts surface as a `Conflict` AST node wrapping `local_blocks` and
`remote_blocks` lists, emitted to markdown as a `.gd-conflict` div. The user
resolves by editing; `gdoc push --continue` reparses, sees no `Conflict`
nodes left, applies the resolved OpPlan.

**Three load-bearing details:**

- *Comments and suggestions are diffed separately.* Their lifecycle (create
  / edit / reply / resolve / accept / reject) doesn't map cleanly to block
  diff; they get their own diff routines emitting `MergeComment`,
  `SetSuggestionState`, etc.
- *Styles are diffed via the class registry, not inline.* If `.gd-p-d2af`
  exists on both sides with the same definition, no style op. If the
  definition changed, emit one `ApplyStyle` to every block referencing it.
- *The OpPlan is ordered.* Deletes before inserts before styles before chip
  mutations, so character indices stay stable through the apply phase. The
  OpPlanner re-bases each op's indices against the running model.

## OpPlan IR + channel selection

The OpPlan is a small, ordered list of channel-agnostic mutation primitives.
Each primitive is a plain dataclass — no I/O, no logic. The OpPlanner stage
walks the plan and emits one or both write channels.

**The primitives.** A starter set at the right grain to map 1:1 with either
a Docs `Request` or an OT command:

| Primitive | Carries | Maps to (Docs API) | Maps to (`/save`) |
|---|---|---|---|
| `InsertText(block_id, offset, text, run_style?)` | text + inline style | `insertText` | `is` |
| `DeleteRange(block_id, start, end)` | range | `deleteContentRange` | `ds` |
| `ApplyStyle(scope, range, style)` | scope text/paragraph/heading | `updateTextStyle` / `updateParagraphStyle` | `as` |
| `InsertBlock(after_id, block)` | full block AST | `insertText` + style ops | `is` + `as` + `ae` |
| `DeleteBlock(block_id)` | — | `deleteContentRange` | `ds` |
| `MoveBlock(block_id, after_id)` | — | (decompose) | (decompose) |
| `AddElement(kind, anchor, payload)` | chips, lists, drawings | varies | `ae` + `nm` + `te` |
| `SetSuggestionState(suggestion_id, action)` | accept/reject | `acceptOrRejectSuggestion` | `as st="suggestion"` |
| `MergeComment(comment_id, content, reply?, action?)` | comment lifecycle | Drive Comments v3 | Drive Comments v3 |
| `SetTabStructure(parent_id, ordered_children)` | tab moves/renames | (Docs API tabs) | `mkch` + `ac` |

`MoveBlock` and `InsertBlock` are higher-level primitives the planner
decomposes into channel-specific calls. The decomposition lives in `apply/`,
not in `ops/`.

**Channel selection — one function, one table.** `apply/policy.py` is a
single dispatcher:

```python
def channel_for(op: Op) -> Channel:
    if isinstance(op, MergeComment):                 return DRIVE_API
    if isinstance(op, SetSuggestionState):           return DOCS_API
    if isinstance(op, AddElement) and op.kind == "voting-chip-populate":
                                                     return KIX_SAVE
    if isinstance(op, AddElement) and op.kind not in DOCS_API_CHIPS:
                                                     return KIX_SAVE
    return DOCS_API     # default
```

**Apply is one transaction per channel.** The planner groups ops by chosen
channel and emits one `batchUpdate` call per group (Docs API supports this
natively — it transforms ops against each other server-side). For `/save`,
one bundle per group; `needsTransformOnClient: true` triggers a retry:
refetch live doc at the new server revision, re-derive the OpPlan against
the new base, retry.

## CLI

One new command, `gdoc push`, with sub-modes. Existing v1 commands untouched.

### Happy path

```
$ gdoc push doc.md DOC
fetching live doc at revision 67 …
3-way merge: base=HEAD (rev 64), local=doc.md (rev 64-derived), remote=rev 67
  local:  6 ops
  remote: 2 ops
  merged: 8 ops, 0 conflicts
applying:
  via Docs API (batchUpdate): 6 ops
  via /save:                  1 op  (voting-chip)
  via Drive Comments v3:      1 op  (reply)
ok — doc at revision 68
wrote doc.md (refreshed signatures, base_revision: 68)
```

### Conflict path

```
$ gdoc push doc.md DOC
fetching live doc at revision 71 …
3-way merge: base=HEAD (rev 64), local=doc.md, remote=rev 71
  3 conflicts; wrote conflict markers to doc.md
  resolve, then run: gdoc push --continue
```

Conflict markers in `doc.md`:

```
::: {.gd-conflict id="c-1"}
::: {.gd-ours}
Local version of the paragraph.
:::
::: {.gd-theirs author="alice@example.com" rev=70}
Remote version of the paragraph.
:::
:::
```

```
$ gdoc push --continue
re-reading doc.md … 0 conflicts remaining
applying merged OpPlan …
ok — doc at revision 72
```

### `--new` for create-from-scratch

```
$ gdoc push doc.md --new --title "Q3 Planning Notes"
creating empty doc …
new doc id: 1XyZ…
3-way merge: base=∅, local=doc.md, remote=empty → 142 ops, 0 conflicts
applying via Docs API (batchUpdate): 142 ops
ok — doc at revision 1
wrote doc.md (doc_id, drive_url, base_revision: 1)
```

### `--dry-run` and `--plan-only`

`--dry-run` prints the OpPlan and exits 0 without writing.
`--plan-only PATH` dumps the OpPlan as JSON to PATH (channel-tagged) for
inspection or for someone else to apply later.

### Three small details

- **No automatic git commit.** `push` writes the refreshed `doc.md` to the
  working tree but doesn't commit. Matches v1's `pull` and `fetch`.
- **Refresh on success.** After a successful apply, the live doc is refetched
  and `doc.md` is rewritten so the working tree reflects what's actually
  live. `base_revision` advances; signatures get refreshed (Docs may
  re-stamp some chips); class names re-emit deterministically (same hash
  inputs → same class names, so no diff churn).
- **Lock file.** `doc.md.gdoc-push-state` holds the OpPlan between conflict
  emission and `--continue`. Removed on successful apply or `--abort`.

## Testing strategy

Round-trip identity and merge correctness are the load-bearing tests. Live
API calls are confined to one opt-in fixture set.

### Three layers

**1. Unit tests on each module (fast, no network).**

| Module | Test |
|---|---|
| `parse/markdown.py` | `parse(emit(ast)) == ast` for each AST fixture |
| `ops/diff.py` | `diff(base, local) == expected_op_plan` for hand-written fixtures |
| `ops/merge.py` | three-way merge table: every cell of the local×remote conflict matrix has at least one fixture |
| `apply/policy.py` | every primitive has a `channel_for` assertion |
| `apply/docs_api.py` | mocked `batchUpdate`: assert request shape per primitive |
| `apply/kix_save.py` | mocked `/save`: assert bundle shape per primitive |

**2. Round-trip property tests on real captured fixtures.** Snapshot the
rich-AST of every test doc as JSON in `tests/fixtures/`. For each:

```
ast → emit → md → parse → ast'        ⇒ ast == ast'
ast → emit → md → parse → emit → md'  ⇒ md  == md'    (byte-identical)
ast → diff(empty, ast) → OpPlan → apply-to-empty-mock → ast''  ⇒ ast == ast''
```

The third property is the heart of round-trip: a freshly-built doc from the
OpPlan must equal the source AST. Runs against every fixture on every CI run.

**3. End-to-end against live docs (opt-in, slow).** `make test-live` (or
`pytest -m live`) runs a small suite against a dedicated test doc the user
owns. Each test:

1. `pull` the doc → markdown.
2. Edit the markdown in some specific way.
3. `push`.
4. Re-`pull` → assert the live doc matches the edit intent.
5. Restore via a saved revision id (Drive `revisions.update`), so the doc is
   left clean for the next run.

Kept out of the default `make test` for the same reason v1's replay tests
are local-fixture-only: real auth, real `/save`, real `batchUpdate`, and
Google-side flakiness.

### Fixture coverage targets

At least one fixture for: each block type, each chip kind we support,
suggestions in every mode (insertion / deletion / replacement / accepted /
rejected), a multi-tab doc, a doc with a `Conflict` node, a doc with
`Unsupported` nodes, a doc where one paragraph references a `.gd-p-*` class
also used elsewhere (so the "edit-the-class-definition affects N blocks"
path is exercised).

### One invariant smoke gate

`gdoc push --dry-run doc.md DOC` should be a no-op when `doc.md` is the
verbatim `pull` result. If diff emits any ops in that case, there's a
round-trip bug. Run this on every fixture as a smoke gate before the heavier
property tests.

## Out of scope

- **Real-time subscription via `/bind`.** Receiving concurrent edits without
  polling needs a `goog.net.BrowserChannel` client. Deferred.
- **Drawings authoring.** No Docs API access; OT shape unknown.
- **Pre-compaction revision recovery.** Drive's revisions truncation
  isn't fixable here.
- **Concurrent-edit avoidance.** We rely on Docs' server-side OT to
  reconcile concurrent saves; no client-side advisory lock.

## Open questions for review

1. Is the granularity of OpPlan primitives right? `MoveBlock` is currently a
   composite that decomposes in `apply/`; should it instead be eagerly
   decomposed in `ops/diff.py` so the planner sees only atoms?
2. Should `--new` accept an `--in-folder DRIVE_FOLDER_ID` so the created
   doc lands somewhere specific, or does that belong on a separate Drive
   subcommand?
3. The `gdoc:` frontmatter shape: prefer flat dotted keys
   (`gdoc.base_revision: 71`) or nested objects? Spec currently uses nested.
4. What's the right behavior when push refresh would overwrite local
   unsaved changes? Spec assumes "refresh always wins after push"; should
   it instead bail and require `--force-refresh`?
