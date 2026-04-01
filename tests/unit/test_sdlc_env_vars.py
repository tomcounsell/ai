"""Tests for SDLC environment variable extraction (issue #420).

Tests the _extract_sdlc_env_vars function in sdk_client.py.
"""

from unittest.mock import MagicMock, patch


class TestExtractSdlcEnvVars:
    """Test _extract_sdlc_env_vars in agent/sdk_client.py."""

    def _make_session(self, **kwargs):
        """Create a mock AgentSession with given fields."""
        session = MagicMock(spec=[])
        session.status = kwargs.get("status", "running")
        session.created_at = kwargs.get("created_at", 1000)
        session.pr_url = kwargs.get("pr_url", None)
        session.branch_name = kwargs.get("branch_name", None)
        session.slug = kwargs.get("slug", None)
        session.slug = kwargs.get("slug", None)
        session.plan_url = kwargs.get("plan_url", None)
        session.issue_url = kwargs.get("issue_url", None)
        return session

    @patch("agent.sdk_client.AgentSession", create=True)
    def test_all_fields_populated(self, mock_model):
        """When all session fields are set, all SDLC_ vars are returned."""
        from agent.sdk_client import _extract_sdlc_env_vars

        session = self._make_session(
            pr_url="https://github.com/tomcounsell/ai/pull/220",
            branch_name="session/my-feature",
            slug="my-feature",
            plan_url="https://github.com/tomcounsell/ai/blob/main/docs/plans/my-feature.md",
            issue_url="https://github.com/tomcounsell/ai/issues/415",
        )

        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.filter.return_value = [session]
            result = _extract_sdlc_env_vars("test-session-id", gh_repo="tomcounsell/ai")

        assert result["SDLC_PR_NUMBER"] == "220"
        assert result["SDLC_PR_BRANCH"] == "session/my-feature"
        assert result["SDLC_SLUG"] == "my-feature"
        assert result["SDLC_PLAN_PATH"] == "docs/plans/my-feature.md"
        assert result["SDLC_ISSUE_NUMBER"] == "415"
        assert result["SDLC_REPO"] == "tomcounsell/ai"

    @patch("models.agent_session.AgentSession")
    def test_no_session_found(self, mock_as):
        """When no session exists, return empty dict."""
        from agent.sdk_client import _extract_sdlc_env_vars

        mock_as.query.filter.return_value = []
        result = _extract_sdlc_env_vars("nonexistent-session")
        assert result == {}

    @patch("models.agent_session.AgentSession")
    def test_none_fields_produce_no_env_vars(self, mock_as):
        """When session fields are None, no env vars are set (not 'None' string)."""
        from agent.sdk_client import _extract_sdlc_env_vars

        session = self._make_session()  # all fields default to None
        mock_as.query.filter.return_value = [session]
        result = _extract_sdlc_env_vars("test-session")

        assert "SDLC_PR_NUMBER" not in result
        assert "SDLC_PR_BRANCH" not in result
        assert "SDLC_SLUG" not in result
        assert "SDLC_PLAN_PATH" not in result
        assert "SDLC_ISSUE_NUMBER" not in result
        assert "SDLC_REPO" not in result
        # Ensure no value is the string "None"
        for v in result.values():
            assert v != "None"

    @patch("models.agent_session.AgentSession")
    def test_partial_fields(self, mock_as):
        """Only set env vars for fields that exist."""
        from agent.sdk_client import _extract_sdlc_env_vars

        session = self._make_session(
            pr_url="https://github.com/tomcounsell/ai/pull/42",
            slug="fix-bug",
        )
        mock_as.query.filter.return_value = [session]
        result = _extract_sdlc_env_vars("test-session")

        assert result["SDLC_PR_NUMBER"] == "42"
        assert result["SDLC_SLUG"] == "fix-bug"
        assert "SDLC_PR_BRANCH" not in result
        assert "SDLC_PLAN_PATH" not in result
        assert "SDLC_ISSUE_NUMBER" not in result

    @patch("models.agent_session.AgentSession")
    def test_plan_url_local_path(self, mock_as):
        """Plan URL that's already a local path is passed through."""
        from agent.sdk_client import _extract_sdlc_env_vars

        session = self._make_session(plan_url="docs/plans/my-feature.md")
        mock_as.query.filter.return_value = [session]
        result = _extract_sdlc_env_vars("test-session")
        assert result["SDLC_PLAN_PATH"] == "docs/plans/my-feature.md"

    @patch("models.agent_session.AgentSession")
    def test_redis_failure_returns_empty(self, mock_as):
        """If Redis query fails, return empty dict gracefully."""
        from agent.sdk_client import _extract_sdlc_env_vars

        mock_as.query.filter.side_effect = Exception("Redis connection refused")
        result = _extract_sdlc_env_vars("test-session")
        assert result == {}

    @patch("models.agent_session.AgentSession")
    def test_gh_repo_not_set_no_sdlc_repo(self, mock_as):
        """When gh_repo is None, SDLC_REPO is not set."""
        from agent.sdk_client import _extract_sdlc_env_vars

        session = self._make_session(pr_url="https://github.com/tomcounsell/ai/pull/1")
        mock_as.query.filter.return_value = [session]
        result = _extract_sdlc_env_vars("test-session", gh_repo=None)
        assert "SDLC_REPO" not in result


class TestObserverRemoved:
    """Verify bridge/observer.py no longer exists (SDLC Redesign Phase 2)."""

    def test_observer_module_deleted(self):
        """bridge/observer.py should not exist — Observer replaced by nudge loop."""
        from pathlib import Path

        observer_path = Path(__file__).parent.parent.parent / "bridge" / "observer.py"
        assert not observer_path.exists(), (
            "bridge/observer.py should be deleted — Observer replaced by nudge loop"
        )
