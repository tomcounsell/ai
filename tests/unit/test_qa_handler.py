"""Tests for Teammate handler message enrichment."""

from agent.teammate_handler import TEAMMATE_MAX_NUDGE_COUNT, build_teammate_instructions


class TestBuildTeammateInstructions:
    def test_returns_string(self):
        result = build_teammate_instructions()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_no_agent_tool_instruction(self):
        result = build_teammate_instructions()
        assert "Do NOT spawn a DevSession" in result
        assert "Do NOT use the Agent tool" in result

    def test_read_only_tools_mentioned(self):
        result = build_teammate_instructions()
        assert "Read" in result
        assert "Glob" in result
        assert "Grep" in result

    def test_no_write_instruction(self):
        result = build_teammate_instructions()
        assert "Do NOT write files" in result

    def test_no_send_telegram_instruction(self):
        """Teammate should not reference send_telegram.py -- single delivery path via summarizer."""
        result = build_teammate_instructions()
        assert "send_telegram.py" not in result

    def test_conversational_tone(self):
        result = build_teammate_instructions()
        assert "directly" in result
        assert "conversational" in result.lower() or "conversationally" in result.lower()

    def test_research_first_behavior(self):
        """Teammate instructions should emphasize research before answering."""
        result = build_teammate_instructions()
        assert "memory_search" in result
        assert "Grep" in result or "Glob" in result
        assert "evidence" in result.lower() or "cite" in result.lower()


class TestTeammateConstants:
    def test_nudge_cap_less_than_default(self):
        """Teammate nudge cap should be significantly lower than the default 50."""
        assert TEAMMATE_MAX_NUDGE_COUNT < 50
        assert TEAMMATE_MAX_NUDGE_COUNT == 10
