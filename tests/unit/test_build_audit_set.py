"""Unit tests for scripts/build_audit_set.py.

Issue #2889/#2921: the `gh issue view` readers in `get_issue_pr` and
`get_issue_body` must be scoped with --repo. A bare ``gh issue view N``
resolves GH_REPO from the environment before cwd, so under a foreign GH_REPO
it answers about a *different* repository's issue #N and exits 0.
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
