# round-trip branch — status

Branch: `worktree-round-trip` (worktree at `.claude/worktrees/round-trip`)
Started overnight 2026-05-13; round-trip + 3-way merge wired in
2026-05-22 → 2026-05-23 follow-up session. Originally followed
[`docs/superpowers/plans/2026-05-14-gdoc-round-trip.md`](docs/superpowers/plans/2026-05-14-gdoc-round-trip.md);
later work extends past it.

## What landed

The seven chunks of the original implementation plan plus Chunks A/B
of the follow-up. All green:

```
$ uv run pytest -q
346 passed in 0.45s

$ uv run ruff check src/ tests/
All checks passed!
```

| Chunk | Module(s) | What it does |
|---|---|---|
| 1 | `ast/nodes.py` | `ParagraphProperties`, paragraph_id on Heading/Paragraph, fuller `StyleDescriptor`, typed `Voter`/`VotingChip`, `Document.gdoc_state` |
| 2 | `emit/markdown.py`, `styles/css.py` | Frontmatter `gdoc:` namespace, `paragraph_id` attrs, `--ot-*` custom-property CSS for ParagraphProperties |
| 3 | `parse/markdown.py` | Round-trip parser: frontmatter, headings, paragraphs, pandoc `::: {…}` divs, inline bold/italic/strike/link, lists, code; `parse(emit(ast)) == ast` proven for fixture set |
| 4 | `ops/{primitives,diff}.py` | `OpPlan` IR + two-phase diff (structural by stable id, content via `difflib`); descending-offset ordering across blocks and within-block |
| 5 | `apply/{policy,docs_api}.py` | Channel-selection dispatcher; translate primitives to `batchUpdate` Request dicts; runner that fetches + translates + applies; cursor-chained inserts, NORMAL_TEXT named-style reset, paragraph-style-before-text-style ordering, ListItem bullets |
| 6 | `cli_push.py`, `cli.py` | `gdoc push` with `--new --title`, `--force`, `--dry-run`, `--plan-only PATH` |
| 7 | `tests/round_trip/` | FakeDocsService + end-to-end property test |
| A | `ast/from_docs_json.py` | Pull-time `paragraph_id` synthesis (`p-{tab_idx}-{block_idx}`, skipping visually-empty paragraphs); aligned `build_block_index_from_docs_document` keys |
| B | `ast/nodes.Conflict`, `merge/three_way.py`, emit/parse for `.gd-conflict` git-style markers, `cli_push.push_merge`, `.pull-state.json` sidecar | Default `gdoc push` runs 3-way merge against the pull-time base; writes conflict markers into local md on overlapping edits |
| C | `cli_push.push_continue`, `_refresh_sidecar`, `has_conflict_blocks` | `gdoc push --continue` reparses the (now hopefully resolved) md and applies; every successful push refreshes the sidecar so the merge base advances |

## Quick smoke test

```bash
$ cat > /tmp/sample.md <<'EOF'
---
title: Sample
doc_id: ''
revision_id: ''
drive_url: ''
captured_at: '2026-05-14T00:00:00+00:00'
schema_version: 1
last_modifying_user: null
source_mode: pull
comments_preserved: true
suggestions_preserved: true
---

# A sample doc

This is a paragraph with **bold** and *italic* text.

## A sub-heading

- list item one
- list item two
EOF

$ uv run gdoc push /tmp/sample.md --dry-run
  InsertBlock    5

$ uv run gdoc push /tmp/sample.md --plan-only /tmp/plan.json
wrote /tmp/plan.json
  InsertBlock    5
```

The `--new` and `--force` flags do call live Google APIs; see the **OAuth
scope** caveat below before running them.

## OAuth scopes

`REQUIRED_SCOPES` now includes write scopes:

```python
# src/google_doc_diff/auth.py
REQUIRED_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]
```

Users with cached `gdoc auth login` tokens need to re-run it once for
Google to prompt for the new scopes. Imported gog tokens that already
carry full `auth/drive` keep working unchanged.

## Live smoke test (verified)

Source markdown — `# H1`, plain paragraph with `**bold**` and `*italic*`,
`## H2`, two bulleted list items — pushed and pulled back through Google
Docs round-trips cleanly: order preserved, headings correct, paragraph
not styled as heading, bullets rendered, bold/italic preserved. Only
differences vs source are Google-added (heading anchor ids, doc-level
revision/captured_at metadata).

## What's deliberately not done

These were named as overnight scope cuts in the implementation plan and
remain follow-ups:

- **`/save` channel backend.** Only Docs API is wired. `apply/kix_save.py`
  is a future addition; the policy dispatcher already has the routing
  slot for it.
- **Authoring comments / suggestions / chips.** Reading them stays as
  v1; writing them needs the `/save` channel or Drive Comments v3 with
  new request shapes.
- **`--abort` for conflict resolution.** `--continue` ships; `--abort`
  (discard markers and re-pull) is still a follow-up. Today the
  equivalent is just `gdoc pull <doc>` which overwrites the conflicted
  md.
- **Stable IDs for ListItems.** `_block_id` returns None for ListItem,
  so list reorderings/edits diff as anonymous inserts. Giving ListItem
  a `paragraph_id` (parallel to Paragraph/Heading) + stamping it in
  `_stamp_paragraph_ids` would close this.
- **Automated live e2e tests** against a real doc. Manual smoke tests
  cover the cycle today; CI needs a sandbox doc + service account.

Each of these is a candidate for a follow-up branch with its own design.

## File map (added in this branch)

```
src/google_doc_diff/
  apply/
    __init__.py
    docs_api.py            <- translate + apply; pure-function core
    policy.py              <- Channel enum + channel_for() dispatcher
  ops/
    __init__.py
    primitives.py          <- OpPlan + 6 frozen-dataclass primitives
    diff.py                <- AST -> OpPlan two-phase diff
  cli_push.py              <- push_new / push_force / push_merge / push_dry_run
  merge/
    __init__.py
    three_way.py           <- merge(base, local, remote) -> (merged_ast, conflicts)

  ast/nodes.py             <- extended (ParagraphProperties, paragraph_id,
                                 VotingChip, Voter, Document.gdoc_state,
                                 fuller StyleDescriptor, Conflict)
  ast/from_docs_json.py    <- _stamp_paragraph_ids on pull
  emit/markdown.py         <- _emit_frontmatter handles gdoc:, paragraph_id
                                 attrs, _emit_conflict for .gd-conflict divs
  parse/markdown.py        <- round-trip parser; .gd-conflict opaque parsing
  styles/css.py            <- paragraph_props_to_css for --ot-* output
  cli.py                   <- pull writes .pull-state.json sidecar;
                              push default = merge; push subcommand wiring

tests/
  unit/
    test_ast_extensions.py
    test_emit_round_trip.py
    test_parse_markdown.py
    test_ops_primitives.py
    test_ops_diff.py
    test_apply_policy.py
    test_apply_docs_api.py
    test_cli_push.py
    test_conflict_round_trip.py
    test_merge_three_way.py
    test_from_docs_json.py
  round_trip/
    test_emit_parse_round_trip.py
    test_full_pipeline_mock.py

docs/superpowers/
  specs/2026-05-14-gdoc-round-trip-design.md   (on main, before this work)
  plans/2026-05-14-gdoc-round-trip.md          (in this branch)
```

## Commits on this branch

```
$ git log --oneline main..HEAD
b5c67f6 push: --continue resolves conflicts; refresh sidecar after every apply
f3dd092 docs: STATUS.md reflects pull-time IDs, surgical force-push, 3-way merge
105bd01 cli: default `gdoc push` to 3-way merge; sidecar holds the base
47ac3d2 merge: three-way AST merge + Conflict AST + .gd-conflict markers
bc6385a push: make surgical edits via push --force actually persist
26b5b64 ast: synthesize paragraph_id on pull so push --force can diff cleanly
f1c655d docs: update STATUS.md — write scopes shipped, live round-trip verified
10c6041 apply: make create-from-scratch round-trip actually work
f0c762d auth: request write scopes so `gdoc push` can hit Docs and Drive
e0725a0 docs: STATUS.md + README round-trip preview
9635c8c tests + lint: end-to-end pipeline mock + ruff clean
25cad6a cli: gdoc push with --new / --force / --dry-run / --plan-only
d1dc573 apply: policy dispatcher + docs_api translate/apply
1cfa412 ops: OpPlan IR + AST diff producing structural & content primitives
60d4939 parse: implement minimum-viable round-trip parser
f2c8263 emit: round-trip carriers — frontmatter gdoc: ns, paragraph_id attrs, --ot-* CSS
821539e ast: round-trip extensions (ParagraphProperties, paragraph_id, VotingChip, gdoc_state)
694a145 plan: gdoc round-trip implementation chunks
```

(The kix_probes investigation + design spec are on `main`.)

## Picking up the thread

Three-way merge with `--continue` and live sidecar refresh now land.
Natural next chunks:

1. **`--abort`**: drop conflict markers and re-pull. Today `gdoc pull
   <doc>` is the manual equivalent.
2. **ListItem `paragraph_id`**: parallel to Paragraph/Heading;
   removes the "anonymous insert" fallback for list edits.
3. **Comments/suggestions/chips merge**: lifecycle ops live in their
   own diff routines per the spec — currently they round-trip read-
   only, not via the merger.

