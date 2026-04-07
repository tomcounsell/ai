"""Unit tests for pipeline stage wiring in agent/hooks/pre_tool_use.py.

Tests _extract_stage_from_prompt(), _start_pipeline_stage(), and the
integration of start_stage() into _maybe_register_dev_session() and
_handle_skill_tool_start().
"""

import logging
from unittest.mock import MagicMock, patch

from agent.hooks.pre_tool_use import (
    _SKILL_TO_STAGE,
    _extract_stage_from_prompt,
    _handle_skill_tool_start,
    _maybe_register_dev_session,
    _start_pipeline_stage,
)


class TestExtractStageFromPrompt:
    """Test _extract_stage_from_prompt helper."""

    def test_extracts_stage_colon_format(self):
        assert _extract_stage_from_prompt("Stage: BUILD") == "BUILD"

    def test_extracts_stage_to_execute_dash_format(self):
        assert _extract_stage_from_prompt("Stage to execute -- PLAN") == "PLAN"

    def test_extracts_stage_to_execute_colon(self):
        assert _extract_stage_from_prompt("Stage to execute: TEST") == "TEST"

    def test_extracts_stage_case_insensitive_prefix(self):
        assert _extract_stage_from_prompt("stage: BUILD") == "BUILD"

    def test_extracts_from_longer_prompt(self):
        prompt = (
            "You are a Developer agent.\n\n"
            "Stage: BUILD\n"
            "Issue: https://github.com/example/repo/issues/42\n"
            "Plan: docs/plans/some-plan.md"
        )
        assert _extract_stage_from_prompt(prompt) == "BUILD"

    def test_returns_none_for_empty_prompt(self):
        assert _extract_stage_from_prompt("") is None

    def test_returns_none_for_none_prompt(self):
        assert _extract_stage_from_prompt(None) is None

    def test_returns_none_for_no_stage(self):
        assert _extract_stage_from_prompt("Just do some work please") is None

    def test_returns_none_for_stage_keyword_without_valid_name(self):
        assert _extract_stage_from_prompt("This is a stage of development") is None

    def test_extracts_first_stage_when_multiple_present(self):
        prompt = "Stage: BUILD\nAfter BUILD, run TEST"
        assert _extract_stage_from_prompt(prompt) == "BUILD"

    def test_all_stage_names(self):
        for stage in [
            "ISSUE",
            "PLAN",
            "CRITIQUE",
            "BUILD",
            "TEST",
            "PATCH",
            "REVIEW",
            "DOCS",
            "MERGE",
        ]:
            assert _extract_stage_from_prompt(f"Stage: {stage}") == stage

    def test_fallback_to_keyword_scan(self):
        prompt = "Execute the REVIEW stage for this PR"
        assert _extract_stage_from_prompt(prompt) == "REVIEW"

    def test_fallback_needs_stage_keyword(self):
        assert _extract_stage_from_prompt("Run the BUILD job now") is None


class TestStartPipelineStage:
    """Test _start_pipeline_stage helper."""

    def _make_mocks(self):
        """Create mock AgentSession and PipelineStateMachine modules."""
        mock_session = MagicMock()
        mock_session.stage_states = None
        mock_session.session_id = "parent-1"

        mock_sm_instance = MagicMock()

        mock_psm_module = MagicMock()
        mock_psm_module.PipelineStateMachine.return_value = mock_sm_instance

        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = [mock_session]

        return mock_session, mock_sm_instance, mock_psm_module, mock_as_module

    def test_starts_stage_on_parent_session(self, caplog):
        mock_session, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks()

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
            _start_pipeline_stage("parent-1", "BUILD")

        mock_psm_mod.PipelineStateMachine.assert_called_once_with(mock_session)
        mock_sm.start_stage.assert_called_once_with("BUILD")
        assert "Started pipeline stage BUILD" in caplog.text

    def test_logs_warning_when_parent_not_found(self, caplog):
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
            _start_pipeline_stage("nonexistent", "BUILD")

        assert "Parent session nonexistent not found" in caplog.text

    def test_catches_start_stage_value_error(self, caplog):
        mock_session, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks()
        mock_sm.start_stage.side_effect = ValueError("Cannot start BUILD: no predecessor completed")

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
            _start_pipeline_stage("parent-1", "BUILD")

        assert "Failed to start pipeline stage BUILD" in caplog.text
        assert "no predecessor completed" in caplog.text

    def test_catches_redis_error(self, caplog):
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
            _start_pipeline_stage("parent-4", "BUILD")

        assert "Failed to start pipeline stage BUILD" in caplog.text


class TestMaybeRegisterDevStartStage:
    """Test that _maybe_register_dev_session calls start_stage wiring."""

    def test_calls_start_stage_for_sdlc_prompt(self, monkeypatch, caplog):
        mock_dev = MagicMock()
        mock_dev.agent_session_id = "session-99"
        mock_as_mod = MagicMock()
        mock_as_mod.AgentSession.create_child.return_value = mock_dev

        tool_input = {
            "type": "dev-session",
            "prompt": "Stage: BUILD\nIssue: https://github.com/example/repo/issues/1",
        }

        with (
            patch("agent.hooks.session_registry.resolve", return_value="parent-session-10"),
            patch.dict("sys.modules", {"models.agent_session": mock_as_mod}),
            patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
            caplog.at_level(logging.INFO),
        ):
            _maybe_register_dev_session(tool_input, claude_uuid="test-uuid")

        mock_start.assert_called_once_with("parent-session-10", "BUILD")

    def test_skips_start_stage_when_no_stage_in_prompt(self, monkeypatch, caplog):
        mock_dev = MagicMock()
        mock_dev.agent_session_id = "session-100"
        mock_as_mod = MagicMock()
        mock_as_mod.AgentSession.create_child.return_value = mock_dev

        tool_input = {
            "type": "dev-session",
            "prompt": "Just do some general work",
        }

        with (
            patch("agent.hooks.session_registry.resolve", return_value="parent-session-11"),
            patch.dict("sys.modules", {"models.agent_session": mock_as_mod}),
            patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
            caplog.at_level(logging.DEBUG),
        ):
            _maybe_register_dev_session(tool_input, claude_uuid="test-uuid")

        mock_start.assert_not_called()
        assert "No SDLC stage found" in caplog.text

    def test_start_stage_failure_does_not_raise(self, monkeypatch, caplog):
        """start_stage failure is caught and logged; the hook always returns normally.

        With issue #808, _maybe_register_dev_session/_maybe_start_pipeline_stage no longer
        calls create_child(). It only starts the pipeline stage. A _start_pipeline_stage
        error is wrapped in try/except and logged as a warning — it never propagates.
        """
        tool_input = {
            "type": "dev-session",
            "prompt": "Stage: BUILD\nDo the build",
        }

        with (
            patch("agent.hooks.session_registry.resolve", return_value="parent-session-12"),
            patch(
                "agent.hooks.pre_tool_use._start_pipeline_stage",
                side_effect=RuntimeError("unexpected"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            # Should not raise — start_stage errors are caught in the try/except block
            _maybe_register_dev_session(tool_input, claude_uuid="test-uuid")

        # Warning should have been logged
        assert "Failed to start pipeline stage" in caplog.text

    def test_skips_entirely_for_non_dev_session(self, monkeypatch):
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session-13")

        tool_input = {
            "type": "chat-session",
            "prompt": "Stage: BUILD",
        }

        with patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start:
            _maybe_register_dev_session(tool_input)

        mock_start.assert_not_called()

    def test_skips_when_no_valor_session_id(self, monkeypatch):
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        tool_input = {
            "type": "dev-session",
            "prompt": "Stage: BUILD",
        }

        with patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start:
            _maybe_register_dev_session(tool_input)

        mock_start.assert_not_called()


class TestSkillToolStartStage:
    """Test _handle_skill_tool_start: maps Skill tool calls to pipeline stage starts."""

    def test_known_skill_triggers_start_stage(self):
        """A known SDLC skill calls _start_pipeline_stage with the mapped stage."""
        tool_input = {"skill": "do-build"}

        with (
            patch("agent.hooks.session_registry.resolve", return_value="session-abc"),
            patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
        ):
            _handle_skill_tool_start(tool_input, claude_uuid="uuid-1")

        mock_start.assert_called_once_with("session-abc", "BUILD")

    def test_all_mapped_skills_trigger_correct_stage(self):
        """Every entry in _SKILL_TO_STAGE maps to the correct stage."""
        for skill_name, expected_stage in _SKILL_TO_STAGE.items():
            with (
                patch("agent.hooks.session_registry.resolve", return_value="session-xyz"),
                patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
            ):
                _handle_skill_tool_start({"skill": skill_name}, claude_uuid="uuid-2")
            mock_start.assert_called_once_with("session-xyz", expected_stage)

    def test_unknown_skill_name_is_ignored(self):
        """A skill not in _SKILL_TO_STAGE silently no-ops."""
        tool_input = {"skill": "do-discover-paths"}

        with (
            patch("agent.hooks.session_registry.resolve", return_value="session-def"),
            patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
        ):
            _handle_skill_tool_start(tool_input, claude_uuid="uuid-3")

        mock_start.assert_not_called()

    def test_missing_skill_key_is_ignored(self, caplog):
        """Empty skill name silently no-ops."""
        tool_input = {}

        with (
            patch("agent.hooks.session_registry.resolve", return_value="session-ghi"),
            patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
            caplog.at_level(logging.DEBUG),
        ):
            _handle_skill_tool_start(tool_input, claude_uuid="uuid-4")

        mock_start.assert_not_called()
        assert "empty skill name" in caplog.text

    def test_no_session_id_skips_gracefully(self, caplog):
        """When session registry returns None, _start_pipeline_stage is not called."""
        tool_input = {"skill": "do-build"}

        with (
            patch("agent.hooks.session_registry.resolve", return_value=None),
            patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
            caplog.at_level(logging.DEBUG),
        ):
            _handle_skill_tool_start(tool_input, claude_uuid="uuid-5")

        mock_start.assert_not_called()
        assert "No session ID resolved" in caplog.text

    def test_skill_to_stage_mapping_is_complete(self):
        """Verify all expected SDLC skills are present in _SKILL_TO_STAGE."""
        expected_skills = {
            "do-plan",
            "do-plan-critique",
            "do-build",
            "do-test",
            "do-patch",
            "do-pr-review",
            "do-docs",
            "do-merge",
        }
        assert expected_skills == set(_SKILL_TO_STAGE.keys())
