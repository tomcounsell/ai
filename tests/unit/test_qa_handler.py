"""Tests for Teammate handler message enrichment."""

from agent.teammate_handler import TEAMMATE_MAX_NUDGE_COUNT, build_teammate_instructions


class TestBuildTeammateInstructions:
    def test_returns_string(self):
        result = build_teammate_instructions()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_no_agent_tool_instruction(self):
        result = build_teammate_instructions()
        assert "Do NOT spawn a Dev session" in result
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

    def test_conversational_humility(self):
        """Teammate instructions should use humility-first framing."""
        result = build_teammate_instructions()
        assert "directly" in result
        assert "conversationally" in result.lower()
        # Humility markers from the plan
        assert "I think" in result
        assert "from what I've seen" in result
        assert "clarif" in result.lower()

    def test_direct_colleague_framing(self):
        """Teammate should frame as direct colleague, not an interviewer."""
        result = build_teammate_instructions()
        assert "direct" in result.lower()
        assert "colleague" in result
        # Old framing should be gone
        assert "knowledgeable teammate" not in result
        assert "who knows the codebase well" not in result

    def test_brevity_guidance(self):
        """Teammate instructions should include brevity guidance."""
        result = build_teammate_instructions()
        assert "1-3 sentences" in result or "brief" in result.lower()

    def test_research_first_behavior(self):
        """Teammate instructions should emphasize research before answering."""
        result = build_teammate_instructions()
        assert "memory_search" in result
        assert "Grep" in result or "Glob" in result
        assert "evidence" in result.lower() or "cite" in result.lower()

    def test_review_gate_awareness(self):
        """Teammate prompt should mention the delivery review gate and the
        tool-call delivery contract introduced in PR #1072.

        The legacy SEND / EDIT: <text> / REACT: <emoji> / SILENT / CONTINUE
        prefix protocol was removed; the parser and the drafter scrubber
        no longer recognise those tokens, so any literal prefix in agent
        output leaks verbatim through the outbox path.
        """
        result = build_teammate_instructions()
        assert "DELIVERY REVIEW" in result
        # Tool-call delivery contract (mirrors agent/hooks/stop.py:163-199).
        assert "tools/send_message.py" in result
        assert "tools/react_with_emoji.py" in result
        # Conceptual options are still mentioned (silent / continue) but
        # not as parseable prefixes the agent should emit.
        assert "Silent" in result
        assert "Continue" in result
        # Stale legacy prefix protocol must NOT appear as instructions.
        assert "SEND — deliver" not in result
        assert "EDIT: <your revised text>" not in result
        assert "REACT: <emoji>" not in result
        assert "SILENT — send nothing" not in result


class TestTeammateConstants:
    def test_nudge_cap_less_than_default(self):
        """Teammate nudge cap should be significantly lower than the default 50."""
        assert TEAMMATE_MAX_NUDGE_COUNT < 50
        assert TEAMMATE_MAX_NUDGE_COUNT == 10
