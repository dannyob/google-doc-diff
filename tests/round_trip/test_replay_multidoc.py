"""Integration tests: two docs in one repo, state reconstruction from git."""

from __future__ import annotations

import os
import shutil
import subprocess
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
    """One prose revision via Drive v2 exportLinks, no comments.

    Both docs intentionally use revision id "1" to exercise cross-doc
    isolation: without file-scoped git log, doc B's reconstruction would
    match doc A's rev-1 commit and silently skip B's prose commit.
    """
    api = mock.Mock()
    rev = {
        "id": "1",  # intentionally identical across docs — tests isolation
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
         mock.patch("google_doc_diff.cli.load_credentials", return_value=mock.Mock()):
        old_cwd = os.getcwd()
        try:
            os.chdir(repo)
            return runner.invoke(cli, ["replay", doc_id, "--out", out_rel, *extra])
        finally:
            os.chdir(old_cwd)


def test_two_docs_one_repo_independent_state(repo):
    (repo / "a").mkdir()
    (repo / "b").mkdir()
    r1 = _run_replay(repo, DOC_A, "a/a.md")
    assert r1.exit_code == 0, r1.output
    # After replay the runner writes a "head state" uncommitted; commit it so
    # the tree is clean before starting the second doc (mirrors real usage).
    _git(["add", "a/a.md"], repo)
    _git(["commit", "-m", "head state"], repo)
    r2 = _run_replay(repo, DOC_B, "b/b.md")
    assert r2.exit_code == 0, r2.output
    assert (repo / ".gdoc-state" / f"{DOC_A}.json").exists()
    assert (repo / ".gdoc-state" / f"{DOC_B}.json").exists()
    # Both docs produce one prose commit each (+ 1 head-state commit = 3 total).
    # With identical revision id "1", an unscoped git log would match doc A's
    # rev-1 commit during doc B's reconstruction and silently skip B's prose
    # commit; the file-scoped fix ensures both are present.
    assert _count_commits(repo) == 3  # 1 prose commit per doc + 1 head-state commit


def test_resume_from_git_after_state_deleted_makes_no_duplicates(repo):
    (repo / "a").mkdir()
    r1 = _run_replay(repo, DOC_A, "a/a.md")
    assert r1.exit_code == 0, r1.output
    before = _count_commits(repo)
    # Simulate a fresh checkout: state cache gone, git history intact.
    shutil.rmtree(repo / ".gdoc-state")
    r2 = _run_replay(repo, DOC_A, "a/a.md", "--resume")
    assert r2.exit_code == 0, r2.output
    assert _count_commits(repo) == before  # reconstructed; nothing re-committed
    assert (repo / ".gdoc-state" / f"{DOC_A}.json").exists()  # cache rebuilt


def test_plain_replay_on_populated_repo_refuses(repo):
    (repo / "a").mkdir()
    assert _run_replay(repo, DOC_A, "a/a.md").exit_code == 0
    shutil.rmtree(repo / ".gdoc-state")
    r = _run_replay(repo, DOC_A, "a/a.md")  # no --resume/--restart
    assert r.exit_code == 2
    assert "resume" in r.output.lower() or "restart" in r.output.lower()
