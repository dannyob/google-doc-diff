# round-trip branch — status

Branch: `worktree-round-trip` (worktree at `.claude/worktrees/round-trip`)
Built overnight 2026-05-13 → 2026-05-14, autonomously, following
[`docs/superpowers/plans/2026-05-14-gdoc-round-trip.md`](docs/superpowers/plans/2026-05-14-gdoc-round-trip.md).

## What landed

All seven chunks of the implementation plan, ~1700 lines added, all green:

```
$ uv run pytest -q
318 passed in 0.41s

$ uv run ruff check src/ tests/
All checks passed!
```

| Chunk | Module(s) | What it does |
|---|---|---|
| 1 | `ast/nodes.py` | `ParagraphProperties`, paragraph_id on Heading/Paragraph, fuller `StyleDescriptor`, typed `Voter`/`VotingChip`, `Document.gdoc_state` |
| 2 | `emit/markdown.py`, `styles/css.py` | Frontmatter `gdoc:` namespace, `paragraph_id` attrs, `--ot-*` custom-property CSS for ParagraphProperties |
| 3 | `parse/markdown.py` | Round-trip parser: frontmatter, headings, paragraphs, pandoc `::: {…}` divs, inline bold/italic/strike/link, lists, code; `parse(emit(ast)) == ast` proven for fixture set |
| 4 | `ops/{primitives,diff}.py` | `OpPlan` IR + two-phase diff (structural by stable id, content via `difflib`) |
| 5 | `apply/{policy,docs_api}.py` | Channel-selection dispatcher (one table, currently all → Docs API); translate primitives to `batchUpdate` Request dicts; runner that fetches + translates + applies |
| 6 | `cli_push.py`, `cli.py` | `gdoc push` with `--new --title`, `--force`, `--dry-run`, `--plan-only PATH` |
| 7 | `tests/round_trip/` | FakeDocsService + end-to-end property test |

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

- **Three-way merge against the remote.** `push` requires `--force` for
  existing docs; no fetch-and-merge pass. The design spec has the full
  symmetric flow.
- **`/save` channel backend.** Only Docs API is wired. `apply/kix_save.py`
  is a future addition; the policy dispatcher already has the routing
  slot for it.
- **Authoring comments / suggestions / chips.** Reading them stays as
  v1; writing them needs the `/save` channel or Drive Comments v3 with
  new request shapes.
- **Conflict UX** (`--continue`, `--abort`, `.gd-conflict` divs).
- **Automated live e2e tests** against a real doc. One manual smoke
  test now passes (see "Live smoke test" above); turning it into an
  automated test requires a service account or sandbox doc the CI
  can write to. The mock-service property test in
  `tests/round_trip/test_full_pipeline_mock.py` covers it offline.
- **Pull-time `paragraph_id` synthesis.** `parse` accepts paragraph_ids
  when present but `ast/from_docs_json.py` doesn't yet stamp them on
  fresh pulls. For now the diff falls back to "everything is an anonymous
  insert" when ids are missing — which is what makes `--new` work.

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
  cli_push.py              <- push_new / push_force / push_dry_run / serializers

  ast/nodes.py             <- extended (ParagraphProperties, paragraph_id,
                                 VotingChip, Voter, Document.gdoc_state,
                                 fuller StyleDescriptor)
  emit/markdown.py         <- _emit_frontmatter handles gdoc:, paragraph_id
                                 attrs, extra_ids in _format_attr_block
  parse/markdown.py        <- new round-trip parser (was stub)
  styles/css.py            <- paragraph_props_to_css for --ot-* output
  cli.py                   <- push subcommand wiring

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

The natural next chunk is **three-way merge** (chunk 8 the plan didn't
have), unblocked by what's already in. Approach:

1. Add `merge/three_way.py` with `merge(base_ast, local_ast, remote_ast)
   -> (merged_ast, conflicts)`. Use the existing `ops/diff.py` to compute
   the two halves; reconcile per-block-id using the matrix in the spec.
2. Emit a `Conflict` AST node + matching `.gd-conflict` emitter / parser.
3. Wire `push` to default to the merge path; `--force` keeps current
   behaviour.

The pull-time `paragraph_id` synthesis is a near-term unblocker for
`push --force` to round-trip cleanly. The two probably belong in the same
follow-up branch.
