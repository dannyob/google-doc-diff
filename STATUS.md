# round-trip branch — status

Branch: `worktree-round-trip` (worktree at `.claude/worktrees/round-trip`)
Started overnight 2026-05-13; round-trip + 3-way merge + comment anchoring
across 2026-05-22 → 2026-05-23 sessions. Originally followed
[`docs/superpowers/plans/2026-05-14-gdoc-round-trip.md`](docs/superpowers/plans/2026-05-14-gdoc-round-trip.md);
later work extends past it.

## What works (live-verified)

| Flow | Ops generated |
|---|---|
| `gdoc push file.md --new --title "..."` | Creates doc from markdown. H1–H3, paragraphs, bold/italic, links, bullets all render. |
| `gdoc pull DOC` → edit → `gdoc push DOC` | 3-way merge. 1 InsertText for a 1-word change on a 1400-block doc. |
| `gdoc push DOC --force` | Overwrite remote. Surgical diffs via paragraph_id. |
| Conflict: both sides edit same block | Writes `<<<<<<< LOCAL` / `=======` / `>>>>>>> REMOTE` markers. |
| `gdoc push --continue` | Applies resolved md, refreshes sidecar. |
| `gdoc push --abort` | Discards markers, restores from remote. |
| Complex docs (925 comments, 63 images, 1400+ blocks) | Normalization prevents phantom diffs. |

425 tests, ruff clean.

## What landed (by chunk)

| Chunk | Module(s) | What it does |
|---|---|---|
| 1 | `ast/nodes.py` | `ParagraphProperties`, paragraph_id on Heading/Paragraph/ListItem, `StyleDescriptor`, `Voter`/`VotingChip`, `Document.gdoc_state`, `Conflict` |
| 2 | `emit/markdown.py`, `styles/css.py` | Frontmatter `gdoc:` namespace, paragraph_id attrs, `--ot-*` CSS, `.gd-conflict` git-style markers, link class-span fix |
| 3 | `parse/markdown.py` | Round-trip parser: frontmatter, headings, paragraphs, pandoc `::: {…}` divs, inline formatting, lists with `[]{#id}` prefix, code, `.gd-conflict` opaque parsing |
| 4 | `ops/{primitives,diff}.py` | `OpPlan` IR + two-phase diff; descending-offset ordering across and within blocks |
| 5 | `apply/{policy,docs_api}.py` | Channel dispatcher; translate to `batchUpdate`; cursor-chained inserts, NORMAL_TEXT reset, paragraph-before-text-style ordering, ListItem bullets; `includeTabsContent` on fetch |
| 6 | `cli_push.py`, `cli.py` | `push --new`, `--force`, `--continue`, `--abort`, `--dry-run`, `--plan-only`; `.pull-state.json` sidecar with `base_md`; sidecar refresh after every apply |
| 7 | `merge/three_way.py` | Block-level 3-way merge with full conflict matrix; ordered pairing for comment anchors |
| 8 | `ast/anchor_comments.py` | Ordered pairing for ambiguous snippets + `kix_resolver` hook for exact positioning via Chrome cookies |
| 9 | `kix/{auth,model,enrich}.py` | Optional kix enrichment layer: Chrome cookie auth; OT model extraction (multi-chunk, ksm-unwrap, per-tab); voting-chip enrichment (emoji + voter ids); suggestion colors. Live-verified on a 4-tab doc: 5/5 voting chips enriched. |

## Known issues / follow-ups

### Parser can't round-trip pandoc `[text]{.class}` spans

markdown-it doesn't understand pandoc attribute spans. Comment anchors
(`[text]{.gd-cmt-anchor #c-XXX}`) and styled spans (`[text]{.gd-style-XXX}`)
in the emitted markdown get parsed back as literal text. The merge
layer works around this via emit→parse normalization (both sides go
through the same lossy pipeline, so they agree on what "unchanged"
looks like), but the emitted markdown for comments and styled inline
spans is lossy.

**Fix**: either teach the parser to handle pandoc `[text]{.class}` via a
pre-processing pass, or switch the emit format for these to something
markdown-it CAN parse (e.g., HTML `<span>` elements).

### InsertText doesn't carry run_style from the target AST

Text-level diffs (`InsertText`) don't carry the target paragraph's run
formatting. If you insert text that should be bold, the inserted text
inherits whatever formatting is at the insert point. Strikethrough and
other formatting on newly inserted text is lost.

**Fix**: when `_emit_text_ops` produces an `InsertText`, look up the
target AST's runs at that offset and attach `run_style`.

### Kix comment-anchor precision needs OT index reconstruction

The OT stream carries exact comment anchor ranges (`as` ops with
`st == "doco_anchor"`, plus `si`/`ei`), and we extract them correctly. But
mapping `si` to an AST block needs a faithful reconstruction of Kix's index
space, which counts table-cell / nested-list / footnote / suggestion content
that the public AST sizes differently (≈90k vs ≈64k chars on the Filecoin
weekly doc). So `si`→block drifts and only a handful land in the right block.

Kix enrichment therefore ships voting chips + suggestion colors only; comment
anchoring stays on the text-matching path (`anchor_comments` without a
resolver). Closing the gap means replaying the `is`/`ae`/`te`/table op stream
to build an exact index→position map — effectively a small Kix layout engine.
The `kix_resolver` hook in `anchor_comments` is the wiring point once that map
exists.

### `/save` channel backend (future: suggestion authoring)

Only Docs API is wired in `apply/policy.py`. Authoring suggestions
as suggestions (rather than direct edits) needs the Kix `/save`
endpoint's `iss` op. The routing slot exists; `apply/kix_save.py` is
a future addition. The `KixSession` already carries the auth material
needed for `/save` POSTs.

### Multi-tab merge

Works for single-tab docs. Multi-tab would need per-tab merge passes
and tab-level structural diff (added/removed/reordered tabs).

### Styling comparison

Comparing our emitted HTML rendering against Google's native HTML
export hasn't been started. The exported files are at `~/tmp/complex-doc.*`.

### Code structure debt (from code review)

- `_block_id` duplicated in `ops/diff.py` and `merge/three_way.py` —
  extract to shared helper.
- Conflict marker constants duplicated in `emit/markdown.py` and
  `parse/markdown.py`.
- `_blocks`/`_flatten` functionally identical across diff and merge.

## File map

```
src/google_doc_diff/
  apply/
    __init__.py
    docs_api.py            <- translate + apply; cursor-chained inserts
    policy.py              <- Channel enum + channel_for() dispatcher
  ops/
    __init__.py
    primitives.py          <- OpPlan + 6 frozen-dataclass primitives
    diff.py                <- AST -> OpPlan two-phase diff
  merge/
    __init__.py
    three_way.py           <- merge(base, local, remote) -> (merged, conflicts)
  cli_push.py              <- push_new / push_force / push_merge /
                              push_continue / push_abort / push_dry_run
  ast/
    nodes.py               <- Conflict, ParagraphProperties, paragraph_id,
                              VotingChip, Voter, Comment.anchor
    from_docs_json.py      <- _stamp_paragraph_ids; kix_resolver passthrough
    anchor_comments.py     <- ordered pairing + kix_resolver hook
  emit/markdown.py         <- .gd-conflict markers, link class-span fix,
                              []{#id} on ListItems
  parse/markdown.py        <- .gd-conflict opaque parsing, []{#id} on ListItems
  styles/css.py            <- paragraph_props_to_css for --ot-* output
  kix/
    __init__.py              <- public API re-exports
    auth.py                  <- KixSession, cookie resolution, /edit fetch
    model.py                 <- KixModel.ops_by_tab; unwrap all DOCS_modelChunk
                                ksm wrappers into per-tab inner op streams
    enrich.py                <- enrich_from_kix decorator; voting chips
                                (emoji + voter ids), suggestion colors
  cli.py                   <- pull writes .pull-state.json sidecar;
                              push default = merge with --continue/--abort;
                              --kix-cookies/--kix-profile/--no-kix on pull

tests/
  unit/
    test_ast_extensions.py
    test_emit_round_trip.py    <- pandoc-leak guard tests
    test_parse_markdown.py
    test_ops_primitives.py
    test_ops_diff.py           <- descending-offset + cross-block ordering
    test_apply_policy.py
    test_apply_docs_api.py     <- ListItem block_index, chained inserts
    test_cli_push.py           <- push_merge, push_continue, push_abort
    test_conflict_round_trip.py <- Conflict + ListItem id round-trip
    test_merge_three_way.py    <- 44 edge-case merge tests
    test_from_docs_json.py     <- paragraph_id stamping
  kix/
    test_model.py              <- multi-chunk/ksm-unwrap, per-tab grouping
    test_auth.py               <- cookie source resolution (mocked fs)
    test_enrich.py             <- suggestion colors, voting chips
    test_cli_kix.py            <- flag parsing, skip diagnostics, integration
  round_trip/
    test_emit_parse_round_trip.py
    test_full_pipeline_mock.py
```
