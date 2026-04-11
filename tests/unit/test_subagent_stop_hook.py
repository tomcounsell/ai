"""Unit tests for agent/hooks/subagent_stop.py hook.

Phase 5 update: Removed tests for _register_dev_session_completion,
_record_stage_on_parent, _post_stage_comment_on_completion, _get_stage_states,
_resolve_tracking_issue, and _extract_output_tail — all removed from the hook
as part of the session_registry cleanup. Dev session stage tracking is now
handled by the worker's post-completion handler (_handle_dev_session_completion
in agent/agent_session_queue.py).

Retained: basic logging tests and _extract_outcome_summary tests.
"""

import logging

import pytest

from agent.hooks.subagent_stop import (
    _extract_outcome_summary,
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
        """Non dev-session agent types should return empty dict."""
        input_data = {"agent_type": "chat-session", "agent_id": "xyz"}
        result = await subagent_stop_hook(input_data, None, None)
        assert result == {}

    @pytest.mark.asyncio
    async def test_dev_session_returns_empty(self):
        """Dev session type returns empty dict (stage tracking moved to worker)."""
        input_data = {"agent_type": "dev-session", "agent_id": "dev-123"}
        result = await subagent_stop_hook(input_data, None, None)
        assert result == {}


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
