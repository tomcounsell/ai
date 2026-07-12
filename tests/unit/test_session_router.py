"""Unit tests for bridge/session_router.py semantic session routing.

#1925: the LLM classification call routes through ``agent.llm.run_typed``
with a typed ``SessionRouteDecision`` output model instead of a hand-rolled
``anthropic_slot()`` client + markdown-fence-strip + ``json.loads`` parse.
These tests mock ``run_typed`` at its ``bridge.session_router`` import site
(module-level ``from agent.llm import run_typed``) -- no real network call
and no dependence on PydanticAI's internal Anthropic tool-calling wire
format. AgentSession candidates are lightweight fakes (no Redis needed).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bridge.session_router import (
    ROUTING_CONFIDENCE_THRESHOLD,
    SessionRouteDecision,
    find_matching_session,
)


def _make_candidate(session_id: str, status: str = "dormant", expectations: str = "a reply"):
    return SimpleNamespace(
        session_id=session_id,
        status=status,
        expectations=expectations,
        context_summary="some context",
        updated_at=100.0,
        created_at=90.0,
    )


class TestFindMatchingSessionZeroCandidates:
    @pytest.mark.asyncio
    async def test_no_candidates_skips_llm_call(self):
        mock_run_typed = AsyncMock(side_effect=AssertionError("run_typed MUST NOT be invoked"))
        with (
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("bridge.session_router.run_typed", mock_run_typed),
        ):
            mock_query.filter.return_value = []
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="hello", project_key="valor"
            )
        assert matched_id is None
        assert confidence == 0.0
        mock_run_typed.assert_not_called()

    @pytest.mark.asyncio
    async def test_candidates_without_expectations_are_excluded(self):
        """A candidate with no expectations never reaches the LLM call."""
        mock_run_typed = AsyncMock(side_effect=AssertionError("run_typed MUST NOT be invoked"))
        candidate = _make_candidate("sess-1", expectations="")
        with (
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("bridge.session_router.run_typed", mock_run_typed),
        ):
            mock_query.filter.return_value = [candidate]
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="hello", project_key="valor"
            )
        assert matched_id is None
        assert confidence == 0.0
        mock_run_typed.assert_not_called()


class TestFindMatchingSessionWithCandidates:
    @pytest.mark.asyncio
    async def test_high_confidence_match_routes(self):
        candidate = _make_candidate("sess-match")
        mock_run_typed = AsyncMock(
            return_value=SessionRouteDecision(
                match="sess-match", confidence=0.92, reason="directly answers expectations"
            )
        )
        with (
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("bridge.session_router.get_anthropic_api_key", return_value="fake-key"),
            patch("bridge.session_router.run_typed", mock_run_typed),
        ):
            mock_query.filter.return_value = [candidate]
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="plan", project_key="valor"
            )
        assert matched_id == "sess-match"
        assert confidence == 0.92
        mock_run_typed.assert_called_once()
        # Typed output model asserted directly (#1925).
        call_args = mock_run_typed.call_args
        assert call_args[0][1] is SessionRouteDecision

    @pytest.mark.asyncio
    async def test_below_threshold_does_not_route(self):
        candidate = _make_candidate("sess-1")
        mock_run_typed = AsyncMock(
            return_value=SessionRouteDecision(match="sess-1", confidence=0.5, reason="weak signal")
        )
        with (
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("bridge.session_router.get_anthropic_api_key", return_value="fake-key"),
            patch("bridge.session_router.run_typed", mock_run_typed),
        ):
            mock_query.filter.return_value = [candidate]
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="unrelated", project_key="valor"
            )
        assert matched_id is None
        assert confidence == 0.0
        assert 0.5 < ROUTING_CONFIDENCE_THRESHOLD  # sanity on the fixture

    @pytest.mark.asyncio
    async def test_null_match_does_not_route(self):
        candidate = _make_candidate("sess-1")
        mock_run_typed = AsyncMock(
            return_value=SessionRouteDecision(match=None, confidence=0.0, reason="new topic")
        )
        with (
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("bridge.session_router.get_anthropic_api_key", return_value="fake-key"),
            patch("bridge.session_router.run_typed", mock_run_typed),
        ):
            mock_query.filter.return_value = [candidate]
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="unrelated", project_key="valor"
            )
        assert matched_id is None
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_invalid_matched_id_is_rejected(self):
        """A matched_id not among the candidates is treated as no-match (hallucination guard)."""
        candidate = _make_candidate("sess-real")
        mock_run_typed = AsyncMock(
            return_value=SessionRouteDecision(
                match="sess-does-not-exist", confidence=0.95, reason="hallucinated id"
            )
        )
        with (
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("bridge.session_router.get_anthropic_api_key", return_value="fake-key"),
            patch("bridge.session_router.run_typed", mock_run_typed),
        ):
            mock_query.filter.return_value = [candidate]
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="plan", project_key="valor"
            )
        assert matched_id is None
        assert confidence == 0.0


class TestFindMatchingSessionFailSafe:
    @pytest.mark.asyncio
    async def test_no_api_key_skips_llm_call(self):
        candidate = _make_candidate("sess-1")
        mock_run_typed = AsyncMock(side_effect=AssertionError("run_typed MUST NOT be invoked"))
        with (
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("bridge.session_router.get_anthropic_api_key", return_value=""),
            patch("bridge.session_router.run_typed", mock_run_typed),
        ):
            mock_query.filter.return_value = [candidate]
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="plan", project_key="valor"
            )
        assert matched_id is None
        assert confidence == 0.0
        mock_run_typed.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_call_error_degrades_to_no_match(self):
        """Preserve the site's conservative default: any wrapper failure
        (timeout, exhausted schema retry, provider error) degrades gracefully
        to new-session creation rather than raising."""
        from agent.llm import LLMCallError

        candidate = _make_candidate("sess-1")
        mock_run_typed = AsyncMock(side_effect=LLMCallError("simulated failure"))
        with (
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("bridge.session_router.get_anthropic_api_key", return_value="fake-key"),
            patch("bridge.session_router.run_typed", mock_run_typed),
        ):
            mock_query.filter.return_value = [candidate]
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="plan", project_key="valor"
            )
        assert matched_id is None
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_unrelated_exception_degrades_to_no_match(self):
        """Any unexpected exception (not just LLMCallError) still degrades safely."""
        with patch("models.agent_session.AgentSession.query") as mock_query:
            mock_query.filter.side_effect = RuntimeError("redis down")
            matched_id, confidence = await find_matching_session(
                chat_id="chat-1", message_text="plan", project_key="valor"
            )
        assert matched_id is None
        assert confidence == 0.0
