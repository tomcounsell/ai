"""Unit tests for agent/hooks/subagent_stop.py hook."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from agent.hooks.subagent_stop import (
    _extract_outcome_summary,
    _get_stage_states,
    _post_stage_comment_on_completion,
    _register_dev_session_completion,
    _resolve_tracking_issue,
    subagent_stop_hook,
)


class TestSubagentStopHookBasic:
    """Test basic logging behavior for any subagent completion."""

    @pytest.mark.asyncio
    async def test_logs_subagent_completion(self, caplog):
        """Hook should log when any subagent completes."""
        input_data = {"agent_type": "some-agent", "agent_id": "abc-123"}
        with caplog.at_level(logging.INFO):
            result = await subagent_stop_hook(input_data, None, None)
        assert result == {}
        assert "Subagent completed" in caplog.text
        assert "some-agent" in caplog.text
        assert "abc-123" in caplog.text

    @pytest.mark.asyncio
    async def test_unknown_agent_type_defaults(self, caplog):
        """Hook should handle missing agent_type and agent_id gracefully."""
        input_data = {}
        with caplog.at_level(logging.INFO):
            result = await subagent_stop_hook(input_data, None, None)
        assert result == {}
        assert "unknown" in caplog.text

    @pytest.mark.asyncio
    async def test_non_dev_session_returns_empty(self):
        """Non dev-session agent types should return empty dict without registration."""
        input_data = {"agent_type": "chat-session", "agent_id": "xyz"}
        result = await subagent_stop_hook(input_data, None, None)
        assert result == {}


class TestRegisterDevSessionCompletion:
    """Test _register_dev_session_completion helper."""

    def test_skips_when_no_session_id(self, monkeypatch, caplog):
        """Should skip gracefully when VALOR_SESSION_ID is not set."""
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        with caplog.at_level(logging.DEBUG):
            _register_dev_session_completion("agent-1")
        assert "skipping DevSession completion" in caplog.text

    def test_marks_dev_session_completed(self, monkeypatch):
        """Should mark pending dev sessions as completed."""
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session-1")

        mock_dev = MagicMock()
        mock_dev.status = "running"
        mock_dev.job_id = "job-42"

        mock_query = MagicMock()
        mock_query.filter.return_value = [mock_dev]

        with patch("agent.hooks.subagent_stop.AgentSession", create=True) as mock_as:
            # Patch the import inside the function
            with patch.dict(
                "sys.modules", {"models.agent_session": MagicMock(AgentSession=mock_as)}
            ):
                mock_as.query = mock_query
                _register_dev_session_completion("agent-1")

        assert mock_dev.status == "completed"
        mock_dev.save.assert_called_once()

    def test_skips_already_completed_sessions(self, monkeypatch):
        """Should not overwrite sessions that are already completed or failed."""
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session-2")

        mock_dev_completed = MagicMock()
        mock_dev_completed.status = "completed"

        mock_dev_failed = MagicMock()
        mock_dev_failed.status = "failed"

        mock_query = MagicMock()
        mock_query.filter.return_value = [mock_dev_completed, mock_dev_failed]

        with patch("agent.hooks.subagent_stop.AgentSession", create=True) as mock_as:
            with patch.dict(
                "sys.modules", {"models.agent_session": MagicMock(AgentSession=mock_as)}
            ):
                mock_as.query = mock_query
                _register_dev_session_completion("agent-1")

        # Neither should have save called since both are terminal
        mock_dev_completed.save.assert_not_called()
        mock_dev_failed.save.assert_not_called()

    def test_handles_import_error(self, monkeypatch, caplog):
        """Should log warning if AgentSession import fails."""
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session-3")

        with patch.dict("sys.modules", {"models.agent_session": None}):
            with caplog.at_level(logging.WARNING):
                _register_dev_session_completion("agent-1")
        assert "Failed to register DevSession completion" in caplog.text

    def test_handles_query_error(self, monkeypatch, caplog):
        """Should log warning if Redis query raises."""
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session-4")

        mock_module = MagicMock()
        mock_module.AgentSession.query.filter.side_effect = RuntimeError("Redis down")

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            with caplog.at_level(logging.WARNING):
                _register_dev_session_completion("agent-1")
        assert "Failed to register DevSession completion" in caplog.text


class TestGetSdlcStages:
    """Test _get_stage_states helper."""

    def test_returns_stage_data_as_string(self):
        """Should return parsed stage data when available."""
        stage_data = {"PLAN": "done", "BUILD": "pending"}
        mock_session = MagicMock()
        mock_session.stage_states = json.dumps(stage_data)

        mock_module = MagicMock()
        mock_module.AgentSession.query.filter.return_value = [mock_session]

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            result = _get_stage_states("session-1")

        assert result is not None
        assert "PLAN" in result
        assert "done" in result

    def test_returns_none_when_no_sessions(self):
        """Should return None when no sessions found."""
        mock_module = MagicMock()
        mock_module.AgentSession.query.filter.return_value = []

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            result = _get_stage_states("nonexistent")

        assert result is None

    def test_returns_none_when_no_stage_data(self):
        """Should return None when session has no stage data."""
        mock_session = MagicMock()
        mock_session.stage_states = None

        mock_module = MagicMock()
        mock_module.AgentSession.query.filter.return_value = [mock_session]

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            result = _get_stage_states("session-1")

        assert result is None

    def test_handles_dict_input(self):
        """Should handle stage data that is already a dict (not JSON string)."""
        stage_data = {"TEST": "passed"}
        mock_session = MagicMock()
        mock_session.stage_states = stage_data

        mock_module = MagicMock()
        mock_module.AgentSession.query.filter.return_value = [mock_session]

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            result = _get_stage_states("session-1")

        assert result is not None
        assert "TEST" in result

    def test_handles_import_error(self):
        """Should return None on import error."""
        with patch.dict("sys.modules", {"models.agent_session": None}):
            result = _get_stage_states("session-1")
        assert result is None

    def test_handles_query_error(self):
        """Should return None on query error."""
        mock_module = MagicMock()
        mock_module.AgentSession.query.filter.side_effect = RuntimeError("Redis down")

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            result = _get_stage_states("session-1")

        assert result is None


class TestExtractOutcomeSummary:
    """Tests for _extract_outcome_summary."""

    def test_extracts_result_field(self):
        result = _extract_outcome_summary({"result": "Build completed successfully"})
        assert "Build completed" in result

    def test_extracts_output_field(self):
        result = _extract_outcome_summary({"output": "Tests passed"})
        assert "Tests passed" in result

    def test_extracts_from_nested_result(self):
        result = _extract_outcome_summary({"result": {"text": "PR created at https://..."}})
        assert "PR created" in result

    def test_returns_default_when_empty(self):
        result = _extract_outcome_summary({})
        assert "completed" in result

    def test_truncates_long_output(self):
        long_text = "x" * 500
        result = _extract_outcome_summary({"result": long_text})
        assert len(result) <= 200

    def test_handles_non_string_result(self):
        result = _extract_outcome_summary({"result": 42})
        assert "completed" in result

    def test_skips_none_values(self):
        result = _extract_outcome_summary({"result": None, "output": None})
        assert "completed" in result


class TestResolveTrackingIssue:
    """Test _resolve_tracking_issue helper."""

    def test_from_sdlc_tracking_issue_env(self, monkeypatch):
        monkeypatch.setenv("SDLC_TRACKING_ISSUE", "42")
        assert _resolve_tracking_issue() == 42

    def test_from_sdlc_issue_number_env(self, monkeypatch):
        monkeypatch.delenv("SDLC_TRACKING_ISSUE", raising=False)
        monkeypatch.setenv("SDLC_ISSUE_NUMBER", "99")
        assert _resolve_tracking_issue() == 99

    def test_returns_none_when_no_env_vars(self, monkeypatch):
        monkeypatch.delenv("SDLC_TRACKING_ISSUE", raising=False)
        monkeypatch.delenv("SDLC_ISSUE_NUMBER", raising=False)
        monkeypatch.delenv("SDLC_SLUG", raising=False)
        assert _resolve_tracking_issue() is None

    def test_ignores_non_digit_values(self, monkeypatch):
        monkeypatch.setenv("SDLC_TRACKING_ISSUE", "not-a-number")
        monkeypatch.delenv("SDLC_ISSUE_NUMBER", raising=False)
        monkeypatch.delenv("SDLC_SLUG", raising=False)
        assert _resolve_tracking_issue() is None

    def test_from_plan_frontmatter(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SDLC_TRACKING_ISSUE", raising=False)
        monkeypatch.delenv("SDLC_ISSUE_NUMBER", raising=False)
        monkeypatch.setenv("SDLC_SLUG", "test-slug")

        plan_file = tmp_path / "test-slug.md"
        plan_file.write_text("---\ntracking: https://github.com/owner/repo/issues/123\n---\n")
        monkeypatch.setenv("SDLC_PLAN_PATH", str(plan_file))

        assert _resolve_tracking_issue() == 123


class TestPostStageCommentOnCompletion:
    """Test _post_stage_comment_on_completion helper."""

    def test_skips_when_no_tracking_issue(self, monkeypatch, caplog):
        monkeypatch.delenv("SDLC_TRACKING_ISSUE", raising=False)
        monkeypatch.delenv("SDLC_ISSUE_NUMBER", raising=False)
        monkeypatch.delenv("SDLC_SLUG", raising=False)
        with caplog.at_level(logging.DEBUG):
            _post_stage_comment_on_completion({}, "BUILD")
        assert "No tracking issue" in caplog.text

    def test_posts_comment_on_success(self, monkeypatch):
        monkeypatch.setenv("SDLC_TRACKING_ISSUE", "42")
        with patch("utils.issue_comments.post_stage_comment", return_value=True) as mock_post:
            _post_stage_comment_on_completion({"result": "Tests passed"}, "TEST")
        mock_post.assert_called_once()
        assert mock_post.call_args.kwargs["issue_number"] == 42
        assert mock_post.call_args.kwargs["stage"] == "TEST"

    def test_handles_post_failure(self, monkeypatch, caplog):
        monkeypatch.setenv("SDLC_TRACKING_ISSUE", "42")
        with patch("utils.issue_comments.post_stage_comment", return_value=False):
            with caplog.at_level(logging.WARNING):
                _post_stage_comment_on_completion({}, "BUILD")
        assert "Failed to post stage comment" in caplog.text

    def test_never_crashes_on_exception(self, monkeypatch, caplog):
        monkeypatch.setenv("SDLC_TRACKING_ISSUE", "42")
        with patch(
            "utils.issue_comments.post_stage_comment",
            side_effect=RuntimeError("unexpected"),
        ):
            with caplog.at_level(logging.WARNING):
                _post_stage_comment_on_completion({}, "BUILD")
        assert "non-fatal" in caplog.text


class TestSubagentStopHookDevSession:
    """Test full hook behavior for dev-session agent type."""

    @pytest.mark.asyncio
    async def test_injects_stage_states_for_dev_session(self, monkeypatch):
        """Should inject pipeline state into reason field for dev-sessions."""
        monkeypatch.setenv("VALOR_SESSION_ID", "pm-session-1")

        with (
            patch("agent.hooks.subagent_stop._register_dev_session_completion") as mock_reg,
            patch(
                "agent.hooks.subagent_stop._get_stage_states",
                return_value="{'PLAN': 'done', 'BUILD': 'done'}",
            ),
            patch("agent.hooks.subagent_stop._post_stage_comment_on_completion"),
        ):
            input_data = {"agent_type": "dev-session", "agent_id": "dev-1"}
            result = await subagent_stop_hook(input_data, None, None)

        mock_reg.assert_called_once_with("dev-1")
        assert "reason" in result
        assert "Pipeline state" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_injection_when_session_id_not_set(self, monkeypatch):
        """Should not inject stages when VALOR_SESSION_ID is not set."""
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        with (
            patch("agent.hooks.subagent_stop._register_dev_session_completion"),
            patch("agent.hooks.subagent_stop._post_stage_comment_on_completion"),
        ):
            input_data = {"agent_type": "dev-session", "agent_id": "dev-2"}
            result = await subagent_stop_hook(input_data, None, None)

        assert result == {}

    @pytest.mark.asyncio
    async def test_no_injection_when_no_stage_data(self, monkeypatch):
        """Should return empty dict when no SDLC stage data exists."""
        monkeypatch.setenv("VALOR_SESSION_ID", "pm-session-2")

        with (
            patch("agent.hooks.subagent_stop._register_dev_session_completion"),
            patch("agent.hooks.subagent_stop._get_stage_states", return_value=None),
            patch("agent.hooks.subagent_stop._post_stage_comment_on_completion"),
        ):
            input_data = {"agent_type": "dev-session", "agent_id": "dev-3"}
            result = await subagent_stop_hook(input_data, None, None)

        assert result == {}

    @pytest.mark.asyncio
    async def test_posts_stage_comment_on_completion(self, monkeypatch):
        """Should call _post_stage_comment_on_completion for dev-sessions."""
        monkeypatch.setenv("VALOR_SESSION_ID", "pm-session-3")

        with (
            patch("agent.hooks.subagent_stop._register_dev_session_completion"),
            patch("agent.hooks.subagent_stop._get_stage_states", return_value=None),
            patch("agent.hooks.subagent_stop._post_stage_comment_on_completion") as mock_post,
        ):
            input_data = {
                "agent_type": "dev-session",
                "agent_id": "dev-4",
                "result": "Build complete",
            }
            await subagent_stop_hook(input_data, None, None)

        mock_post.assert_called_once()
