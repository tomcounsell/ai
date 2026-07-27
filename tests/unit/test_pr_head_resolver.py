"""Unit tests for tools.pr_head_resolver (#2404).

The verdict-staleness gate (#2062) compares a recorded REVIEW verdict's
``head_sha`` trailer against the PR's current head. A stale current-head read
in the fail-open direction returns the pre-push value -- the same value the
trailer carries -- so the gate sees a match and passes a verdict that predates
newly pushed code. These tests pin the guarantee that the authoritative git
read wins over a stale ``gh`` read, so a stale read can never make the gate
pass.
"""

from __future__ import annotations

import logging

import pytest

from tools import pr_head_resolver as r

OLD = "a" * 40  # the stale, pre-push SHA a cached/eventually-consistent gh serves
NEW = "b" * 40  # the true current head SHA (authoritative git ls-remote)


def test_git_primary_wins_over_stale_gh(monkeypatch):
    """The acceptance-bar case: gh serves the STALE sha, git serves the true
    head -- the resolver returns the git (authoritative) value, never gh's."""
    monkeypatch.setattr(r, "_git_ls_remote_pr_head", lambda pr, repo, root: NEW)
    monkeypatch.setattr(r, "_gh_pr_head", lambda pr, repo: OLD)
    assert r.resolve_pr_head_sha(42, repo="o/n", repo_root="/x") == NEW


def test_cross_check_disagreement_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(r, "_git_ls_remote_pr_head", lambda pr, repo, root: NEW)
    monkeypatch.setattr(r, "_gh_pr_head", lambda pr, repo: OLD)
    with caplog.at_level(logging.WARNING):
        assert r.resolve_pr_head_sha(42) == NEW
    assert any("head-SHA disagreement" in rec.message for rec in caplog.records)


def test_cross_check_false_skips_gh(monkeypatch):
    """With git resolved and cross_check off, gh is not consulted at all."""
    monkeypatch.setattr(r, "_git_ls_remote_pr_head", lambda pr, repo, root: NEW)

    def _boom(pr, repo):
        raise AssertionError("gh must not be called when git resolves and cross_check=False")

    monkeypatch.setattr(r, "_gh_pr_head", _boom)
    assert r.resolve_pr_head_sha(42, cross_check=False) == NEW


def test_gh_fallback_when_git_empty(monkeypatch):
    monkeypatch.setattr(r, "_git_ls_remote_pr_head", lambda pr, repo, root: None)
    monkeypatch.setattr(r, "_gh_pr_head", lambda pr, repo: NEW)
    assert r.resolve_pr_head_sha(42) == NEW


def test_both_unresolvable_returns_none(monkeypatch):
    monkeypatch.setattr(r, "_git_ls_remote_pr_head", lambda pr, repo, root: None)
    monkeypatch.setattr(r, "_gh_pr_head", lambda pr, repo: None)
    assert r.resolve_pr_head_sha(42) is None


def test_no_agreement_needed_when_only_git(monkeypatch):
    """git resolves, gh returns None -- git value is used, no warning, no error."""
    monkeypatch.setattr(r, "_git_ls_remote_pr_head", lambda pr, repo, root: NEW)
    monkeypatch.setattr(r, "_gh_pr_head", lambda pr, repo: None)
    assert r.resolve_pr_head_sha(42) == NEW


# --- _origin_matches_repo: the cross-repo cwd guard -------------------------


class _FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


@pytest.mark.parametrize(
    "repo,url,expected",
    [
        ("owner/name", "https://github.com/owner/name.git", True),
        ("owner/name", "https://github.com/owner/name", True),
        ("owner/name", "git@github.com:owner/name.git", True),
        ("owner/name", "https://github.com/other/repo.git", False),
        ("owner/name", "https://github.com/owner/name-suffix.git", False),
        (None, "https://github.com/anything/here.git", True),  # repo=None trusts local origin
    ],
)
def test_origin_matches_repo(monkeypatch, repo, url, expected):
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: _FakeProc(0, url + "\n"))
    assert r._origin_matches_repo(repo, "/x") is expected


def test_git_read_skipped_on_origin_mismatch(monkeypatch):
    """When origin points at a different repo, the git read is skipped entirely
    (returns None) so the caller falls back to gh --repo -- the #2377 cross-repo
    cwd hazard cannot resolve the wrong repo's head."""
    monkeypatch.setattr(r, "_origin_matches_repo", lambda repo, root: False)

    def _must_not_run(*a, **k):
        raise AssertionError("git ls-remote must not run when origin does not match repo")

    monkeypatch.setattr(r.subprocess, "run", _must_not_run)
    assert r._git_ls_remote_pr_head(42, "owner/name", "/x") is None
