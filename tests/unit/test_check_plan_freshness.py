"""Unit tests for scripts/check_plan_freshness.py.

Issue #2889/#2921: `_latest_issue_comment_id` must scope `gh issue view`
with --repo. A bare ``gh issue view N`` resolves GH_REPO from the environment
before cwd, so under a foreign GH_REPO it answers about a *different*
repository's issue #N and exits 0.
"""

import scripts.check_plan_freshness as cpf


class TestLatestIssueCommentIdRepoScoping:
    """Issue #2889: `_latest_issue_comment_id` must scope `gh issue view`.

    The argv must carry ``--repo <resolved-slug>`` when a repo resolves,
    mirroring the tools/sdlc_stage_query.py ladder (GH_REPO env first, else
    ``gh repo view --json nameWithOwner`` from the working-tree root /
    ``SDLC_TARGET_REPO``); when nothing resolves, the argv degrades to the
    prior unscoped shape and the None-on-failure contract is preserved.
    """

    def test_argv_scoped_from_gh_repo_env(self, monkeypatch):
        captured: dict = {}

        class FakeResult:
            returncode = 0
            stdout = (
                '{"comments": [{"url": '
                '"https://github.com/tomcounsell/ai/issues/42#issuecomment-777"}]}'
            )

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return FakeResult()

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(cpf.subprocess, "run", fake_run)
        assert cpf._latest_issue_comment_id("42") == "777"
        assert captured["argv"] == [
            "gh",
            "issue",
            "view",
            "42",
            "--repo",
            "tomcounsell/ai",
            "--json",
            "comments",
        ]

    def test_argv_scoped_from_derived_repo(self, monkeypatch):
        """GH_REPO unset: slug derived via gh repo view from the git root."""
        captured: list = []

        class IssueResult:
            returncode = 0
            stdout = (
                '{"comments": [{"url": '
                '"https://github.com/tomcounsell/ai/issues/42#issuecomment-777"}]}'
            )

        def fake_run(argv, **kwargs):
            captured.append(argv)
            if argv[:2] == ["git", "rev-parse"]:
                return type("GitResult", (), {"returncode": 0, "stdout": "/repo/root"})()
            if argv[:3] == ["gh", "repo", "view"]:
                return type("RepoResult", (), {"returncode": 0, "stdout": "tomcounsell/ai"})()
            return IssueResult()

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        monkeypatch.setattr(cpf.subprocess, "run", fake_run)
        assert cpf._latest_issue_comment_id("42") == "777"
        assert captured[-1] == [
            "gh",
            "issue",
            "view",
            "42",
            "--repo",
            "tomcounsell/ai",
            "--json",
            "comments",
        ]

    def test_argv_unscoped_when_repo_resolution_fails(self, monkeypatch):
        """No GH_REPO and gh repo view fails: degrade to the unscoped argv."""
        captured: list = []

        class IssueResult:
            returncode = 0
            stdout = '{"comments": []}'

        def fake_run(argv, **kwargs):
            captured.append(argv)
            if argv[:2] == ["git", "rev-parse"] or argv[:3] == ["gh", "repo", "view"]:
                return type("FailResult", (), {"returncode": 1, "stdout": ""})()
            return IssueResult()

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        monkeypatch.setattr(cpf.subprocess, "run", fake_run)
        assert cpf._latest_issue_comment_id("42") is None
        assert captured[-1] == ["gh", "issue", "view", "42", "--json", "comments"]

    def test_gh_failure_returns_none(self, monkeypatch):
        """The fail-soft contract: any gh failure reads as None (freshness
        cannot be verified, so the plan is not treated as stale)."""

        def fake_run(argv, **kwargs):
            raise FileNotFoundError("gh missing")

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(cpf.subprocess, "run", fake_run)
        assert cpf._latest_issue_comment_id("42") is None
