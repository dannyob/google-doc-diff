# Per-doc replay state — design

GitHub issue #2. Lets a single git repo hold several docs replayed by
`gdoc replay`, each independently resumable, by keying replay state per doc
instead of one shared file per directory.

Scope is the state-keying change only. The issue's secondary observation —
optionally committing comment-only events — is deferred to its own design.

## Problem

`gdoc replay` writes one `.gdoc-replay-state.json` at the repo root
(`replay/state.py:47` — `state_path()` = `cwd / ".gdoc-replay-state.json"`).
`--resume`/`--restart` and `fetch` all read that single file. Replaying a
second doc into the same repo overwrites the first doc's state, so neither can
be incrementally resumed.

The target use case is an archive repo mirroring many source docs, commonly
several docs in one content directory:

```
pod-wbr/ldo/
  ldo-pod-wbr.md
  web2-pod-wbr.md
```

## What the state file actually holds

On every run, `replay` rebuilds the event timeline fresh from the Drive/Docs
API; the timeline is never read back from the state file. The only thing the
state file carries that isn't re-derived is **which events are already
committed** — per-event `status` (`pending`/`committed`/`failed`) and `git_sha`
— so resume can skip them.

Each replay commit already records the original author, the event timestamp
(`GIT_AUTHOR_DATE`), and an identifying message (`runner.py:288`,
`_commit_message_for`): `prose: revision <id>`, `comment: <id>`,
`reply: <cid> <rid>`, etc. So in commit mode the committed-set is recoverable
from git history. The state file is therefore a cache, not the source of
truth — git is. This design makes that explicit.

## Decisions

1. **Location & key.** State moves to `<cwd>/.gdoc-state/<doc_id>.json`, one
   file per doc. `doc_id` is the true per-doc identity, is unique (so any
   number of docs in one content directory coexist), is a safe filename
   (alphanumeric / `_` / `-`), and is stable if the `.md` is later renamed or
   moved. `.gdoc-state/` lives in `cwd`, consistent with `replay` already
   treating `cwd` as the repo root (it checks `cwd/.git` and commits there).

2. **`.gdoc-state/` is gitignored.** It is a rebuildable cache and must not
   pollute the archive. `replay` does **not** edit `.gitignore`; on first use
   in a repo where `.gdoc-state/` is untracked, it prints a one-line hint
   suggesting the user add `.gdoc-state/` to `.gitignore`.

3. **Commit trailer for exact reconstruction.** Every replay commit gains a
   trailer line:

   ```
   prose: revision 9245

   Gdoc-event: rev-9245
   ```

   The trailer value is `Event.event_id` (`timeline.py:43`) — the same stable
   key the state file uses (`rev-<id>`, `comment_create-c-<id>`,
   `reply_create-c-<id>-r-<id>`). Reconstruction is then an exact
   `event_id → sha` lookup rather than a heuristic message match.

4. **`--state PATH` override.** Points at an exact state file, bypassing the
   computed `.gdoc-state/<doc_id>.json` path, for non-standard layouts.

## Committed-set resolution

In **commit mode**, the committed-set is resolved once per run, in order:

1. If `.gdoc-state/<doc_id>.json` (or `--state PATH`) exists, use it (fast
   path; today's behaviour and on-disk shape, just relocated).
2. Else reconstruct from git: read `git log` for the current branch, extract
   each commit's `Gdoc-event` trailer, and build `event_id → sha`. Mark each
   freshly-built timeline event whose `event_id` is present as `committed`
   with that sha; everything else stays `pending`. Write the rebuilt state to
   `.gdoc-state/<doc_id>.json` so the scan is a one-time cost per checkout.
3. Else (no state, no matching git history) — a genuinely fresh replay; all
   events `pending`.

`git log` is read with
`--format=%H%x00%(trailers:key=Gdoc-event,valueonly,separator=%x00)` (or
equivalent) so parsing needs no message-body heuristics.

**Pre-trailer histories** (repos replayed before this change) have no trailer.
For those, reconstruction falls back to matching a timeline event to a commit
by `(_commit_message_for(ev), author-date)`; author-date carries the event
timestamp and the message carries the ids, which is unique in practice.
Unmatched commits are left out of the committed-set (worst case: a redundant
re-commit, never a wrong attribution).

**`--no-commit` mode** has no git to reconstruct from, so it relies solely on
the state file — behaviour unchanged.

### `--squash-by-author` caveat

Squashing coalesces adjacent same-author prose events into one commit carrying
the representative event's `event_id`. Reconstruction is exact only when the
same `--squash-by-author` value is re-passed, because the rebuilt timeline must
produce the same representative events. `--squash-by-author` is not persisted
(it never has been). A reconstructed resume with a different (or missing)
squash value may re-commit some prose events. This is documented, not fixed;
persisting the squash window and replaying its grouping is more machinery than
the case warrants.

## Unified duplicate-history guard

Today any commit-mode run with no state file re-replays the entire history —
plain `replay`, not only `--resume`. With reconstruction available, the guard
becomes uniform:

- In commit mode, always resolve the committed-set (state-or-git, above).
- If it is non-empty and neither `--resume` nor `--restart` was passed, refuse
  with the existing message ("state exists; pass --resume or --restart").
- `--restart` discards the state file *and* ignores reconstructed history,
  starting a fresh line.

So a fresh clone behaves exactly like a repo with an intact state file:
`replay --resume` continues cleanly; plain `replay` never silently duplicates.

## Wiring

- **`replay/state.py`**: `state_path`/`read_state`/`write_state`/`remove_state`
  take a resolved file path instead of deriving `cwd / FIXED_NAME`. A helper
  `default_state_path(doc_id, cwd)` returns `cwd/.gdoc-state/<doc_id>.json` and
  creates `.gdoc-state/` on write. New `reconstruct_committed_set(doc_id,
  timeline, cwd)` returning `{event_id: sha}` (uses the trailer; message+date
  fallback). `_commit_message_for` moves from `runner.py` to a shared location
  both the runner and reconstruction import.
- **`replay/git.py`**: `commit()` gains an `event_id` argument and appends the
  `Gdoc-event:` trailer (blank line + trailer, per git convention). `is_clean`
  ignores the `.gdoc-state/` prefix (today it ignores the single filename).
- **`cli.py` `replay`**: compute the state path from `doc_id` (or `--state`);
  apply the committed-set resolution and unified guard; print the gitignore
  hint when `.gdoc-state/` is untracked. Legacy migration: if the new path is
  absent but `./.gdoc-replay-state.json` exists and its `doc_id` matches, read
  it once and write it to the new location.
- **`cli.py` `fetch`**: compute the state path from the resolved `doc_id`.
  `fetch <path>.md` already resolves the doc from frontmatter, so it is
  unaffected. Bare no-args `fetch` in a multi-doc repo has no single state to
  read; it errors asking for a doc id, URL, or `.md` path (as it already does
  when no state is present).

## Testing

- **Path derivation** (unit): two docs → two distinct
  `.gdoc-state/<doc_id>.json`; `--state` override honoured; legacy
  `./.gdoc-replay-state.json` migrated when doc id matches.
- **Reconstruction** (unit): from a synthesized `git log` (trailers present),
  the committed-set equals the expected `event_id → sha`. Cases: a squashed
  prose commit, an empty-prose commit (`--allow-empty`), and a pre-trailer
  history exercising the message+date fallback.
- **Guard** (unit): non-empty committed-set with neither `--resume` nor
  `--restart` exits 2; `--restart` ignores existing history.
- **Trailer** (unit): `commit()` writes a parseable `Gdoc-event` trailer whose
  value round-trips through `git log --format=%(trailers:...)`.
- **Integration** (mocked API, real temp git repo): replay doc A and doc B into
  one repo → two independent state files; resume each independently; then delete
  `.gdoc-state/` and resume from git alone → no duplicate commits, history
  unchanged.

## Out of scope

- Committing comment-only events (issue #2's secondary note) — separate design.
- Persisting `--squash-by-author` for exact squashed reconstruction.
- Moving `pull`'s `<out>.pull-state.json` sidecar into `.gdoc-state/`.
