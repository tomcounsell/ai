"""Tests for skill invocation tracking and session-scoped skill registry.

Validates that:
- record_metric is called when _handle_skill_tool_start processes any skill
- _SESSION_SKILLS registry is populated with skill name on invocation
- Post-session extraction cleans up _SESSION_SKILLS and emits outcome metrics
- Unknown skills (not in _SKILL_TO_STAGE) still get tracked in analytics
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_session_skills():
    """Clear the session skills registry before and after each test."""
    from agent.hooks.pre_tool_use import _SESSION_SKILLS

    _SESSION_SKILLS.clear()
    yield
    _SESSION_SKILLS.clear()


class TestSkillInvocationTracking:
    """Test that skill invocations are recorded via record_metric."""

    @patch("agent.hooks.pre_tool_use.record_metric")
    @patch("agent.hooks.session_registry.resolve", return_value="test-session-123")
    def test_known_skill_records_metric(self, mock_resolve, mock_record):
        """A known skill (in _SKILL_TO_STAGE) records a metric."""
        from agent.hooks.pre_tool_use import _handle_skill_tool_start

        with patch("agent.hooks.pre_tool_use._start_pipeline_stage"):
            _handle_skill_tool_start({"skill": "do-build"}, "claude-uuid-1")

        mock_record.assert_called_once_with(
            "skill.invocation",
            1,
            {"skill": "do-build", "session_id": "test-session-123"},
        )

    @patch("agent.hooks.pre_tool_use.record_metric")
    @patch("agent.hooks.session_registry.resolve", return_value="test-session-456")
    def test_unknown_skill_records_metric(self, mock_resolve, mock_record):
        """An unknown skill (NOT in _SKILL_TO_STAGE) still records a metric."""
        from agent.hooks.pre_tool_use import _handle_skill_tool_start

        _handle_skill_tool_start({"skill": "do-discover-paths"}, "claude-uuid-2")

        mock_record.assert_called_once_with(
            "skill.invocation",
            1,
            {"skill": "do-discover-paths", "session_id": "test-session-456"},
        )

    @patch("agent.hooks.pre_tool_use.record_metric")
    def test_empty_skill_name_skips_tracking(self, mock_record):
        """Empty skill name returns early without recording."""
        from agent.hooks.pre_tool_use import _handle_skill_tool_start

        _handle_skill_tool_start({"skill": ""}, "claude-uuid-3")
        mock_record.assert_not_called()

    @patch("agent.hooks.pre_tool_use.record_metric")
    @patch("agent.hooks.session_registry.resolve", return_value=None)
    def test_no_session_id_skips_tracking(self, mock_resolve, mock_record):
        """No resolved session ID skips metric recording."""
        from agent.hooks.pre_tool_use import _handle_skill_tool_start

        _handle_skill_tool_start({"skill": "do-build"}, "claude-uuid-4")
        mock_record.assert_not_called()


class TestSessionSkillsRegistry:
    """Test that _SESSION_SKILLS registry tracks skills per session."""

    @patch("agent.hooks.pre_tool_use.record_metric")
    @patch("agent.hooks.session_registry.resolve", return_value="session-A")
    def test_skill_appended_to_registry(self, mock_resolve, mock_record):
        """Skill name is appended to _SESSION_SKILLS for the session."""
        from agent.hooks.pre_tool_use import _SESSION_SKILLS, _handle_skill_tool_start

        with patch("agent.hooks.pre_tool_use._start_pipeline_stage"):
            _handle_skill_tool_start({"skill": "do-build"}, "uuid-1")

        assert _SESSION_SKILLS == {"session-A": ["do-build"]}

    @patch("agent.hooks.pre_tool_use.record_metric")
    @patch("agent.hooks.session_registry.resolve", return_value="session-A")
    def test_multiple_skills_appended(self, mock_resolve, mock_record):
        """Multiple skill invocations accumulate in the registry."""
        from agent.hooks.pre_tool_use import _SESSION_SKILLS, _handle_skill_tool_start

        with patch("agent.hooks.pre_tool_use._start_pipeline_stage"):
            _handle_skill_tool_start({"skill": "do-build"}, "uuid-1")
            _handle_skill_tool_start({"skill": "do-test"}, "uuid-1")
            _handle_skill_tool_start({"skill": "do-build"}, "uuid-1")

        assert _SESSION_SKILLS == {"session-A": ["do-build", "do-test", "do-build"]}

    @patch("agent.hooks.pre_tool_use.record_metric")
    @patch("agent.hooks.session_registry.resolve", return_value="session-B")
    def test_unknown_skill_appended_to_registry(self, mock_resolve, mock_record):
        """Unknown skills are also tracked in the registry."""
        from agent.hooks.pre_tool_use import _SESSION_SKILLS, _handle_skill_tool_start

        _handle_skill_tool_start({"skill": "do-discover-paths"}, "uuid-2")

        assert _SESSION_SKILLS == {"session-B": ["do-discover-paths"]}


class TestPostSessionSkillOutcome:
    """Test that post-session extraction records skill outcomes."""

    @pytest.mark.asyncio
    @patch("agent.memory_extraction.extract_observations_async", return_value=None)
    @patch("agent.memory_extraction.detect_outcomes_async", return_value={})
    @patch("agent.memory_hook.get_injected_thoughts", return_value=[])
    @patch("agent.memory_hook.clear_session")
    async def test_skill_outcomes_recorded(
        self, mock_clear, mock_thoughts, mock_detect, mock_extract
    ):
        """Post-session extraction records outcome metrics for invoked skills."""
        from agent.hooks.pre_tool_use import _SESSION_SKILLS
        from agent.memory_extraction import run_post_session_extraction

        # Pre-populate the registry
        _SESSION_SKILLS["session-X"] = ["do-build", "do-test", "do-build"]

        with patch("analytics.collector.record_metric") as mock_record:
            await run_post_session_extraction("session-X", "some response text")

        # Should record deduped skill outcomes
        calls = mock_record.call_args_list
        skill_names = {c.args[2]["skill"] for c in calls}
        assert skill_names == {"do-build", "do-test"}
        for call in calls:
            assert call.args[0] == "skill.outcome"
            assert call.args[1] == 1
            assert call.args[2]["outcome"] == "success"
            assert call.args[2]["session_id"] == "session-X"

    @pytest.mark.asyncio
    @patch("agent.memory_extraction.extract_observations_async", return_value=None)
    @patch("agent.memory_extraction.detect_outcomes_async", return_value={})
    @patch("agent.memory_hook.get_injected_thoughts", return_value=[])
    @patch("agent.memory_hook.clear_session")
    async def test_session_skills_cleaned_up(
        self, mock_clear, mock_thoughts, mock_detect, mock_extract
    ):
        """Post-session extraction removes the session from _SESSION_SKILLS."""
        from agent.hooks.pre_tool_use import _SESSION_SKILLS
        from agent.memory_extraction import run_post_session_extraction

        _SESSION_SKILLS["session-Y"] = ["do-plan"]

        with patch("analytics.collector.record_metric"):
            await run_post_session_extraction("session-Y", "response")

        assert "session-Y" not in _SESSION_SKILLS

    @pytest.mark.asyncio
    @patch("agent.memory_extraction.extract_observations_async", return_value=None)
    @patch("agent.memory_extraction.detect_outcomes_async", return_value={})
    @patch("agent.memory_hook.get_injected_thoughts", return_value=[])
    @patch("agent.memory_hook.clear_session")
    async def test_no_skills_no_outcome_metrics(
        self, mock_clear, mock_thoughts, mock_detect, mock_extract
    ):
        """No skills invoked means no outcome metrics recorded."""
        from agent.memory_extraction import run_post_session_extraction

        with patch("analytics.collector.record_metric") as mock_record:
            await run_post_session_extraction("session-Z", "response")

        mock_record.assert_not_called()
