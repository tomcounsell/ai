"""Unit tests for scripts/build_audit_set.py.

Issue #2889/#2921/#2928: the `gh issue view` readers in `get_issue_pr` and
`get_issue_body`, plus the `gh pr list` fallback in `get_issue_pr` and the
`gh pr diff` reader in `fetch_pr_diff`, must be scoped with --repo. A bare
`gh ... N` resolves GH_REPO from the environment before cwd, so under a
foreign GH_REPO it answers about a *different* repository and exits 0.
"""

import scripts.build_audit_set as bas


class TestIssueViewRepoScoping:
    """Issue #2889: the shell argv must carry ``--repo <resolved-slug>``.

    The slug resolves via the shared ladder (GH_REPO env first, else
    ``gh repo view --json nameWithOwner`` from the working-tree root /
    ``SDLC_TARGET_REPO``); when nothing resolves, the argv degrades to the
    prior unscoped shape and the existing failure contract is preserved
    (``get_issue_pr`` returns ``(None, None)`` on failure, ``get_issue_body``
    returns ``None``).
    """

    def test_get_issue_pr_scoped_from_gh_repo_env(self, monkeypatch):
        captured: list = []

        class FakeResult:
            returncode = 0
            stdout = '{"title": "T", "closedByPullRequestsReferences": [{"number": 9}]}'

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return FakeResult()

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        assert bas.get_issue_pr(42) == (9, "T")
        assert "--repo tomcounsell/ai" in captured[0]
        assert captured[0].startswith("gh issue view 42")

    def test_get_issue_pr_unscoped_when_repo_resolution_fails(self, monkeypatch):
        """No GH_REPO and git root resolution fails: degrade to unscoped argv."""
        captured: list = []

        class FakeResult:
            returncode = 0
            stdout = '{"title": "T", "closedByPullRequestsReferences": [{"number": 9}]}'

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            if "rev-parse" in cmd:
                return type("FailResult", (), {"returncode": 1, "stdout": ""})()
            return FakeResult()

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        assert bas.get_issue_pr(42) == (9, "T")
        assert "--repo" not in captured[-1]
        assert captured[-1].startswith("gh issue view 42")

    def test_get_issue_body_scoped_from_gh_repo_env(self, monkeypatch):
        captured: list = []

        class FakeResult:
            returncode = 0
            stdout = '{"body": "hello"}'

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return FakeResult()

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        assert bas.get_issue_body(42) == "hello"
        assert "--repo tomcounsell/ai" in captured[0]
        assert captured[0].startswith("gh issue view 42")

    def test_get_issue_body_unscoped_when_repo_resolution_fails(self, monkeypatch):
        captured: list = []

        class FakeResult:
            returncode = 0
            stdout = '{"body": "hello"}'

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            if "rev-parse" in cmd:
                return type("FailResult", (), {"returncode": 1, "stdout": ""})()
            return FakeResult()

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        assert bas.get_issue_body(42) == "hello"
        assert "--repo" not in captured[-1]
        assert captured[-1].startswith("gh issue view 42")


class TestPrListFallbackRepoScoping:
    """Issue #2928: the ``gh pr list --search`` fallback inside ``get_issue_pr``.

    Same wrong-repository bug class as #2889: a bare ``gh pr list`` under a
    foreign GH_REPO (or from a wrong cwd) searches a *different* repository
    and exits 0, so the fallback could resolve PR #N from the wrong repo. The
    argv must carry ``--repo <resolved-slug>`` in the scoped case and degrade
    to the prior unscoped shape when nothing resolves.
    """

    def test_pr_list_fallback_scoped_from_gh_repo_env(self, monkeypatch):
        captured: list = []

        class FakeResult:
            returncode = 0
            stdout = '{"title": "T", "closedByPullRequestsReferences": []}'

        class FakeListResult:
            returncode = 0
            stdout = '[{"number": 9, "title": "T"}]'

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            if "gh pr list" in cmd:
                return FakeListResult()
            return FakeResult()

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        assert bas.get_issue_pr(42) == (9, "T")
        assert "--repo tomcounsell/ai" in captured[-1]
        assert captured[-1].startswith("gh pr list --repo tomcounsell/ai")

    def test_pr_list_fallback_unscoped_when_repo_resolution_fails(self, monkeypatch):
        """No GH_REPO and git root resolution fails: degrade to unscoped argv."""
        captured: list = []

        class FakeResult:
            returncode = 0
            stdout = '{"title": "T", "closedByPullRequestsReferences": []}'

        class FakeListResult:
            returncode = 0
            stdout = '[{"number": 9, "title": "T"}]'

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            if "rev-parse" in cmd:
                return type("FailResult", (), {"returncode": 1, "stdout": ""})()
            if "gh pr list" in cmd:
                return FakeListResult()
            return FakeResult()

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        assert bas.get_issue_pr(42) == (9, "T")
        assert "--repo" not in captured[-1]
        assert captured[-1].startswith("gh pr list --search")


class TestPrDiffRepoScoping:
    """Issue #2928: the ``gh pr diff`` reader in ``fetch_pr_diff``.

    Same wrong-repository bug class as #2889: a bare ``gh pr diff`` under a
    foreign GH_REPO (or from a wrong cwd) diffs a *different* repository's PR
    #N and exits 0. The argv must carry ``--repo <resolved-slug>`` in the
    scoped case and degrade to the prior unscoped shape when nothing resolves;
    the existing failure value (``None``) is preserved in both cases.
    """

    def test_pr_diff_scoped_from_gh_repo_env(self, monkeypatch, tmp_path):
        captured: list = []

        class FakeResult:
            returncode = 0
            stdout = "diff --git a/x b/x"

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return FakeResult()

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        # DIFFS_DIR must sit under WORKTREE so the relative return path resolves.
        monkeypatch.setattr(bas, "WORKTREE", tmp_path.parent)
        monkeypatch.setattr(bas, "DIFFS_DIR", tmp_path)
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        result = bas.fetch_pr_diff(9, "some-slug")
        assert "--repo tomcounsell/ai" in captured[0]
        assert captured[0].startswith("gh pr diff --repo tomcounsell/ai")
        assert result is not None

    def test_pr_diff_unscoped_when_repo_resolution_fails(self, monkeypatch, tmp_path):
        captured: list = []

        class FakeResult:
            returncode = 0
            stdout = "diff --git a/x b/x"

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            if "rev-parse" in cmd:
                return type("FailResult", (), {"returncode": 1, "stdout": ""})()
            return FakeResult()

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        monkeypatch.setattr(bas, "WORKTREE", tmp_path.parent)
        monkeypatch.setattr(bas, "DIFFS_DIR", tmp_path)
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        result = bas.fetch_pr_diff(9, "some-slug")
        assert "--repo" not in captured[-1]
        assert captured[-1].startswith("gh pr diff 9")
        assert result is not None

    def test_pr_diff_failure_returns_none(self, monkeypatch, tmp_path):
        """Preserve the existing failure value: non-zero rc → None."""
        captured: list = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return type("FailResult", (), {"returncode": 1, "stdout": ""})()

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(bas, "WORKTREE", tmp_path.parent)
        monkeypatch.setattr(bas, "DIFFS_DIR", tmp_path)
        monkeypatch.setattr(bas.subprocess, "run", fake_run)
        assert bas.fetch_pr_diff(9, "some-slug") is None
        assert "--repo tomcounsell/ai" in captured[0]
