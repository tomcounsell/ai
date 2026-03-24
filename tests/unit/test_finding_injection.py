"""Tests for finding injection paths.

Verifies PostToolUse injection (via memory_hook) and pre-dispatch
injection (via pre_tool_use hook).
"""

from unittest.mock import MagicMock, patch


class TestMemoryHookFindingInjection:
    """_inject_findings() in memory_hook.py."""

    def test_returns_empty_when_no_slug(self, monkeypatch):
        """Should return empty list when no slug is available."""
        from agent.memory_hook import _inject_findings

        monkeypatch.delenv("VALOR_WORK_ITEM_SLUG", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        result = _inject_findings("session-1", ["auth", "jwt"])
        assert result == []

    @patch("agent.finding_query.query_findings")
    def test_returns_thought_blocks_when_findings_exist(self, mock_query, monkeypatch):
        """Should return <thought> blocks with finding content."""
        from agent.memory_hook import _inject_findings

        monkeypatch.setenv("VALOR_WORK_ITEM_SLUG", "auth-feature")

        f1 = MagicMock()
        f1.content = "Auth uses JWT RS256"
        f1.stage = "BUILD"
        mock_query.return_value = [f1]

        result = _inject_findings("session-1", ["auth"])
        assert len(result) == 1
        assert "<thought>" in result[0]
        assert "Auth uses JWT RS256" in result[0]
        assert "Prior finding from BUILD" in result[0]

    @patch("agent.finding_query.query_findings")
    def test_returns_empty_when_no_findings(self, mock_query, monkeypatch):
        """Should return empty list when no findings match."""
        from agent.memory_hook import _inject_findings

        monkeypatch.setenv("VALOR_WORK_ITEM_SLUG", "auth-feature")
        mock_query.return_value = []

        result = _inject_findings("session-1", ["auth"])
        assert result == []

    def test_handles_exception_gracefully(self, monkeypatch):
        """Should return empty list on any exception."""
        from agent.memory_hook import _inject_findings

        monkeypatch.setenv("VALOR_WORK_ITEM_SLUG", "auth-feature")

        with patch("agent.finding_query.query_findings", side_effect=Exception("boom")):
            result = _inject_findings("session-1", ["auth"])
            assert result == []

    @patch("agent.finding_query.query_findings")
    def test_falls_back_to_session_slug(self, mock_query, monkeypatch):
        """Should get slug from session when env var not set."""
        from agent.memory_hook import _inject_findings

        monkeypatch.delenv("VALOR_WORK_ITEM_SLUG", raising=False)
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session")

        mock_session = MagicMock()
        mock_session.slug = "test-slug"
        mock_session.work_item_slug = None

        with patch("models.agent_session.AgentSession.query") as mock_query_cls:
            mock_query_cls.filter.return_value = [mock_session]
            mock_query.return_value = []

            _inject_findings("session-1", ["topic"])
            # Should have queried with the session slug
            mock_query.assert_called_once()


class TestPreToolUseFindingInjection:
    """_maybe_inject_findings_into_prompt() in pre_tool_use.py."""

    def test_skips_non_dev_session(self):
        """Should not inject for non-dev-session agent types."""
        from agent.hooks.pre_tool_use import _maybe_inject_findings_into_prompt

        tool_input = {"type": "research", "prompt": "Do research"}
        _maybe_inject_findings_into_prompt(tool_input)
        # Prompt should be unchanged
        assert tool_input["prompt"] == "Do research"

    @patch("agent.finding_query.query_findings")
    @patch("agent.finding_query.format_findings_for_injection")
    def test_injects_findings_into_prompt(self, mock_format, mock_query, monkeypatch):
        """Should append findings to dev-session prompt."""
        from agent.hooks.pre_tool_use import _maybe_inject_findings_into_prompt

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session")

        mock_session = MagicMock()
        mock_session.slug = "auth-feature"
        mock_session.work_item_slug = None
        mock_session.project_key = "test"

        f1 = MagicMock()
        mock_query.return_value = [f1]
        mock_format.return_value = "## Prior Findings\n- [BUILD] test finding"

        tool_input = {"type": "dev-session", "prompt": "Build the auth feature"}

        with patch("models.agent_session.AgentSession.query") as mock_q:
            mock_q.filter.return_value = [mock_session]
            _maybe_inject_findings_into_prompt(tool_input)

        assert "Prior Findings" in tool_input["prompt"]
        assert "Build the auth feature" in tool_input["prompt"]

    def test_handles_no_session_gracefully(self, monkeypatch):
        """Should not crash when no parent session found."""
        from agent.hooks.pre_tool_use import _maybe_inject_findings_into_prompt

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        tool_input = {"type": "dev-session", "prompt": "Build it"}
        _maybe_inject_findings_into_prompt(tool_input)
        assert tool_input["prompt"] == "Build it"

    @patch("models.agent_session.AgentSession.query")
    def test_handles_no_slug_gracefully(self, mock_query, monkeypatch):
        """Should skip injection when session has no slug."""
        from agent.hooks.pre_tool_use import _maybe_inject_findings_into_prompt

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session")

        mock_session = MagicMock()
        mock_session.slug = None
        mock_session.work_item_slug = None
        mock_query.filter.return_value = [mock_session]

        tool_input = {"type": "dev-session", "prompt": "Build it"}
        _maybe_inject_findings_into_prompt(tool_input)
        assert tool_input["prompt"] == "Build it"


class TestSubagentStopFindingExtraction:
    """_extract_and_persist_findings() in subagent_stop.py."""

    def test_skips_when_no_session_id(self, monkeypatch):
        """Should skip when VALOR_SESSION_ID is not set."""
        from agent.hooks.subagent_stop import _extract_and_persist_findings

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        # Should not raise
        _extract_and_persist_findings({"result": "done"}, "agent-1")

    @patch("agent.finding_extraction.extract_findings_from_output")
    @patch("models.agent_session.AgentSession.query")
    def test_extracts_findings_on_completion(self, mock_query, mock_extract, monkeypatch):
        """Should call extraction when parent session has a slug."""
        from agent.hooks.subagent_stop import _extract_and_persist_findings

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session")

        mock_session = MagicMock()
        mock_session.slug = "auth-feature"
        mock_session.work_item_slug = None
        mock_session.project_key = "test"
        mock_session.current_stage = "BUILD"
        mock_query.filter.return_value = [mock_session]

        mock_extract.return_value = [{"finding_id": "f1"}]

        _extract_and_persist_findings({"result": "Built auth module successfully"}, "agent-1")

        mock_extract.assert_called_once()
        call_kwargs = mock_extract.call_args[1]
        assert call_kwargs["slug"] == "auth-feature"
        assert call_kwargs["stage"] == "BUILD"

    @patch("models.agent_session.AgentSession.query")
    def test_handles_extraction_error(self, mock_query, monkeypatch):
        """Should not crash on extraction errors."""
        from agent.hooks.subagent_stop import _extract_and_persist_findings

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session")
        mock_query.filter.side_effect = Exception("Redis down")

        # Should not raise
        _extract_and_persist_findings({"result": "done"}, "agent-1")
