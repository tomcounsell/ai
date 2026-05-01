"""Unit tests for the continuation PM fallback mechanism.

Tests that _create_continuation_pm creates valid PM sessions and that
_handle_dev_session_completion invokes it when steering fails.

See docs/plans/pm-session-scope-and-wait.md for the design.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

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
        working_dir="/tmp",
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
        # Issue #1195: spawn contract — both fields must be populated.
        assert cont.session_id is not None
        assert cont.working_dir is not None
        # session_id chain pattern: {parent.session_id}_cont{depth}
        assert cont.session_id == f"{terminal_pm.session_id}_cont1"

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
            for c in AgentSession.query.filter(parent_agent_session_id=terminal_pm.agent_session_id)
            if c.session_type == "pm"
        ]
        assert len(pm_children) == 1
        cont = pm_children[0]
        assert "unknown issue" in cont.message_text
        assert cont.status == "pending"
        # Issue #1195: spawn contract.
        assert cont.session_id is not None
        assert cont.working_dir is not None

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
            for c in AgentSession.query.filter(parent_agent_session_id=terminal_pm.agent_session_id)
            if c.session_type == "pm"
        ]
        assert len(pm_children) == 1
        cont = pm_children[0]
        # Issue #1195: spawn contract — even with empty result_preview, the
        # session_id and working_dir must be populated.
        assert cont.session_id is not None
        assert cont.working_dir is not None

    def test_session_id_pattern_is_continuation_chain(self, terminal_pm, redis_test_db):
        """Issue #1195: session_id follows {parent.session_id}_cont{depth}."""
        from agent.agent_session_queue import _create_continuation_pm

        _create_continuation_pm(
            parent=terminal_pm,
            agent_session=None,
            issue_number=934,
            stage="BUILD",
            outcome="success",
            result_preview="r",
        )

        pm_children = [
            c
            for c in AgentSession.query.filter(parent_agent_session_id=terminal_pm.agent_session_id)
            if c.session_type == "pm"
        ]
        assert len(pm_children) == 1
        # First continuation: depth=1, suffix=_cont1
        assert pm_children[0].session_id == f"{terminal_pm.session_id}_cont1"

    def test_working_dir_resolves_via_helper(self, terminal_pm, redis_test_db):
        """Issue #1195: working_dir is resolved through _resolve_working_dir_for_parent."""
        from agent.agent_session_queue import _create_continuation_pm

        with patch(
            "agent.session_completion._resolve_working_dir_for_parent",
            return_value="/resolved/path",
        ) as mock_resolve:
            _create_continuation_pm(
                parent=terminal_pm,
                agent_session=None,
                issue_number=934,
                stage="BUILD",
                outcome="success",
                result_preview="r",
            )

        # Helper must be called exactly once with the parent.
        assert mock_resolve.call_count == 1
        called_arg = mock_resolve.call_args[0][0]
        assert called_arg.session_id == terminal_pm.session_id

        pm_children = [
            c
            for c in AgentSession.query.filter(parent_agent_session_id=terminal_pm.agent_session_id)
            if c.session_type == "pm"
        ]
        assert len(pm_children) == 1
        assert pm_children[0].working_dir == "/resolved/path"

    def test_no_spawn_when_parent_session_id_is_none(self, redis_test_db, caplog):
        """Issue #1195: malformed parent (session_id=None) does not poison the chain.

        We construct a stand-in parent object with ``session_id=None`` and call
        ``_create_continuation_pm`` directly — it must log the error and skip
        the spawn rather than save another None-id session.
        """
        import logging

        from agent.agent_session_queue import _create_continuation_pm

        class _Stub:
            session_id = None
            agent_session_id = "malformed-parent-001"
            project_key = "test"
            chat_id = "999"
            continuation_depth = 0
            project_config = None

        before_count = len(list(AgentSession.query.all()))

        with caplog.at_level(logging.ERROR):
            _create_continuation_pm(
                parent=_Stub(),
                agent_session=None,
                issue_number=934,
                stage="BUILD",
                outcome="success",
                result_preview="r",
            )

        # No new session created.
        after_count = len(list(AgentSession.query.all()))
        assert after_count == before_count

        # Structured error log emitted.
        _msgs = [r.message for r in caplog.records]
        assert any("[continuation-pm-blocked]" in m and "session_id is None" in m for m in _msgs), (
            f"Expected '[continuation-pm-blocked]' error log; got: {_msgs}"
        )

    def test_resolve_helper_raise_is_logged_not_swallowed(self, terminal_pm, redis_test_db, caplog):
        """If _resolve_working_dir_for_parent raises, the error is logged
        with the [harness] tag and parent context, and no malformed continuation
        is saved.

        Issue #1206: covers the broad ``except Exception`` branch at
        ``agent/session_completion.py:436``. The helper raising mid-spawn is a
        plausible failure (config lookup, projects.json IO error) and the
        previous log line lost parent context, making post-mortem triage
        harder. This test asserts the enriched log message.
        """
        import logging

        from agent.agent_session_queue import _create_continuation_pm

        before = len(list(AgentSession.query.all()))
        with (
            patch(
                "agent.session_completion._resolve_working_dir_for_parent",
                side_effect=RuntimeError("simulated config failure"),
            ),
            caplog.at_level(logging.ERROR),
        ):
            _create_continuation_pm(
                parent=terminal_pm,
                agent_session=None,
                issue_number=934,
                stage="BUILD",
                outcome="success",
                result_preview="r",
            )

        after = len(list(AgentSession.query.all()))
        assert after == before, "no malformed continuation session was saved"

        msgs = [r.message for r in caplog.records]
        assert any("[harness] _create_continuation_pm failed" in m for m in msgs), (
            f"Expected [harness] tag in error log; got: {msgs!r}"
        )
        assert any(terminal_pm.session_id in m for m in msgs), (
            f"Expected parent session_id in error log; got: {msgs!r}"
        )
        assert any("simulated config failure" in m for m in msgs), (
            f"Expected the simulated RuntimeError text in error log; got: {msgs!r}"
        )


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
            working_dir="/tmp",
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
            for c in AgentSession.query.filter(parent_agent_session_id=deep_parent.agent_session_id)
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
            working_dir="/tmp",
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
            for c in AgentSession.query.filter(parent_agent_session_id=parent_at_1.agent_session_id)
            if c.session_type == "pm"
        ]
        assert len(children) == 1
        assert children[0].continuation_depth == 2
        # Issue #1195: chain pattern follows {parent.session_id}_cont{depth}.
        assert children[0].session_id == "pm-depth-1_cont2"
        assert children[0].working_dir is not None


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
            for c in AgentSession.query.filter(parent_agent_session_id=terminal_pm.agent_session_id)
            if c.session_type == "pm"
        ]
        assert len(pm_children) >= 1
        cont = pm_children[0]
        assert cont.status == "pending"
        assert "CONTINUATION" in cont.message_text

    @pytest.mark.asyncio
    async def test_steer_accepted_pm_terminal_creates_continuation(self, redis_test_db):
        """When steer is accepted but PM is terminal at re-check, a continuation PM is created.

        Under the new ordering (issue #987 fix), _handle_dev_session_completion is called
        after complete_transcript() which has already run _finalize_parent_sync(). So when
        the steer is accepted but the PM is in a terminal status at re-check time, a
        continuation PM must be created — the steer message is orphaned and the PM will
        never consume it.
        """
        from agent.agent_session_queue import _handle_dev_session_completion

        # Create a PM that is in terminal (completed) status — simulates the state
        # after _finalize_parent_sync has already run.
        terminal_pm_for_steer = AgentSession.create(
            session_id="pm-terminal-steer-001",
            session_type="pm",
            project_key="test",
            working_dir="/tmp",
            status="completed",
            chat_id="999",
            message_text="Run SDLC on issue #934 (issues/934)",
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        dev = AgentSession.create(
            session_id="dev-terminal-steer-001",
            session_type="dev",
            project_key="test",
            status="completed",
            chat_id="999",
            message_text="Stage: BUILD",
            parent_agent_session_id=terminal_pm_for_steer.agent_session_id,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        with (
            patch(
                "agent.agent_session_queue.steer_session",
                return_value={
                    "success": True,
                    "session_id": terminal_pm_for_steer.session_id,
                    "error": None,
                },
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=934),
        ):
            await _handle_dev_session_completion(
                session=terminal_pm_for_steer,
                agent_session=dev,
                result="BUILD complete.",
            )

        # A continuation PM must be created — the steer was accepted but the PM
        # is terminal and will never consume the message.
        pm_children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=terminal_pm_for_steer.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(pm_children) >= 1
        cont = pm_children[0]
        assert cont.status == "pending"
        assert "CONTINUATION" in cont.message_text

    @pytest.mark.asyncio
    async def test_steer_accepted_pm_non_terminal_no_continuation(self, redis_test_db):
        """When steer is accepted and PM is still active at re-check, no continuation PM.

        This is the happy path: the steer message was accepted and the PM is still
        alive to process it. No continuation PM is needed.
        """
        from agent.agent_session_queue import _handle_dev_session_completion

        # Create an active parent — still alive, will process the steering message
        active_pm = AgentSession.create(
            session_id="pm-active-steer-002",
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
            session_id="dev-active-steer-002",
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

        # No continuation PM — the active PM will consume the steering message
        pm_children = [
            c
            for c in AgentSession.query.filter(parent_agent_session_id=active_pm.agent_session_id)
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
            for c in AgentSession.query.filter(parent_agent_session_id=terminal_pm.agent_session_id)
            if c.session_type == "pm"
        ]
        # Only one continuation should exist despite two calls
        assert len(pm_children) == 1


# ---------------------------------------------------------------------------
# Test 5: Ordering race — PM terminal at re-check creates continuation PM
# ---------------------------------------------------------------------------


class TestHandleCompletionOrderingRace:
    """Tests for the issue #987 ordering race fix.

    After fix 1, _handle_dev_session_completion is called AFTER complete_transcript()
    (which runs _finalize_parent_sync() inline). These tests verify that when the PM
    is already terminal at the time of the re-check guard, a continuation PM is
    unconditionally created regardless of whether the steer was accepted or rejected.
    """

    @pytest.mark.asyncio
    async def test_pm_terminal_at_recheck_creates_continuation(self, redis_test_db):
        """Steer accepted + PM terminal at re-check → continuation PM created.

        This directly simulates the post-fix scenario: _handle_dev_session_completion
        is called after _finalize_parent_sync has already run, so the PM is already
        in a terminal state. The re-check guard must detect this and create a
        continuation PM.
        """
        from agent.agent_session_queue import _handle_dev_session_completion

        # Parent is already terminal — simulates state after _finalize_parent_sync ran
        completed_pm = AgentSession.create(
            session_id="pm-ordering-race-001",
            session_type="pm",
            project_key="test",
            working_dir="/tmp",
            status="completed",
            chat_id="999",
            message_text="Run SDLC on issue #987 (issues/987)",
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        dev = AgentSession.create(
            session_id="dev-ordering-race-001",
            session_type="dev",
            project_key="test",
            status="completed",
            chat_id="999",
            message_text="Stage: PLAN",
            parent_agent_session_id=completed_pm.agent_session_id,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        with (
            patch(
                "agent.agent_session_queue.steer_session",
                return_value={
                    "success": True,
                    "session_id": completed_pm.session_id,
                    "error": None,
                },
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=987),
        ):
            await _handle_dev_session_completion(
                session=completed_pm,
                agent_session=dev,
                result="PLAN complete. Critique stage next.",
            )

        # Continuation PM must be created — steer was accepted but PM is terminal
        pm_children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=completed_pm.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(pm_children) >= 1
        cont = pm_children[0]
        assert cont.status == "pending"
        assert "CONTINUATION" in cont.message_text


# ---------------------------------------------------------------------------
# Test 6: Path B — agent_session is None, uses session.parent_agent_session_id
# ---------------------------------------------------------------------------


class TestHandleCompletionPathBFallback:
    """Tests for the Path B fix (issue #987 Fix 2).

    When agent_session is None (status='running' filter returned nothing due to a
    race with health-check recovery or fast finalization), _handle_dev_session_completion
    must fall back to session.parent_agent_session_id rather than returning silently.
    """

    @pytest.mark.asyncio
    async def test_agent_session_none_uses_session_parent_id(self, redis_test_db):
        """When agent_session is None, session.parent_agent_session_id creates continuation PM.

        Before fix 2, agent_session=None caused an early return with no continuation PM.
        After fix 2, the outer session object's parent_agent_session_id is used as fallback.
        """
        from agent.agent_session_queue import _handle_dev_session_completion

        # Parent is in terminal status
        terminal_pm_path_b = AgentSession.create(
            session_id="pm-path-b-001",
            session_type="pm",
            project_key="test",
            working_dir="/tmp",
            status="completed",
            chat_id="999",
            message_text="Run SDLC on issue #987 (issues/987)",
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )
        # The outer session object (populated from queue entry) has parent_agent_session_id set
        outer_session = AgentSession.create(
            session_id="dev-path-b-001",
            session_type="dev",
            project_key="test",
            status="completed",
            chat_id="999",
            message_text="Stage: PLAN",
            parent_agent_session_id=terminal_pm_path_b.agent_session_id,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        with (
            patch(
                "agent.agent_session_queue.steer_session",
                return_value={
                    "success": False,
                    "error": "Session is in terminal status 'completed' — steering rejected",
                },
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=987),
        ):
            # agent_session=None simulates the status="running" filter returning nothing
            await _handle_dev_session_completion(
                session=outer_session,
                agent_session=None,
                result="PLAN complete.",
            )

        # Continuation PM must be created via session.parent_agent_session_id fallback
        pm_children = [
            c
            for c in AgentSession.query.filter(
                parent_agent_session_id=terminal_pm_path_b.agent_session_id
            )
            if c.session_type == "pm"
        ]
        assert len(pm_children) >= 1
        cont = pm_children[0]
        assert cont.status == "pending"
        assert "CONTINUATION" in cont.message_text
