"""Unit tests for the Redis re-read in _complete_agent_session.

Covers Bug 2 fix: worker cancellation path re-reads session from Redis before
finalizing to capture accumulated stage_states (SDLC pipeline transitions).

See docs/plans/session_lifecycle_stale_cleanup.md Bug 2.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_session(
    session_id="session-1",
    agent_session_id="agent-1",
    status="running",
    stage_states=None,
):
    """Create a minimal session-like object with stage_states."""
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=agent_session_id,
        status=status,
        stage_states=stage_states or {},
        session_events=[],
        created_at=0,
        save=MagicMock(),
        delete=MagicMock(),
        log_lifecycle_transition=MagicMock(),
    )


class TestCompleteAgentSessionRedisReread:
    """_complete_agent_session re-reads from Redis before calling finalize_session."""

    @pytest.mark.asyncio
    async def test_fresh_record_used_when_found(self):
        """When a fresher running record exists in Redis, it is used for finalization."""
        from agent.agent_session_queue import _complete_agent_session

        stale_session = _make_session(
            session_id="sid-1",
            stage_states={"PLAN": "pending", "BUILD": "pending"},
        )
        fresh_session = _make_session(
            session_id="sid-1",
            stage_states={"PLAN": "completed", "BUILD": "completed", "TEST": "running"},
        )

        with (
            patch("agent.agent_session_queue.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.return_value = [fresh_session]

            await _complete_agent_session(stale_session, failed=False)

        # finalize_session must be called with the FRESH record, not the stale one
        mock_finalize.assert_called_once()
        args, kwargs = mock_finalize.call_args
        used_session = args[0]
        assert used_session is fresh_session, (
            "Should use fresh Redis record, not stale in-memory object"
        )
        assert used_session.stage_states["PLAN"] == "completed"
        assert used_session.stage_states["BUILD"] == "completed"

    @pytest.mark.asyncio
    async def test_fallback_to_in_memory_when_no_running_record(self):
        """When no running record found in Redis, falls back to in-memory session."""
        from agent.agent_session_queue import _complete_agent_session

        stale_session = _make_session(session_id="sid-2")

        with (
            patch("agent.agent_session_queue.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            # Redis returns nothing for this session_id
            mock_as_class.query.filter.return_value = []

            await _complete_agent_session(stale_session, failed=True)

        mock_finalize.assert_called_once()
        args, kwargs = mock_finalize.call_args
        used_session = args[0]
        assert used_session is stale_session, (
            "Should fall back to in-memory object when Redis has no running record"
        )
        assert args[1] == "failed"

    @pytest.mark.asyncio
    async def test_fallback_to_in_memory_when_redis_raises(self):
        """When Redis read raises an exception, falls back to in-memory session."""
        from agent.agent_session_queue import _complete_agent_session

        stale_session = _make_session(session_id="sid-3")

        with (
            patch("agent.agent_session_queue.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.side_effect = ConnectionError("Redis down")

            await _complete_agent_session(stale_session, failed=False)

        mock_finalize.assert_called_once()
        args, kwargs = mock_finalize.call_args
        assert args[0] is stale_session, "Should fall back to in-memory on Redis error"
        assert args[1] == "completed"

    @pytest.mark.asyncio
    async def test_most_recent_record_chosen_when_multiple_found(self):
        """When multiple running records exist, the most recently created is used."""
        from agent.agent_session_queue import _complete_agent_session

        stale_session = _make_session(session_id="sid-4")
        old_record = _make_session(session_id="sid-4")
        old_record.created_at = 1000.0
        old_record.stage_states = {"PLAN": "completed"}

        new_record = _make_session(session_id="sid-4")
        new_record.created_at = 2000.0
        new_record.stage_states = {"PLAN": "completed", "BUILD": "completed"}

        with (
            patch("agent.agent_session_queue.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            # Return in "wrong" order to test sorting
            mock_as_class.query.filter.return_value = [old_record, new_record]

            await _complete_agent_session(stale_session, failed=False)

        args, _ = mock_finalize.call_args
        used_session = args[0]
        assert used_session is new_record, "Should use the most recent record (created_at=2000)"
        assert "BUILD" in used_session.stage_states

    @pytest.mark.asyncio
    async def test_none_session_id_skips_reread(self):
        """When session_id is None, skips Redis re-read and uses in-memory object."""
        from agent.agent_session_queue import _complete_agent_session

        no_id_session = _make_session(session_id=None)

        with (
            patch("agent.agent_session_queue.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            await _complete_agent_session(no_id_session, failed=False)

        # AgentSession.query.filter should not be called when session_id is None
        mock_as_class.query.filter.assert_not_called()
        mock_finalize.assert_called_once()
        args, _ = mock_finalize.call_args
        assert args[0] is no_id_session

    @pytest.mark.asyncio
    async def test_status_completed_for_success(self):
        """_complete_agent_session passes 'completed' status when failed=False."""
        from agent.agent_session_queue import _complete_agent_session

        session = _make_session(session_id="sid-ok")

        with (
            patch("agent.agent_session_queue.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.return_value = []
            await _complete_agent_session(session, failed=False)

        args, _ = mock_finalize.call_args
        assert args[1] == "completed"

    @pytest.mark.asyncio
    async def test_status_failed_for_failure(self):
        """_complete_agent_session passes 'failed' status when failed=True."""
        from agent.agent_session_queue import _complete_agent_session

        session = _make_session(session_id="sid-fail")

        with (
            patch("agent.agent_session_queue.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.return_value = []
            await _complete_agent_session(session, failed=True)

        args, _ = mock_finalize.call_args
        assert args[1] == "failed"
