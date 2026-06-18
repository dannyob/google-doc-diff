# Per-doc Replay State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Key `gdoc replay` state per doc so one git repo can hold many independently-resumable docs, with the state file as a rebuildable cache over git history.

**Architecture:** Replay state moves from a single `cwd/.gdoc-replay-state.json` to `cwd/.gdoc-state/<doc_id>.json` (one per doc). Each replay commit gains a `Gdoc-event: <event_id>` trailer, so when the state file is absent (fresh checkout) the committed-set is reconstructed exactly from `git log`. A unified guard treats a non-empty committed-set (from state file or git) as "existing history", so plain `replay` never duplicates commits.

**Tech Stack:** Python 3.11+, Click, pytest, `git` via subprocess (`replay/git.py`).

**Spec:** `docs/superpowers/specs/2026-06-17-replay-state-per-doc-design.md`

## Global Constraints

- Python ≥3.11; `from __future__ import annotations` at the top of every module (matches existing files).
- Ruff: line-length 100; rules `E,F,W,I,B,UP`. Run `ruff check .` and `ruff format .` clean before each commit.
- Run tests inside the venv: `source .venv/bin/activate` first, or prefix commands with it.
- `doc_id` values are `[A-Za-z0-9_-]{20,}` — safe as bare filenames; no sanitizing needed.
- Commit messages: first line states what changed; keep each commit to one concern.

---

### Task 1: Relocate state to `.gdoc-state/<doc_id>.json`

Change `replay/state.py` so its read/write/remove functions take an explicit file `Path` instead of deriving `cwd / FIXED_NAME`, add a `default_state_path(doc_id, cwd)` helper, and rewire the two CLI callers (`replay`, `fetch`).

**Files:**
- Modify: `src/google_doc_diff/replay/state.py`
- Modify: `src/google_doc_diff/cli.py` (the `replay` command ~248-447 and `fetch` command ~471-528)
- Test: `tests/unit/test_replay_state.py`

**Interfaces:**
- Produces:
  - `STATE_DIRNAME = ".gdoc-state"`, `LEGACY_STATE_FILENAME = ".gdoc-replay-state.json"`
  - `default_state_path(doc_id: str, cwd: Path | None = None) -> Path` → `cwd/.gdoc-state/<doc_id>.json`
  - `legacy_state_path(cwd: Path | None = None) -> Path` → `cwd/.gdoc-replay-state.json`
  - `write_state(state: ReplayState, path: Path) -> Path` (creates parent dir)
  - `read_state(path: Path) -> ReplayState | None`
  - `remove_state(path: Path) -> None`

- [ ] **Step 1: Write the failing test**

Replace the path-related tests in `tests/unit/test_replay_state.py` (keep the existing `ReplayState` serialization tests). Add:

```python
from pathlib import Path

from google_doc_diff.replay.state import (
    EventState,
    ReplayState,
    default_state_path,
    read_state,
    remove_state,
    write_state,
)


def _sample_state(doc_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"):
    return ReplayState(
        doc_id=doc_id, out_path="x.md", extract_assets=False,
        include_comments=True, since=None, until=None, timeline_hash="h",
        events=[EventState(id="rev-1", kind="prose_change",
                           timestamp="2026-01-01T00:00:00+00:00", author="a@b")],
    )


def test_default_state_path_is_per_doc(tmp_path):
    p1 = default_state_path("DOCA", tmp_path)
    p2 = default_state_path("DOCB", tmp_path)
    assert p1 == tmp_path / ".gdoc-state" / "DOCA.json"
    assert p2 == tmp_path / ".gdoc-state" / "DOCB.json"
    assert p1 != p2


def test_write_creates_parent_dir_and_round_trips(tmp_path):
    state = _sample_state()
    path = default_state_path(state.doc_id, tmp_path)
    assert not path.parent.exists()
    write_state(state, path)
    assert path.exists()
    back = read_state(path)
    assert back.doc_id == state.doc_id
    assert [e.id for e in back.events] == ["rev-1"]


def test_read_missing_returns_none(tmp_path):
    assert read_state(default_state_path("NOPE", tmp_path)) is None


def test_two_docs_one_dir_do_not_collide(tmp_path):
    a = _sample_state("DOCA")
    b = _sample_state("DOCB")
    write_state(a, default_state_path("DOCA", tmp_path))
    write_state(b, default_state_path("DOCB", tmp_path))
    assert read_state(default_state_path("DOCA", tmp_path)).doc_id == "DOCA"
    assert read_state(default_state_path("DOCB", tmp_path)).doc_id == "DOCB"


def test_remove_state(tmp_path):
    path = default_state_path("DOCA", tmp_path)
    write_state(_sample_state("DOCA"), path)
    remove_state(path)
    assert not path.exists()
    remove_state(path)  # idempotent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_replay_state.py -v`
Expected: FAIL — `ImportError: cannot import name 'default_state_path'`.

- [ ] **Step 3: Rewrite the path functions in `state.py`**

Replace the bottom of `src/google_doc_diff/replay/state.py` (from `STATE_FILENAME` and the four path functions) with:

```python
STATE_DIRNAME = ".gdoc-state"
LEGACY_STATE_FILENAME = ".gdoc-replay-state.json"


def default_state_path(doc_id: str, cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / STATE_DIRNAME / f"{doc_id}.json"


def legacy_state_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / LEGACY_STATE_FILENAME


def write_state(state: ReplayState, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.to_json())
    return path


def read_state(path: Path) -> ReplayState | None:
    if not path.exists():
        return None
    return ReplayState.from_json(path.read_text())


def remove_state(path: Path) -> None:
    if path.exists():
        path.unlink()
```

(Keep `STATE_FILENAME` deleted — `LEGACY_STATE_FILENAME` replaces its one remaining use.)

- [ ] **Step 4: Rewire `replay` in `cli.py`**

In the `replay` command, immediately after `cwd = Path.cwd()` add the state-path resolution, and replace every `read_state(cwd)` / `write_state(state, cwd)` / `remove_state(cwd)` with the explicit path. Also move the `out_path` computation up to just after `doc_id` is resolved (the duplicate-guard task needs it early; harmless now).

Replace:
```python
    doc_id, path_hint = resolve_doc_target(doc)
    cwd = Path.cwd()

    existing = read_state(cwd)
```
with:
```python
    doc_id, path_hint = resolve_doc_target(doc)
    cwd = Path.cwd()
    out_path = out or path_hint or Path(_slugify(doc_id) + ".md")
    state_file = default_state_path(doc_id, cwd)

    existing = read_state(state_file)
```

Then delete the later line `out_path = out or path_hint or Path(_slugify(doc_id) + ".md")` (now computed above).

Replace `remove_state(cwd)` → `remove_state(state_file)`.
Replace both `write_state(state, cwd)` → `write_state(state, state_file)`.
In `_on_event`, replace `write_state(state, cwd)` → `write_state(state, state_file)`.

Update the two user-facing path strings:
```python
        click.echo(
            f"{state_file} exists. Use --resume to continue "
            "or --restart to discard.",
            err=True,
        )
```
and
```python
    click.echo(
        f"replayed {len(pending)} event(s); state: {state_file}"
    )
```

Update the import line in the command to pull in `default_state_path`:
```python
    from google_doc_diff.replay.state import (
        EventState,
        ReplayState,
        default_state_path,
        read_state,
        remove_state,
        write_state,
    )
```

- [ ] **Step 5: Rewire `fetch` in `cli.py`**

`fetch` previously read `cwd` state before resolving a doc, and supported a bare no-arg call. Per-doc state needs the doc id first. Replace the head of `fetch` (the `from ... import read_state` through the `out_path` resolution) with:

```python
    from google_doc_diff.replay.state import default_state_path, read_state

    cwd = Path.cwd()
    if not doc:
        click.echo("fetch needs a doc id, URL, or .md path "
                   "(per-doc state means there is no single default).", err=True)
        sys.exit(2)
    doc_id, path_hint = resolve_doc_target(doc)
    state = read_state(default_state_path(doc_id, cwd))

    if out:
        out_path = out
    elif path_hint:
        out_path = path_hint
    elif state:
        out_path = Path(state.out_path)
    else:
        out_path = Path(_slugify(doc_id) + ".md")
```

- [ ] **Step 6: Run tests**

Run: `source .venv/bin/activate && pytest tests/unit/test_replay_state.py tests/test_cli.py -v`
Expected: PASS. If a `test_cli.py` test asserted bare-`fetch` behaviour or the old state filename, update it to the new contract (bare `fetch` exits 2; state lives at `.gdoc-state/<doc_id>.json`).

- [ ] **Step 7: Lint and commit**

```bash
source .venv/bin/activate && ruff check . && ruff format --check .
git add src/google_doc_diff/replay/state.py src/google_doc_diff/cli.py tests/unit/test_replay_state.py tests/test_cli.py
git commit -m "replay: key state per doc under .gdoc-state/<doc_id>.json"
```

---

### Task 2: Add `Gdoc-event` commit trailer

Each replay commit records its `event_id` as a git trailer so the committed-set is exactly recoverable from history.

**Files:**
- Modify: `src/google_doc_diff/replay/git.py` (`commit` ~34-60)
- Modify: `src/google_doc_diff/replay/runner.py` (`_commit_event` ~218-233)
- Test: `tests/unit/test_replay_git.py`

**Interfaces:**
- Consumes: `Event.event_id` (`replay/timeline.py:43`).
- Produces: `commit(message, *, author_name, author_email, timestamp, cwd=None, event_id=None) -> str` — appends a `Gdoc-event: <event_id>` trailer when `event_id` is given.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_replay_git.py`:

```python
import subprocess

from google_doc_diff.replay import git as gitwrap


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)


def test_commit_writes_gdoc_event_trailer(tmp_path):
    from datetime import datetime, timezone
    _init_repo(tmp_path)
    (tmp_path / "x.md").write_text("hi")
    gitwrap.add([tmp_path / "x.md"], cwd=tmp_path)
    sha = gitwrap.commit(
        "prose: revision 9245",
        author_name="Alice", author_email="a@b",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        cwd=tmp_path, event_id="rev-9245",
    )
    trailer = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Gdoc-event,valueonly)", sha],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert trailer == "rev-9245"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_replay_git.py::test_commit_writes_gdoc_event_trailer -v`
Expected: FAIL — `commit()` got an unexpected keyword argument `event_id`.

- [ ] **Step 3: Implement the trailer in `git.py`**

Change `commit`'s signature and message assembly:

```python
def commit(
    message: str,
    *,
    author_name: str,
    author_email: str,
    timestamp: datetime,
    cwd: Path | None = None,
    event_id: str | None = None,
) -> str:
    """Create a commit with the given author identity + timestamp.

    When event_id is given, append a `Gdoc-event: <event_id>` trailer so the
    committed event is recoverable from `git log` without the state file.

    Returns the new commit's SHA.
    """
    iso = timestamp.isoformat()
    full_message = message
    if event_id:
        full_message = f"{message}\n\nGdoc-event: {event_id}\n"
    env = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_AUTHOR_DATE": iso,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
        "GIT_COMMITTER_DATE": iso,
    }
    _run(
        ["git", "commit", "-m", full_message, "--allow-empty"],
        cwd=cwd, extra_env=env,
    )
    r = _run(["git", "rev-parse", "HEAD"], cwd=cwd, capture=True)
    return r.stdout.strip()
```

- [ ] **Step 4: Pass `event_id` from the runner**

In `runner.py` `_commit_event`, add `event_id=ev.event_id` to the `gitwrap.commit(...)` call:

```python
        return gitwrap.commit(
            msg,
            author_name=name,
            author_email=email,
            timestamp=ev.timestamp,
            cwd=self.opt.cwd,
            event_id=ev.event_id,
        )
```

- [ ] **Step 5: Run tests**

Run: `source .venv/bin/activate && pytest tests/unit/test_replay_git.py -v`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
source .venv/bin/activate && ruff check . && ruff format --check .
git add src/google_doc_diff/replay/git.py src/google_doc_diff/replay/runner.py tests/unit/test_replay_git.py
git commit -m "replay: record Gdoc-event trailer on each commit"
```

---

### Task 3: Reconstruct the committed-set from git

Add a function that reads `git log`, maps each commit's `Gdoc-event` trailer (with a message+author-date fallback for pre-trailer commits) to a sha, and returns the `{event_id: sha}` for events present in the supplied timeline. Move `_commit_message_for` to `state.py` so both the runner and reconstruction share it.

**Files:**
- Modify: `src/google_doc_diff/replay/runner.py` (remove local `_commit_message_for`, import from state)
- Modify: `src/google_doc_diff/replay/state.py` (add `commit_message_for`, `reconstruct_committed_set`)
- Test: `tests/unit/test_replay_reconstruct.py` (new)

**Interfaces:**
- Consumes: `Event` (`replay/timeline.py`), `commit()` trailer from Task 2.
- Produces:
  - `commit_message_for(ev: Event) -> str` (the function formerly named `_commit_message_for` in runner.py, verbatim body).
  - `reconstruct_committed_set(events: list[Event], cwd: Path) -> dict[str, str]` → `{event_id: sha}` for timeline events that have a matching commit. Trailer match first; for commits lacking the trailer, fall back to `(commit_message_for(ev), author-date)`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_replay_reconstruct.py`:

```python
import subprocess
from datetime import datetime, timezone

from google_doc_diff.replay import git as gitwrap
from google_doc_diff.replay.state import reconstruct_committed_set
from google_doc_diff.replay.timeline import Event


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)


def _ev(kind, ts, *, revision_id=None, comment_id=None, reply_id=None):
    return Event(kind=kind, timestamp=ts, author="a@b",
                 revision_id=revision_id, comment_id=comment_id, reply_id=reply_id)


def test_reconstruct_matches_by_trailer(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "x.md").write_text("v1")
    gitwrap.add([tmp_path / "x.md"], cwd=tmp_path)
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    e1 = _ev("prose_change", ts, revision_id="9245")
    sha1 = gitwrap.commit("prose: revision 9245", author_name="A", author_email="a@b",
                          timestamp=ts, cwd=tmp_path, event_id=e1.event_id)
    (tmp_path / "x.md").write_text("v2")
    gitwrap.add([tmp_path / "x.md"], cwd=tmp_path)
    ts2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    e2 = _ev("comment_create", ts2, comment_id="c-XYZ6")
    sha2 = gitwrap.commit("comment: c-XYZ6", author_name="A", author_email="a@b",
                          timestamp=ts2, cwd=tmp_path, event_id=e2.event_id)

    # e3 is in the timeline but never committed -> not in the result.
    e3 = _ev("prose_change", datetime(2026, 1, 3, tzinfo=timezone.utc), revision_id="9300")

    result = reconstruct_committed_set([e1, e2, e3], tmp_path)
    assert result == {e1.event_id: sha1, e2.event_id: sha2}


def test_reconstruct_falls_back_to_message_and_date_without_trailer(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "x.md").write_text("v1")
    gitwrap.add([tmp_path / "x.md"], cwd=tmp_path)
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Pre-trailer commit: no event_id passed.
    sha = gitwrap.commit("prose: revision 9245", author_name="A", author_email="a@b",
                         timestamp=ts, cwd=tmp_path)
    e1 = _ev("prose_change", ts, revision_id="9245")
    result = reconstruct_committed_set([e1], tmp_path)
    assert result == {e1.event_id: sha}


def test_reconstruct_empty_when_no_repo_history(tmp_path):
    _init_repo(tmp_path)
    e1 = _ev("prose_change", datetime(2026, 1, 1, tzinfo=timezone.utc), revision_id="1")
    assert reconstruct_committed_set([e1], tmp_path) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/unit/test_replay_reconstruct.py -v`
Expected: FAIL — `cannot import name 'reconstruct_committed_set'`.

- [ ] **Step 3: Move `commit_message_for` into `state.py`**

In `src/google_doc_diff/replay/runner.py`, delete the `_commit_message_for(ev)` function (~288-303) and replace its single call site in `_commit_event` (`msg = _commit_message_for(ev)`) with `msg = commit_message_for(ev)`. Add to the runner's imports:

```python
from google_doc_diff.replay.state import commit_message_for
```

In `src/google_doc_diff/replay/state.py`, add near the top (after imports). Note `state.py` must not import `runner` (circular) — it imports `Event` lazily inside the function to avoid an import cycle:

```python
def commit_message_for(ev) -> str:
    """Commit subject for a replay event. Shared by the runner (to write
    commits) and reconstruction (to match pre-trailer commits)."""
    if ev.kind == "prose_change":
        return f"prose: revision {ev.revision_id}"
    if ev.kind == "comment_create":
        return f"comment: {ev.comment_id}"
    if ev.kind == "comment_edit":
        return f"comment edit: {ev.comment_id}"
    if ev.kind == "comment_delete":
        return f"comment delete: {ev.comment_id}"
    if ev.kind == "reply_create":
        return f"reply: {ev.comment_id} {ev.reply_id}"
    if ev.kind == "reply_resolve":
        return f"resolve: {ev.comment_id}"
    if ev.kind == "reply_reopen":
        return f"reopen: {ev.comment_id}"
    return ev.kind
```

- [ ] **Step 4: Implement `reconstruct_committed_set`**

Add to `src/google_doc_diff/replay/state.py`:

```python
def reconstruct_committed_set(events, cwd: Path) -> dict[str, str]:
    """Recover {event_id: sha} from git history when the state file is gone.

    Each replay commit carries a `Gdoc-event: <event_id>` trailer (exact
    match). Commits predating that trailer are matched by
    (commit_message_for(ev), author-date), which is unique in practice.
    Only events present in `events` are returned.
    """
    import subprocess
    from datetime import datetime

    # sha \0 author-date \0 subject \0 trailer-value, one record per commit.
    fmt = "%H%x00%aI%x00%s%x00%(trailers:key=Gdoc-event,valueonly)"
    try:
        out = subprocess.run(
            ["git", "log", f"--format={fmt}"],
            cwd=str(cwd), capture_output=True, text=True,
        )
    except FileNotFoundError:
        return {}
    if out.returncode != 0:
        return {}

    by_event: dict[str, str] = {}                 # event_id -> sha (from trailer)
    by_msg_date: dict[tuple[str, str], str] = {}  # (subject, iso) -> sha (fallback)
    for line in out.stdout.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\x00")
        if len(parts) < 4:
            continue
        sha, author_date, subject, trailer = parts[0], parts[1], parts[2], parts[3].strip()
        if trailer:
            by_event.setdefault(trailer, sha)
        try:
            iso = datetime.fromisoformat(author_date).isoformat()
        except ValueError:
            iso = author_date
        by_msg_date.setdefault((subject, iso), sha)

    result: dict[str, str] = {}
    for ev in events:
        if ev.event_id in by_event:
            result[ev.event_id] = by_event[ev.event_id]
            continue
        key = (commit_message_for(ev), ev.timestamp.isoformat())
        if key in by_msg_date:
            result[ev.event_id] = by_msg_date[key]
    return result
```

- [ ] **Step 5: Run tests**

Run: `source .venv/bin/activate && pytest tests/unit/test_replay_reconstruct.py tests/unit/test_replay_git.py -v`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
source .venv/bin/activate && ruff check . && ruff format --check .
git add src/google_doc_diff/replay/state.py src/google_doc_diff/replay/runner.py tests/unit/test_replay_reconstruct.py
git commit -m "replay: reconstruct committed-set from git log via Gdoc-event trailer"
```

---

### Task 4: Unified committed-set resolution, duplicate guard, gitignore hint

Wire reconstruction into the `replay` command: when no state file exists in commit mode, reconstruct from git; treat any non-empty committed-set as "existing history" for the resume/restart guard; print a gitignore hint; and make the dirty-check ignore the whole `.gdoc-state/` directory.

**Files:**
- Modify: `src/google_doc_diff/cli.py` (`replay` command)
- Modify: `src/google_doc_diff/replay/git.py` (`is_clean` ~14-25)
- Test: `tests/round_trip/test_replay_multidoc.py` (new — integration, mocked API, real temp git repo)

**Interfaces:**
- Consumes: `reconstruct_committed_set` (Task 3), `default_state_path` (Task 1), `Gdoc-event` trailer (Task 2).
- Produces: `is_clean(cwd=None, ignore=None, ignore_prefixes=None) -> bool` — `ignore_prefixes` skips any porcelain path starting with one of the given prefixes.

- [ ] **Step 1: Write the failing integration test**

Create `tests/round_trip/test_replay_multidoc.py`. This drives the `replay` CLI with a mocked `GdocAPI` so two docs replay into one repo, then deletes `.gdoc-state/` and resumes doc A purely from git, asserting no duplicate commits.

```python
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from google_doc_diff.cli import cli


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout


def _count_commits(cwd):
    out = _git(["rev-list", "--count", "HEAD"], cwd)
    return int(out.strip())


# A doc id is >=20 chars of [A-Za-z0-9_-].
DOC_A = "AAAAAAAAAAAAAAAAAAAAAAA"
DOC_B = "BBBBBBBBBBBBBBBBBBBBBBB"


def _fake_api_for(doc_id):
    """One prose revision via Drive v2 exportLinks, no comments."""
    api = mock.Mock()
    rev = {
        "id": "1",
        "modifiedDate": "2026-01-01T00:00:00.000Z",
        "lastModifyingUser": {"emailAddress": "a@b", "displayName": "A"},
        "exportLinks": {"text/markdown": f"https://example/{doc_id}/md"},
    }
    api.list_revisions.return_value = [rev]
    api.list_comments.return_value = []
    api.fetch_revision_export.return_value = b"# Title\n\nBody for " + doc_id.encode()
    api.get_document.return_value = {"title": f"Doc {doc_id}", "tabs": []}
    return api


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def _run_replay(repo, doc_id, out_rel, *extra):
    runner = CliRunner()
    with mock.patch("google_doc_diff.cli.GdocAPI", return_value=_fake_api_for(doc_id)), \
         mock.patch("google_doc_diff.cli.load_credentials", return_value=mock.Mock()), \
         runner.isolated_filesystem(temp_dir=repo.parent):
        # isolated_filesystem cd's into a new dir; we want the repo itself.
        import os
        os.chdir(repo)
        return runner.invoke(cli, ["replay", doc_id, "--out", out_rel, *extra])


def test_two_docs_one_repo_independent_state(repo):
    (repo / "a").mkdir()
    (repo / "b").mkdir()
    r1 = _run_replay(repo, DOC_A, "a/a.md")
    assert r1.exit_code == 0, r1.output
    r2 = _run_replay(repo, DOC_B, "b/b.md")
    assert r2.exit_code == 0, r2.output
    assert (repo / ".gdoc-state" / f"{DOC_A}.json").exists()
    assert (repo / ".gdoc-state" / f"{DOC_B}.json").exists()
    assert _count_commits(repo) == 2  # one prose commit per doc


def test_resume_from_git_after_state_deleted_makes_no_duplicates(repo):
    (repo / "a").mkdir()
    r1 = _run_replay(repo, DOC_A, "a/a.md")
    assert r1.exit_code == 0, r1.output
    before = _count_commits(repo)
    # Simulate a fresh checkout: state cache gone, git history intact.
    import shutil
    shutil.rmtree(repo / ".gdoc-state")
    r2 = _run_replay(repo, DOC_A, "a/a.md", "--resume")
    assert r2.exit_code == 0, r2.output
    assert _count_commits(repo) == before  # reconstructed; nothing re-committed
    assert (repo / ".gdoc-state" / f"{DOC_A}.json").exists()  # cache rebuilt


def test_plain_replay_on_populated_repo_refuses(repo):
    (repo / "a").mkdir()
    assert _run_replay(repo, DOC_A, "a/a.md").exit_code == 0
    import shutil
    shutil.rmtree(repo / ".gdoc-state")
    r = _run_replay(repo, DOC_A, "a/a.md")  # no --resume/--restart
    assert r.exit_code == 2
    assert "resume" in r.output.lower() or "restart" in r.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/round_trip/test_replay_multidoc.py -v`
Expected: FAIL — `test_resume_from_git...` and `test_plain_replay...` fail because reconstruction isn't wired in (state-less resume re-commits or errors wrongly).

- [ ] **Step 3: Add `ignore_prefixes` to `is_clean`**

In `src/google_doc_diff/replay/git.py`, replace `is_clean`:

```python
def is_clean(
    cwd: Path | None = None,
    ignore: list[str] | None = None,
    ignore_prefixes: list[str] | None = None,
) -> bool:
    """True iff the working tree has no staged/unstaged changes (excluding
    untracked, plus any paths in `ignore` or under any of `ignore_prefixes`)."""
    r = _run(["git", "status", "--porcelain"], cwd=cwd, capture=True)
    ignored = set(ignore or [])
    prefixes = tuple(ignore_prefixes or ())
    for line in r.stdout.splitlines():
        path = line[3:].strip()
        if path in ignored:
            continue
        if prefixes and path.startswith(prefixes):
            continue
        return False
    return True
```

- [ ] **Step 4: Wire resolution + guard into `replay`**

In `cli.py` `replay`, the early guard currently fires only when a state *file* exists. Reconstruction needs the timeline, which is built after the API calls. So: keep the early file-based fast path, and add a post-timeline reconstruction + guard.

Replace the early guard block:
```python
    existing = read_state(state_file)
    if existing and not (resume or restart):
        click.echo(
            f"{state_file} exists. Use --resume to continue "
            "or --restart to discard.",
            err=True,
        )
        sys.exit(2)
    if restart:
        remove_state(state_file)
        existing = None
```
with (note: only the `restart` removal stays here; the no-flag refusal moves below so it can also cover git-reconstructed history):
```python
    existing = read_state(state_file)
    if restart:
        remove_state(state_file)
        existing = None
```

Then, right after `new_hash = timeline_hash(events)` (the timeline is now built), insert the reconstruction + unified guard:
```python
    # If no state file but we're committing into an existing git history
    # (e.g. a fresh checkout), rebuild the committed-set from git so resume
    # continues instead of duplicating commits.
    if existing is None and commit and (cwd / ".git").exists():
        from google_doc_diff.replay.state import reconstruct_committed_set
        recovered = reconstruct_committed_set(events, cwd)
        if recovered:
            existing = ReplayState(
                doc_id=doc_id, out_path=str(out_path),
                extract_assets=extract_assets, include_comments=include_comments,
                since=since, until=until, timeline_hash=new_hash,
                events=[EventState(**event_to_state_dict(e)) for e in events],
            )
            for est in existing.events:
                if est.id in recovered:
                    est.status = "committed"
                    est.git_sha = recovered[est.id]
            click.echo(
                f"reconstructed {len(recovered)} committed event(s) from git history."
            )

    if existing and not (resume or restart):
        click.echo(
            f"replay history exists for this doc ({state_file} or git). "
            "Use --resume to continue or --restart to discard.",
            err=True,
        )
        sys.exit(2)
```

Because a reconstructed `existing` has `timeline_hash == new_hash` and matching timestamps, the downstream `_can_reconcile`/merge path on `--resume` accepts it unchanged.

- [ ] **Step 5: Update the dirty-check call + add the gitignore hint**

In the `if commit:` block, replace the ignore wiring:
```python
        # State dir + the in-flight output .md are expected-dirty.
        ignore = []
        try:
            ignore.append(str(out_path.relative_to(cwd)))
        except ValueError:
            pass
        if not gitwrap.is_clean(cwd, ignore=ignore,
                                ignore_prefixes=[".gdoc-state/", ".gdoc-state"]):
            click.echo("git working tree is dirty; commit or stash first, or "
                       "pass --no-commit.", err=True)
            sys.exit(2)
        # Nudge the user to gitignore the cache dir (we don't edit it for them).
        gi = cwd / ".gitignore"
        already = gi.exists() and any(
            line.strip().rstrip("/") == ".gdoc-state"
            for line in gi.read_text().splitlines()
        )
        if not already:
            click.echo("hint: add '.gdoc-state/' to .gitignore "
                       "(replay state is a rebuildable cache).", err=True)
```

- [ ] **Step 6: Run tests**

Run: `source .venv/bin/activate && pytest tests/round_trip/test_replay_multidoc.py tests/unit/test_replay_git.py -v`
Expected: PASS. Then the full suite: `pytest -q`. Fix any `test_cli.py`/`test_replay*` cases that asserted the old single-file guard text.

- [ ] **Step 7: Lint and commit**

```bash
source .venv/bin/activate && ruff check . && ruff format --check .
git add src/google_doc_diff/cli.py src/google_doc_diff/replay/git.py tests/round_trip/test_replay_multidoc.py
git commit -m "replay: resolve committed-set from state or git; guard against duplicate history"
```

---

### Task 5: `--state PATH` override and legacy state migration

Add an explicit `--state` override to `replay`, and migrate a pre-existing `./.gdoc-replay-state.json` into the new per-doc location on first use.

**Files:**
- Modify: `src/google_doc_diff/cli.py` (`replay` command)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `default_state_path`, `legacy_state_path`, `read_state`, `write_state` (Task 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (CLI-level, `--dry-run` keeps it offline):

```python
import json
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from google_doc_diff.cli import cli

_DOC = "CCCCCCCCCCCCCCCCCCCCCCC"


def _patched_invoke(args):
    runner = CliRunner()
    api = mock.Mock()
    api.list_revisions.return_value = []
    api.list_comments.return_value = []
    with mock.patch("google_doc_diff.cli.GdocAPI", return_value=api), \
         mock.patch("google_doc_diff.cli.load_credentials", return_value=mock.Mock()):
        return runner.invoke(cli, args)


def test_legacy_state_is_migrated(tmp_path):
    legacy = tmp_path / ".gdoc-replay-state.json"
    legacy.write_text(json.dumps({
        "doc_id": _DOC, "out_path": "x.md", "extract_assets": False,
        "include_comments": True, "since": None, "until": None,
        "timeline_hash": "h", "events": [],
    }))
    runner = CliRunner()
    with mock.patch("google_doc_diff.cli.GdocAPI") as api_cls, \
         mock.patch("google_doc_diff.cli.load_credentials", return_value=mock.Mock()):
        api = api_cls.return_value
        api.list_revisions.return_value = []
        api.list_comments.return_value = []
        import os
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            res = runner.invoke(cli, ["replay", _DOC, "--out", "x.md",
                                      "--no-commit", "--resume"])
        finally:
            os.chdir(cwd)
    assert res.exit_code == 0, res.output
    assert (tmp_path / ".gdoc-state" / f"{_DOC}.json").exists()


def test_state_override_path(tmp_path):
    custom = tmp_path / "custom-state.json"
    runner = CliRunner()
    with mock.patch("google_doc_diff.cli.GdocAPI") as api_cls, \
         mock.patch("google_doc_diff.cli.load_credentials", return_value=mock.Mock()):
        api = api_cls.return_value
        api.list_revisions.return_value = []
        api.list_comments.return_value = []
        import os
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            res = runner.invoke(cli, ["replay", _DOC, "--out", "x.md",
                                      "--no-commit", "--state", str(custom)])
        finally:
            os.chdir(cwd)
    assert res.exit_code == 0, res.output
    assert custom.exists()
    assert not (tmp_path / ".gdoc-state" / f"{_DOC}.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_cli.py::test_state_override_path tests/test_cli.py::test_legacy_state_is_migrated -v`
Expected: FAIL — `--state` is not a known option; legacy file is ignored.

- [ ] **Step 3: Add the `--state` option**

Add to the `replay` command decorators (next to `--restart`):

```python
@click.option("--state", "state_override", type=click.Path(path_type=Path),
              help="Explicit replay-state file path "
                   "(default: .gdoc-state/<doc_id>.json).")
```

Add `state_override` to the `def replay(...)` parameter list.

- [ ] **Step 4: Apply override + legacy migration**

Replace the state-path resolution added in Task 1:
```python
    state_file = default_state_path(doc_id, cwd)

    existing = read_state(state_file)
```
with:
```python
    state_file = state_override or default_state_path(doc_id, cwd)

    existing = read_state(state_file)
    if existing is None and not state_override:
        legacy = read_state(legacy_state_path(cwd))
        if legacy is not None and legacy.doc_id == doc_id:
            write_state(legacy, state_file)   # migrate into .gdoc-state/<doc_id>.json
            existing = legacy
            click.echo(f"migrated legacy {legacy_state_path(cwd)} "
                       f"-> {state_file}.", err=True)
```

Add `legacy_state_path` to the command's `from google_doc_diff.replay.state import (...)` block.

- [ ] **Step 5: Run tests**

Run: `source .venv/bin/activate && pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite, lint, smoke test, commit**

```bash
source .venv/bin/activate && pytest -q && ruff check . && ruff format --check .
```
End-to-end smoke (against the installed entry point, per the project's "rebuild the binary" rule):
```bash
source .venv/bin/activate && uv pip install -e . >/dev/null
gdoc replay --help | grep -- --state    # confirms the option is wired
```
Commit:
```bash
git add src/google_doc_diff/cli.py tests/test_cli.py
git commit -m "replay: add --state override and migrate legacy state file"
```

---

## Self-Review

**Spec coverage:**
- `.gdoc-state/<doc_id>.json` per doc → Task 1. ✓
- Gitignored, hint not auto-edit → Task 4 Step 5. ✓
- `Gdoc-event` trailer → Task 2. ✓
- Committed-set resolution (state → git → fresh) → Task 4 Step 4. ✓
- Pre-trailer message+date fallback → Task 3 Step 4 + test. ✓
- Squash caveat → behavioural, documented in spec; no task needed (reconstruction matches representative event_ids when the same `--squash-by-author` is passed). ✓
- `--no-commit` relies on state file only → preserved (reconstruction is gated on `commit and .git`). ✓
- Unified duplicate-history guard → Task 4 Step 4. ✓
- `--state` override → Task 5. ✓
- Legacy migration → Task 5 Step 4. ✓
- `fetch` rewiring → Task 1 Step 5. ✓
- `is_clean` ignores `.gdoc-state/` → Task 4 Step 3 + 5. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `default_state_path`/`legacy_state_path`/`read_state`/`write_state`/`remove_state` take a `Path` consistently across Tasks 1, 4, 5. `commit(..., event_id=...)` defined in Task 2, consumed in Task 2 (runner) and tested in Task 3. `commit_message_for` defined in Task 3, used by runner + reconstruction. `reconstruct_committed_set(events, cwd) -> dict[str,str]` defined Task 3, consumed Task 4. `is_clean(..., ignore_prefixes=...)` defined + consumed Task 4. ✓

**Note for the implementer:** `tests/test_cli.py` and `tests/unit/test_replay_state.py` already exercise the old single-file/bare-`fetch` behaviour. Each task that changes that contract updates the affected tests in the same commit; if `pytest -q` surfaces an old assertion not mentioned here, update it to the new contract (per-doc path, bare `fetch` exits 2, guard text mentions resume/restart) rather than reverting the change.
