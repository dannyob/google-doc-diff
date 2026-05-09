"""Tests for replay/git.py — exercises against a real temporary git repo."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from google_doc_diff.replay import git as gitwrap


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    return tmp_path


def test_is_clean_true_for_fresh_repo(repo):
    assert gitwrap.is_clean(repo) is True


def test_is_clean_false_after_change(repo):
    (repo / "f.txt").write_text("hi")
    _git(["add", "f.txt"], repo)
    assert gitwrap.is_clean(repo) is False


def test_is_clean_ignores_listed_paths(repo):
    (repo / "noise.txt").write_text("noise")
    _git(["add", "noise.txt"], repo)
    assert gitwrap.is_clean(repo, ignore=["noise.txt"]) is True
    assert gitwrap.is_clean(repo) is False


def test_commit_records_author_and_timestamp(repo):
    (repo / "f.md").write_text("content")
    gitwrap.add([Path("f.md")], cwd=repo)
    when = datetime(2026, 5, 1, 12, 30, tzinfo=UTC)
    sha = gitwrap.commit(
        "first",
        author_name="Alice",
        author_email="alice@example.com",
        timestamp=when,
        cwd=repo,
    )
    assert sha
    log = subprocess.run(
        ["git", "log", "--format=%an|%ae|%aI", "-1"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    name, email, when_iso = log.split("|")
    assert name == "Alice"
    assert email == "alice@example.com"
    assert when_iso.startswith("2026-05-01T12:30:00")
