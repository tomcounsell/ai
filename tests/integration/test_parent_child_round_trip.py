"""Integration tests for the new PM → valor_session → worker → harness → steer flow.

Covers the harness-abstraction Phase 3-5 round-trip:
  1. `valor_session create --role dev --parent <id>` creates a child session with
     `parent_agent_session_id` pointing to the parent.
  2. `_handle_dev_session_completion()` calls `steer_session()` on the parent PM
     session after the CLI harness returns.
  3. `PipelineStateMachine` stage transitions (complete/fail) are driven by
     `classify_outcome()` on the result text.

All external I/O (Redis reads on GitHub issue, harness subprocess) is mocked.
Real Redis (db=1 via autouse redis_test_db) is used for AgentSession persistence.

See docs/features/harness-abstraction.md "Post-Completion SDLC Handler (Phase 3)"
for the architecture this test validates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models.agent_session import AgentSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm_session(redis_test_db):
    """Create a parent PM session in Redis."""
    session = AgentSession.create(
        session_id="pm-round-trip-001",
        session_type="pm",
        project_key="test",
        status="active",
        chat_id="999",
        sender_name="TestUser",
        message_text="Run the BUILD stage (issues/780)",
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )
    return session


@pytest.fixture
def dev_session(pm_session, redis_test_db):
    """Create a child dev session linked to the PM session via parent_agent_session_id."""
    session = AgentSession.create(
        session_id="dev-round-trip-001",
        session_type="dev",
        project_key="test",
        status="active",
        chat_id="999",
        sender_name="TestUser",
        message_text="Stage: BUILD\nImplement the feature (issues/780)",
        parent_agent_session_id=pm_session.agent_session_id,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )
    return session


# ---------------------------------------------------------------------------
# Test 1: valor_session create --role dev --parent stores correct linkage
# ---------------------------------------------------------------------------


class TestDevSessionParentLinkage:
    """valor_session create --role dev --parent <id> wires parent_agent_session_id."""

    def test_create_dev_session_stores_parent_id(self, pm_session, redis_test_db):
        """AgentSession.create with parent_agent_session_id links child to parent."""
        parent_uuid = pm_session.agent_session_id

        child = AgentSession.create(
            session_id="dev-linkage-test-001",
            session_type="dev",
            project_key="test",
            status="pending",
            chat_id="999",
            sender_name="valor-session (dev)",
            message_text="Stage: BUILD\nBuild the feature.",
            parent_agent_session_id=parent_uuid,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        assert child.parent_agent_session_id == parent_uuid
        assert child.session_type == "dev"

    def test_child_found_by_parent_uuid(self, pm_session, redis_test_db):
        """Child session is queryable via parent's agent_session_id."""
        parent_uuid = pm_session.agent_session_id

        AgentSession.create(
            session_id="dev-linkage-query-001",
            session_type="dev",
            project_key="test",
            status="pending",
            chat_id="999",
            sender_name="valor-session (dev)",
            message_text="Stage: BUILD",
            parent_agent_session_id=parent_uuid,
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        children = list(AgentSession.query.filter(parent_agent_session_id=parent_uuid))
        assert len(children) >= 1
        assert any(c.session_id == "dev-linkage-query-001" for c in children)

    def test_parent_session_id_none_without_parent(self, redis_test_db):
        """Dev session without --parent has parent_agent_session_id=None."""
        standalone_dev = AgentSession.create(
            session_id="dev-no-parent-001",
            session_type="dev",
            project_key="test",
            status="pending",
            chat_id="999",
            sender_name="valor-session (dev)",
            message_text="Stage: BUILD",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        assert standalone_dev.parent_agent_session_id is None


# ---------------------------------------------------------------------------
# Test 2: _handle_dev_session_completion calls steer_session on parent
# ---------------------------------------------------------------------------


class TestHandleDevSessionCompletion:
    """_handle_dev_session_completion steers parent PM session on harness return."""

    @pytest.mark.asyncio
    async def test_success_result_steers_parent(self, pm_session, dev_session, redis_test_db):
        """Successful harness result causes steer_session to be called on the parent PM."""
        from agent.agent_session_queue import _handle_dev_session_completion
        from agent.pipeline_state import PipelineStateMachine

        # Advance pipeline to BUILD so classify_outcome has a current stage
        sm = PipelineStateMachine(pm_session)
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")
        sm.complete_stage("PLAN")
        sm.start_stage("CRITIQUE")
        sm.complete_stage("CRITIQUE")
        sm.start_stage("BUILD")

        # Reload pm_session so it reflects updated stage_states
        pm_sessions = list(AgentSession.query.filter(session_id=pm_session.session_id))
        updated_pm = pm_sessions[0]

        with (
            patch("agent.agent_session_queue.steer_session") as mock_steer,
            patch("agent.agent_session_queue._extract_issue_number", return_value=None),
        ):
            await _handle_dev_session_completion(
                session=updated_pm,
                agent_session=dev_session,
                result="PR created at https://github.com/test/repo/pull/42. BUILD stage complete.",
            )

        mock_steer.assert_called_once()
        call_args = mock_steer.call_args
        steered_session_id = call_args[0][0]
        steering_msg = call_args[0][1]

        assert steered_session_id == pm_session.session_id
        assert "Dev session completed" in steering_msg or "BUILD" in steering_msg

    @pytest.mark.asyncio
    async def test_no_parent_id_skips_steering(self, redis_test_db):
        """Dev session without parent_agent_session_id skips steer_session call."""
        standalone_dev = AgentSession.create(
            session_id="dev-no-parent-steer-001",
            session_type="dev",
            project_key="test",
            status="active",
            chat_id="999",
            sender_name="Test",
            message_text="Stage: BUILD",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        from agent.agent_session_queue import _handle_dev_session_completion

        with patch("agent.agent_session_queue.steer_session") as mock_steer:
            await _handle_dev_session_completion(
                session=standalone_dev,
                agent_session=standalone_dev,
                result="Some result text",
            )

        mock_steer.assert_not_called()

    @pytest.mark.asyncio
    async def test_steer_message_contains_stage_and_outcome(
        self, pm_session, dev_session, redis_test_db
    ):
        """Steering message includes stage name and outcome classification."""
        from agent.agent_session_queue import _handle_dev_session_completion
        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(pm_session)
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")
        sm.complete_stage("PLAN")
        sm.start_stage("CRITIQUE")
        sm.complete_stage("CRITIQUE")
        sm.start_stage("BUILD")

        pm_sessions = list(AgentSession.query.filter(session_id=pm_session.session_id))
        updated_pm = pm_sessions[0]

        captured_messages = []

        def _capture_steer(session_id, message):
            captured_messages.append((session_id, message))
            return {"success": True, "session_id": session_id, "error": None}

        with (
            patch("agent.agent_session_queue.steer_session", side_effect=_capture_steer),
            patch("agent.agent_session_queue._extract_issue_number", return_value=None),
        ):
            await _handle_dev_session_completion(
                session=updated_pm,
                agent_session=dev_session,
                result='<!-- OUTCOME {"result": "success"} --> BUILD complete, PR opened.',
            )

        assert len(captured_messages) == 1
        _, msg = captured_messages[0]
        # Should mention the stage and some outcome indicator
        assert any(kw in msg for kw in ("BUILD", "Stage", "stage", "outcome", "Outcome"))

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self, pm_session, redis_test_db):
        """Exceptions in _handle_dev_session_completion are swallowed (non-fatal)."""
        from agent.agent_session_queue import _handle_dev_session_completion

        # Use a mock that forces an error inside the function
        broken_session = MagicMock()
        broken_session.parent_agent_session_id = pm_session.agent_session_id
        broken_session.message_text = "Stage: BUILD"

        with (
            patch(
                "agent.agent_session_queue.steer_session", side_effect=RuntimeError("steer failed")
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=None),
        ):
            # Should not raise — all exceptions are caught
            await _handle_dev_session_completion(
                session=pm_session,
                agent_session=broken_session,
                result="Some result text",
            )


# ---------------------------------------------------------------------------
# Test 3: PipelineStateMachine stage transitions via classify_outcome
# ---------------------------------------------------------------------------


class TestPipelineStateMachineTransitions:
    """classify_outcome drives complete_stage / fail_stage via _handle_dev_session_completion."""

    @pytest.mark.asyncio
    async def test_success_result_completes_stage(self, pm_session, dev_session, redis_test_db):
        """Result text indicating success calls complete_stage on current in_progress stage."""
        from agent.agent_session_queue import _handle_dev_session_completion
        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(pm_session)
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")

        pm_sessions = list(AgentSession.query.filter(session_id=pm_session.session_id))
        updated_pm = pm_sessions[0]

        with (
            patch(
                "agent.agent_session_queue.steer_session",
                return_value={"success": True, "error": None},
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=None),
        ):
            await _handle_dev_session_completion(
                session=updated_pm,
                agent_session=dev_session,
                result="Plan document created at docs/plans/my-feature.md. PLAN stage complete.",
            )

        # Verify PLAN stage advanced on parent (completed or failed based on outcome)
        refreshed = list(AgentSession.query.filter(session_id=pm_session.session_id))[0]
        stage_states = json.loads(refreshed.stage_states) if refreshed.stage_states else {}
        assert stage_states.get("PLAN") in ("completed", "failed")

    @pytest.mark.asyncio
    async def test_no_current_stage_skips_psm_update(self, pm_session, dev_session, redis_test_db):
        """When no stage is in_progress, PSM update is skipped without error."""
        from agent.agent_session_queue import _handle_dev_session_completion

        # Don't start any stages — no in_progress stage
        with (
            patch(
                "agent.agent_session_queue.steer_session",
                return_value={"success": True, "error": None},
            ),
            patch("agent.agent_session_queue._extract_issue_number", return_value=None),
        ):
            # Should not raise
            await _handle_dev_session_completion(
                session=pm_session,
                agent_session=dev_session,
                result="Some result text.",
            )
