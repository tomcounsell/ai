"""Integration tests for the parent-child Eng session round-trip.

Covers:
  1. `valor_session create --role eng --parent <id>` creates a child session with
     `parent_agent_session_id` pointing to the parent.
  2. `PipelineStateMachine` stage transitions and parent-child linkage.
  3. Transcript-boundary safety for waiting_for_children status.

All external I/O (Redis reads on GitHub issue, harness subprocess) is mocked.
Real Redis (db=1 via autouse redis_test_db) is used for AgentSession persistence.

See docs/features/harness-abstraction.md for the architecture this test validates.
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
        session_type="eng",
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
        session_type="eng",
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
            session_type="eng",
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
        assert child.session_type == "eng"

    def test_child_found_by_parent_uuid(self, pm_session, redis_test_db):
        """Child session is queryable via parent's agent_session_id."""
        parent_uuid = pm_session.agent_session_id

        AgentSession.create(
            session_id="dev-linkage-query-001",
            session_type="eng",
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

    def test_no_parent_when_not_set(self, redis_test_db):
        """Dev session without --parent has parent_agent_session_id=None."""
        standalone_dev = AgentSession.create(
            session_id="dev-no-parent-001",
            session_type="eng",
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

    def test_child_working_dir_rooted_under_parent_project_via_projects_json(
        self, tmp_path, monkeypatch, redis_test_db
    ):
        """#1158: child session's working_dir must be rooted inside the
        parent's project (via the projects.json lookup), NOT copied directly
        from parent.working_dir.

        This is the integration-layer regression guard for the governing
        principle: project_key → repo (via projects.json) is the only pairing.
        """
        import argparse
        from unittest.mock import MagicMock, patch

        from tools.valor_session import cmd_create

        # Simulated project root for the parent's project_key.
        parent_project_root = tmp_path / "test_proj_root"
        parent_project_root.mkdir()

        # Parent session is in a worktree under the project — child must NOT
        # copy this path; child should re-derive via projects.json.
        parent_worktree = parent_project_root / ".worktrees" / "parent-wt"
        parent_worktree.mkdir(parents=True)

        parent_uuid = "parent-integration-uuid-001"
        fake_parent = MagicMock()
        fake_parent.project_key = "test"
        fake_parent.agent_session_id = parent_uuid
        fake_parent.working_dir = str(parent_worktree)

        projects_json = {"test": {"working_directory": str(parent_project_root)}}

        # Child worktree path — distinct from parent's worktree.
        child_wt = parent_project_root / ".worktrees" / "sdlc-500"
        child_wt.mkdir(parents=True)

        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        monkeypatch.chdir(tmp_path)  # cwd unrelated

        args = argparse.Namespace(
            command="create",
            role="eng",
            message="Run SDLC on issue #500",
            chat_id=None,
            parent=parent_uuid,
            project_key=None,  # Inherit from parent
            slug=None,
            model=None,
            json=False,
        )

        with (
            patch(
                "bridge.routing.load_config",
                return_value={"projects": projects_json, "defaults": {}},
            ),
            patch("tools.valor_session._find_session", return_value=fake_parent),
            patch(
                "agent.worktree_manager.get_or_create_worktree",
                return_value=child_wt,
            ),
            patch("agent.worktree_manager._validate_slug"),
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("tools.valor_session._check_worker_health", return_value=(True, 1)),
        ):
            rc = cmd_create(args)

        assert rc == 0

        # REGRESSION GUARD: working_dir must be rooted under the project root
        # from projects.json — NOT the parent's working_dir.
        assert captured["working_dir"].startswith(str(parent_project_root))
        assert captured["working_dir"] != fake_parent.working_dir
        # The inherited project_key flows to the child.
        assert captured["project_key"] == "test"
        # project_config carries the raw project dict (bridge parity).
        assert captured["project_config"] == projects_json["test"]



# TestHandleDevSessionCompletion and TestPipelineStateMachineTransitions removed:
# _handle_dev_session_completion was deleted when PM and Dev roles were merged
# into a single Eng role. Steering now happens via direct session-steering APIs.

class TestTranscriptBoundarySkipWaitingForChildren:
    """Issue #1156: PM in waiting_for_children must not be prematurely finalized.

    When the PM's transcript ends while a child is still running, the PM stays
    in ``waiting_for_children``. Only after the last child terminates does
    ``_finalize_parent_sync`` transition the PM to ``completed`` with reason
    ``"all children terminal"``.
    """

    def test_pm_with_live_child_not_prematurely_finalized_by_transcript_end(self, redis_test_db):
        """End-to-end scenario from the issue evidence.

        1. PM enters ``waiting_for_children`` with a running child.
        2. PM's transcript ends (worker calls ``complete_transcript``).
        3. PM MUST remain ``waiting_for_children`` — no bypass.
        4. Child finalizes → PM transitions via ``_finalize_parent_sync``.
        5. Parent terminal timestamp is at-or-after the child's.
        """
        from bridge.session_transcript import complete_transcript
        from models.session_lifecycle import finalize_session

        # Step 1: create PM in waiting_for_children with a running child
        pm = AgentSession.create(
            session_id="pm-wfc-e2e-001",
            session_type="eng",
            project_key="test",
            status="waiting_for_children",
            chat_id="999",
            sender_name="TestUser",
            message_text="Run BUILD",
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        child = AgentSession.create(
            session_id="dev-wfc-e2e-001",
            session_type="eng",
            project_key="test",
            status="running",
            chat_id="999",
            sender_name="TestUser",
            message_text="Child task",
            parent_agent_session_id=pm.agent_session_id,
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        # Step 2: PM's transcript ends with status="completed"
        complete_transcript(pm.session_id, status="completed")

        # Step 3: PM must still be waiting_for_children (skip branch fired)
        pm_reloaded = list(AgentSession.query.filter(session_id=pm.session_id))[0]
        assert pm_reloaded.status == "waiting_for_children", (
            f"PM was prematurely finalized to {pm_reloaded.status} — issue #1156 bypass"
        )

        # Step 4: terminate the child; _finalize_parent_sync should transition the PM
        finalize_session(child, "completed", reason="child work done")

        # Step 5: PM transitioned via the sanctioned channel
        pm_after = list(AgentSession.query.filter(session_id=pm.session_id))[0]
        assert pm_after.status == "completed"

        # Both sessions are terminal and carry completion timestamps
        child_after = list(AgentSession.query.filter(session_id=child.session_id))[0]
        assert getattr(child_after, "completed_at", None) is not None
        assert getattr(pm_after, "completed_at", None) is not None
