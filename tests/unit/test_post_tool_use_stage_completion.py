"""Unit tests for Skill tool stage completion in agent/hooks/post_tool_use.py.

Tests _complete_pipeline_stage() and the integration of stage completion logic
into the post_tool_use_hook dispatcher.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.hooks.post_tool_use import _complete_pipeline_stage


class TestCompletePipelineStage:
    """Test _complete_pipeline_stage helper."""

    def _make_mocks(self, current_stage: str | None = "BUILD"):
        """Create mock AgentSession and PipelineStateMachine modules."""
        mock_session = MagicMock()
        mock_session.session_id = "parent-session-1"

        mock_sm_instance = MagicMock()
        mock_sm_instance.current_stage.return_value = current_stage

        mock_psm_module = MagicMock()
        mock_psm_module.PipelineStateMachine.return_value = mock_sm_instance

        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = [mock_session]

        return mock_session, mock_sm_instance, mock_psm_module, mock_as_module

    def test_completes_in_progress_stage(self, caplog):
        """When a stage is in_progress, complete_stage() is called with that stage."""
        _, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks(current_stage="BUILD")

        with (
            patch.dict(
                "sys.modules",
                {
                    "bridge.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.INFO),
        ):
            _complete_pipeline_stage("parent-session-1")

        mock_sm.current_stage.assert_called_once()
        mock_sm.complete_stage.assert_called_once_with("BUILD")
        assert "Completed pipeline stage BUILD" in caplog.text

    def test_skips_when_no_in_progress_stage(self, caplog):
        """When no stage is in_progress, complete_stage() is not called."""
        _, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks(current_stage=None)

        with (
            patch.dict(
                "sys.modules",
                {
                    "bridge.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.DEBUG),
        ):
            _complete_pipeline_stage("parent-session-2")

        mock_sm.complete_stage.assert_not_called()
        assert "No in_progress stage" in caplog.text

    def test_logs_warning_when_session_not_found(self, caplog):
        """When session is not in Redis, logs a warning and does not crash."""
        mock_as_mod = MagicMock()
        mock_as_mod.AgentSession.query.filter.return_value = []
        mock_psm_mod = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "bridge.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.WARNING),
        ):
            _complete_pipeline_stage("nonexistent-session")

        mock_psm_mod.PipelineStateMachine.assert_not_called()
        assert "nonexistent-session" in caplog.text
        assert "not found" in caplog.text

    def test_swallows_complete_stage_exception(self, caplog):
        """If complete_stage() raises, the exception is caught and logged."""
        _, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks(current_stage="TEST")
        mock_sm.complete_stage.side_effect = RuntimeError("state machine error")

        with (
            patch.dict(
                "sys.modules",
                {
                    "bridge.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.WARNING),
        ):
            # Must not raise
            _complete_pipeline_stage("parent-session-3")

        assert "Failed to complete pipeline stage" in caplog.text
        assert "state machine error" in caplog.text

    def test_swallows_redis_error(self, caplog):
        """If Redis lookup raises, the exception is caught and logged."""
        mock_as_mod = MagicMock()
        mock_as_mod.AgentSession.query.filter.side_effect = RuntimeError("Redis down")
        mock_psm_mod = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "bridge.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.WARNING),
        ):
            _complete_pipeline_stage("parent-session-4")

        assert "Failed to complete pipeline stage" in caplog.text


class TestPostToolUseHookSkillCompletion:
    """Test that post_tool_use_hook calls _complete_pipeline_stage on Skill tool completions."""

    @pytest.mark.asyncio
    async def test_skill_tool_triggers_complete_stage(self):
        """A known SDLC Skill tool call invokes _complete_pipeline_stage."""
        mock_watchdog = AsyncMock(return_value={})
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "do-build"},
            "session_id": "uuid-abc",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.session_registry.resolve", return_value="bridge-session-1"),
            patch("agent.hooks.post_tool_use._complete_pipeline_stage") as mock_complete,
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-1", context=MagicMock())

        mock_complete.assert_called_once_with("bridge-session-1")

    @pytest.mark.asyncio
    async def test_unknown_skill_does_not_trigger_complete_stage(self):
        """A non-SDLC skill (e.g., do-discover-paths) does not call _complete_pipeline_stage."""
        mock_watchdog = AsyncMock(return_value={})
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "do-discover-paths"},
            "session_id": "uuid-def",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.session_registry.resolve", return_value="bridge-session-2"),
            patch("agent.hooks.post_tool_use._complete_pipeline_stage") as mock_complete,
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-2", context=MagicMock())

        mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_skill_tool_does_not_trigger_complete_stage(self):
        """Non-Skill tools (e.g., Bash) do not call _complete_pipeline_stage."""
        mock_watchdog = AsyncMock(return_value={})
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "session_id": "uuid-ghi",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.post_tool_use._complete_pipeline_stage") as mock_complete,
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-3", context=MagicMock())

        mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_session_skips_complete_stage(self):
        """When session registry returns None, _complete_pipeline_stage is not called."""
        mock_watchdog = AsyncMock(return_value={})
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "do-plan"},
            "session_id": "uuid-jkl",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.session_registry.resolve", return_value=None),
            patch("agent.hooks.post_tool_use._complete_pipeline_stage") as mock_complete,
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-4", context=MagicMock())

        mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_stage_exception_does_not_propagate(self):
        """Exceptions from _complete_pipeline_stage are swallowed."""
        mock_watchdog = AsyncMock(return_value={})
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "do-build"},
            "session_id": "uuid-mno",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.session_registry.resolve", return_value="bridge-session-5"),
            patch(
                "agent.hooks.post_tool_use._complete_pipeline_stage",
                side_effect=RuntimeError("unexpected failure"),
            ),
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            # Must not raise
            result = await post_tool_use_hook(input_data, tool_use_id="tu-5", context=MagicMock())

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_watchdog_always_runs(self):
        """Watchdog hook is always called, regardless of tool type."""
        mock_watchdog = AsyncMock(return_value={})
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "foo.py"},
            "session_id": "uuid-pqr",
        }

        with patch("agent.health_check.watchdog_hook", mock_watchdog):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-6", context=MagicMock())

        mock_watchdog.assert_called_once()
