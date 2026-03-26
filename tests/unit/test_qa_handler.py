"""Tests for Q&A handler message enrichment."""

from agent.qa_handler import QA_MAX_NUDGE_COUNT, build_qa_instructions


class TestBuildQaInstructions:
    def test_returns_string(self):
        result = build_qa_instructions()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_no_agent_tool_instruction(self):
        result = build_qa_instructions()
        assert "Do NOT spawn a DevSession" in result
        assert "Do NOT use the Agent tool" in result

    def test_read_only_tools_mentioned(self):
        result = build_qa_instructions()
        assert "Read" in result
        assert "Glob" in result
        assert "Grep" in result

    def test_no_write_instruction(self):
        result = build_qa_instructions()
        assert "Do NOT write files" in result

    def test_telegram_send_instruction(self):
        result = build_qa_instructions()
        assert "send_telegram.py" in result

    def test_conversational_tone(self):
        result = build_qa_instructions()
        assert "directly" in result
        assert "conversational" in result.lower() or "conversationally" in result.lower()


class TestQaConstants:
    def test_nudge_cap_less_than_default(self):
        """Q&A nudge cap should be significantly lower than the default 50."""
        assert QA_MAX_NUDGE_COUNT < 50
        assert QA_MAX_NUDGE_COUNT == 10
