"""Tests for persona-aware scheduling restrictions in agent_session_scheduler.

Validates:
1. Teammate persona is blocked from schedule operations
2. Developer persona can schedule
3. Project-manager persona can schedule
4. Default persona (unset) is permissive (developer)
5. Non-SDLC operations (status, push, bump, pop, cancel) are unrestricted for all
"""

from unittest.mock import patch

from tools.agent_session_scheduler import _check_persona_permission


class TestPersonaGate:
    """Tests for _check_persona_permission."""

    def test_developer_can_schedule(self):
        with patch.dict("os.environ", {"PERSONA": "developer"}):
            result = _check_persona_permission("schedule")
            assert result is None

    def test_project_manager_can_schedule(self):
        with patch.dict("os.environ", {"PERSONA": "project-manager"}):
            result = _check_persona_permission("schedule")
            assert result is None

    def test_teammate_blocked_from_schedule(self):
        with patch.dict("os.environ", {"PERSONA": "teammate"}):
            result = _check_persona_permission("schedule")
            assert result is not None
            assert result["status"] == "error"
            assert "Permission denied" in result["message"]
            assert result["persona"] == "teammate"
            assert result["action"] == "schedule"

    def test_teammate_can_view_status(self):
        """Status and other read operations are unrestricted."""
        with patch.dict("os.environ", {"PERSONA": "teammate"}):
            result = _check_persona_permission("status")
            assert result is None

    def test_teammate_can_push(self):
        with patch.dict("os.environ", {"PERSONA": "teammate"}):
            result = _check_persona_permission("push")
            assert result is None

    def test_default_persona_is_permissive(self):
        """When PERSONA env var is not set, default to developer (permissive)."""
        with patch.dict("os.environ", {}, clear=True):
            result = _check_persona_permission("schedule")
            assert result is None

    def test_persona_case_insensitive(self):
        """Persona check should be case-insensitive."""
        with patch.dict("os.environ", {"PERSONA": "Teammate"}):
            result = _check_persona_permission("schedule")
            assert result is not None
            assert result["status"] == "error"


class TestValidateIssueRepoScoping:
    """Issue #2889: `_validate_issue` must scope `gh issue view` with --repo.

    `_validate_issue` gates whether an autonomous session is scheduled on an
    issue, so a wrong-repo lookup (bare ``gh issue view`` under a foreign
    GH_REPO, which answers about the wrong repository and exits 0) could
    schedule unattended work against the wrong repository. The argv must
    carry ``--repo <resolved-slug>`` when a repo resolves; the fail-soft
    contract (returns None on any failure) is preserved.
    """

    def test_argv_scoped_from_gh_repo_env(self, monkeypatch):
        import tools.agent_session_scheduler as sched

        captured: dict = {}

        class FakeResult:
            returncode = 0
            stdout = (
                '{"title": "T", "state": "open", "body": "B", '
                '"url": "https://github.com/tomcounsell/ai/issues/7"}'
            )

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return FakeResult()

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(sched.subprocess, "run", fake_run)
        result = sched._validate_issue(7)
        assert result is not None
        assert result["state"] == "open"
        assert captured["argv"] == [
            "gh",
            "issue",
            "view",
            "7",
            "--repo",
            "tomcounsell/ai",
            "--json",
            "title,state,body,url",
        ]

    def test_fail_soft_on_gh_failure(self, monkeypatch):
        """Any gh failure returns None -- the scheduler gate fails open."""
        import tools.agent_session_scheduler as sched

        def fake_run(argv, **kwargs):
            raise OSError("gh missing")

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(sched.subprocess, "run", fake_run)
        assert sched._validate_issue(7) is None
