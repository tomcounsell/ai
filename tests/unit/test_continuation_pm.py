"""Unit tests for the continuation PM fallback mechanism.

Tests that _create_continuation_pm creates valid PM sessions and that
_handle_dev_session_completion invokes it when steering fails.

See docs/plans/pm-session-scope-and-wait.md for the design.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models.agent_session import AgentSession


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def terminal_pm(redis_test_db):
    """Create a parent PM session in terminal (completed) status."""
    session = AgentSession.create(
        session_id="pm-continuation-001",
        session_type="pm",
        project_key="test",
        status="completed",
        chat_id="999",
        sender_name="TestUser",
        message_text="Run SDLC on issue #934 (issues/934)",
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
        continuation_depth=0,
    )
    return session


@pytest.fixture
def dev_session_for_continuation(terminal_pm, redis_test_db):
    """Create a child dev session linked to the terminal PM."""
    session = AgentSession.create(
        session_id="dev-continuation-001",
        session_type="dev",
        project_key="test",
        status="active",
        chat_id="999",
        sender_name="TestUser",
        message_text="Stage: BUILD\nImplement feature (issues/934)",
        parent_agent_session_id=terminal_pm.agent_session_id,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )
    return session


# ---------------------------------------------------------------------------
# Test 1: _create_continuation_pm creates a valid PM session
# ---------------------------------------------------------------------------


class TestCreateContinuationPM:
    """Direct tests for _create_continuation_pm."""

    def test_creates_pm_session_with_correct_fields(self, terminal_pm, redis_test_db):
        """Continuation PM is created with correct session_type, status, and depth."""
        from agent.agent_session_queue import _create_continuation_pm

        _create_continuation_pm(
            parent=terminal_pm,
            agent_session=None,
            issue_number=934,
            stage="BUILD",
            outcome="success",
            result_preview="PR created successfully.",
        )

        # Find the continuation session
        children = list(
            AgentSession.query.filter(parent_agent_session_id=terminal_pm.agent_session_id)
        )
        # Filter to PM sessions only (exclude dev_session_for_continuation if present)
        pm_children = [c for c in children if c.session_type == "pm"]
        assert len(pm_children) == 1

        cont = pm_children[0]
        assert cont.session_type == "pm"
        assert cont.status == "pending"
        assert cont.continuation_depth == 1
        assert cont.chat_id == terminal_pm.chat_id
        assert cont.project_key == terminal_pm.project_key
        assert "CONTINUATION" in cont.message_text
        assert "934" in cont.message_text
        assert "BUILD" in cont.message_text

    def test_creates_session_with_issue_number_none(self, terminal_pm, redis_test_db):
        """Continuation PM is created even when issue_number is None."""
        from agent.agent_session_queue import _create_continuation_pm

        _create_continuation_pm(
            parent=terminal_pm,
            agent_session=None,
            issue_number=None,
            stage="CRITIQUE",
            outcome="success",
            result_preview="Critique complete.",
        )

        pm_children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=terminal_pm.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(pm_children) == 1
        cont = pm_children[0]
        assert "unknown issue" in cont.message_text
        assert cont.status == "pending"

    def test_creates_session_with_empty_result_preview(self, terminal_pm, redis_test_db):
        """Continuation PM handles empty result_preview gracefully."""
        from agent.agent_session_queue import _create_continuation_pm

        _create_continuation_pm(
            parent=terminal_pm,
            agent_session=None,
            issue_number=934,
            stage="BUILD",
            outcome="fail",
            result_preview="",
        )

        pm_children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=terminal_pm.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(pm_children) == 1


# ---------------------------------------------------------------------------
# Test 2: Continuation depth cap prevents infinite chains
# ---------------------------------------------------------------------------


class TestContinuationDepthCap:
    """Depth cap prevents runaway continuation PM chains."""

    def test_depth_cap_blocks_at_max(self, redis_test_db):
        """Continuation PM is NOT created when parent depth >= max (3)."""
        from agent.agent_session_queue import (
            _CONTINUATION_PM_MAX_DEPTH,
            _create_continuation_pm,
        )

        # Create a parent at max depth
        deep_parent = AgentSession.create(
            session_id="pm-deep-001",
            session_type="pm",
            project_key="test",
            status="completed",
            chat_id="999",
            message_text="Deep continuation",
            continuation_depth=_CONTINUATION_PM_MAX_DEPTH,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        _create_continuation_pm(
            parent=deep_parent,
            agent_session=None,
            issue_number=934,
            stage="BUILD",
            outcome="success",
            result_preview="Some result.",
        )

        # No continuation should be created
        children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=deep_parent.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(children) == 0

    def test_depth_increments_from_parent(self, redis_test_db):
        """Continuation depth is parent_depth + 1."""
        from agent.agent_session_queue import _create_continuation_pm

        parent_at_1 = AgentSession.create(
            session_id="pm-depth-1",
            session_type="pm",
            project_key="test",
            status="completed",
            chat_id="999",
            message_text="First continuation",
            continuation_depth=1,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        _create_continuation_pm(
            parent=parent_at_1,
            agent_session=None,
            issue_number=934,
            stage="TEST",
            outcome="success",
            result_preview="Tests passed.",
        )

        children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=parent_at_1.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(children) == 1
        assert children[0].continuation_depth == 2


# ---------------------------------------------------------------------------
# Test 3: _handle_dev_session_completion calls continuation PM on steer failure
# ---------------------------------------------------------------------------


class TestHandleCompletionContinuationFallback:
    """_handle_dev_session_completion creates continuation PM when steer fails."""

    @pytest.mark.asyncio
    async def test_steer_failure_triggers_continuation(
        self, terminal_pm, dev_session_for_continuation, redis_test_db
    ):
        """When steer_session returns failure, a continuation PM is created."""
        from agent.agent_session_queue import _handle_dev_session_completion

        with (
            patch(
                "agent.agent_session_queue.steer_session",
                return_value={
                    "success": False,
                    "session_id": terminal_pm.session_id,
                    "error": "Session is in terminal status 'completed' — steering rejected",
                },
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=934),
        ):
            await _handle_dev_session_completion(
                session=terminal_pm,
                agent_session=dev_session_for_continuation,
                result="BUILD complete. PR created.",
            )

        # A continuation PM should have been created
        pm_children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=terminal_pm.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(pm_children) >= 1
        cont = pm_children[0]
        assert cont.status == "pending"
        assert "CONTINUATION" in cont.message_text

    @pytest.mark.asyncio
    async def test_steer_success_no_continuation(self, redis_test_db):
        """When steer_session succeeds and parent is still active, no continuation PM."""
        from agent.agent_session_queue import _handle_dev_session_completion

        # Create an active parent
        active_pm = AgentSession.create(
            session_id="pm-active-steer-001",
            session_type="pm",
            project_key="test",
            status="active",
            chat_id="999",
            message_text="Run SDLC on issue #934 (issues/934)",
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        dev = AgentSession.create(
            session_id="dev-active-steer-001",
            session_type="dev",
            project_key="test",
            status="active",
            chat_id="999",
            message_text="Stage: BUILD",
            parent_agent_session_id=active_pm.agent_session_id,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        with (
            patch(
                "agent.agent_session_queue.steer_session",
                return_value={"success": True, "session_id": active_pm.session_id, "error": None},
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=None),
        ):
            await _handle_dev_session_completion(
                session=active_pm,
                agent_session=dev,
                result="BUILD complete.",
            )

        # No continuation PM should exist
        pm_children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=active_pm.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(pm_children) == 0

    @pytest.mark.asyncio
    async def test_exception_in_create_continuation_does_not_propagate(
        self, terminal_pm, dev_session_for_continuation, redis_test_db
    ):
        """Failures in _create_continuation_pm do not crash the completion handler."""
        from agent.agent_session_queue import _handle_dev_session_completion

        with (
            patch(
                "agent.agent_session_queue.steer_session",
                return_value={"success": False, "error": "terminal"},
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=934),
            patch(
                "agent.agent_session_queue._create_continuation_pm",
                side_effect=RuntimeError("Redis down"),
            ),
        ):
            # Should not raise
            await _handle_dev_session_completion(
                session=terminal_pm,
                agent_session=dev_session_for_continuation,
                result="BUILD complete.",
            )


# ---------------------------------------------------------------------------
# Test 4: Redis SETNX dedup prevents duplicate continuation PMs
# ---------------------------------------------------------------------------


class TestContinuationDedup:
    """Redis SETNX dedup guard prevents duplicate continuation PMs."""

    def test_second_call_skips_creation(self, terminal_pm, redis_test_db):
        """Second call to _create_continuation_pm for same parent is a no-op."""
        from agent.agent_session_queue import _create_continuation_pm

        # First call: creates
        _create_continuation_pm(
            parent=terminal_pm,
            agent_session=None,
            issue_number=934,
            stage="BUILD",
            outcome="success",
            result_preview="First result.",
        )

        # Second call: dedup blocks
        _create_continuation_pm(
            parent=terminal_pm,
            agent_session=None,
            issue_number=934,
            stage="BUILD",
            outcome="success",
            result_preview="Second result.",
        )

        pm_children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=terminal_pm.agent_session_id
            )
            if c.session_type == "pm"
        ]
        # Only one continuation should exist despite two calls
        assert len(pm_children) == 1
