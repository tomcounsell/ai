"""E2E tests for hook-driven Dev session spawning and completion.

Tests the full lifecycle: PreToolUse creates a Dev session in Redis with
parent linkage → SubagentStop marks it completed → status guards work.

Uses real Redis, mocks only the Claude API boundary.
"""

import os
import time

import pytest

from models.agent_session import AgentSession


@pytest.mark.e2e
class TestDevCreation:
    """Verify PreToolUse hook logic creates Dev sessions correctly."""

    def test_dev_session_created_with_parent_linkage(self):
        """Simulating PreToolUse: create_child with parent_session_id."""
        ts = int(time.time())
        parent_session_id = f"chat_parent_{ts}"

        # Create a parent PM session first
        parent = AgentSession.create_pm(
            session_id=parent_session_id,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="test_chat",
            telegram_message_id=1,
            message_text="run /do-build",
        )
        parent.status = "running"
        parent.save()

        # Simulate what PreToolUse hook does
        dev = AgentSession.create_dev(
            session_id=f"dev-{parent_session_id}",
            project_key="default",
            working_dir="/tmp/test",
            parent_session_id=parent_session_id,
            message_text="dev-session build task",
        )

        # Verify Dev session exists in Redis with correct linkage
        assert dev.is_dev
        assert dev.parent_session_id == parent_session_id
        assert dev.status == "pending"

        # Verify we can find it via parent lookup
        dev_sessions = list(AgentSession.query.filter(parent_agent_session_id=parent_session_id))
        assert len(dev_sessions) >= 1
        found = [d for d in dev_sessions if d.session_id == f"dev-{parent_session_id}"]
        assert len(found) == 1

    def test_dev_session_skipped_without_session_id(self):
        """Without VALOR_SESSION_ID, _maybe_register_dev_session does nothing."""
        from agent.hooks.pre_tool_use import _maybe_register_dev_session

        # Clear VALOR_SESSION_ID if set
        old_val = os.environ.pop("VALOR_SESSION_ID", None)
        try:
            # Should not raise, should silently skip
            _maybe_register_dev_session({"type": "dev-session", "prompt": "test"})
        finally:
            if old_val:
                os.environ["VALOR_SESSION_ID"] = old_val


@pytest.mark.e2e
class TestDevCompletion:
    """Verify SubagentStop hook logic marks Dev sessions completed."""

    def test_subagent_stop_completes_dev_session(self):
        """SubagentStop should transition running Dev session to completed.

        Exercises the two-lookup pattern (issue #808):
        1. Resolve bridge session_id from Claude UUID via session_registry.
        2. Look up parent AgentSession to get agent_session_id UUID.
        3. Query children by that UUID (parent_agent_session_id = agent_session_id UUID).
        """
        from agent.hooks.session_registry import _reset_for_testing, register_pending
        from agent.hooks.subagent_stop import _register_dev_session_completion

        ts = int(time.time())
        parent_sid = f"parent_completion_{ts}"
        fake_claude_uuid = f"fake-claude-{ts}"

        # Create parent PM session and register it in session registry
        parent = AgentSession.create_pm(
            session_id=parent_sid,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            telegram_message_id=1,
            message_text="build",
        )
        parent_agent_uuid = parent.agent_session_id

        _reset_for_testing()
        register_pending(parent_sid)
        # Activate registry mapping: claude_uuid → bridge session_id
        from agent.hooks import session_registry

        session_registry._registry[fake_claude_uuid] = parent_sid

        # Create a running local-* Dev session linked via agent_session_id UUID
        # (simulating child subprocess self-registration via VALOR_PARENT_SESSION_ID)
        dev = AgentSession.create_local(
            session_id=f"local-{parent_sid}",
            project_key="default",
            working_dir="/tmp/test",
            parent_agent_session_id=parent_agent_uuid,
            message_text="building",
            status="running",
        )

        # Simulate SubagentStop hook with two-lookup pattern
        _register_dev_session_completion("agent-123", claude_uuid=fake_claude_uuid)

        _reset_for_testing()

        # Verify the local-* child session was completed
        completed = list(
            AgentSession.query.filter(session_id=f"local-{parent_sid}", status="completed")
        )
        assert len(completed) >= 1

    def test_status_guard_failed_not_overwritten(self):
        """A 'failed' Dev session should not be overwritten to 'completed'."""
        from agent.hooks.session_registry import _reset_for_testing, register_pending
        from agent.hooks.subagent_stop import _register_dev_session_completion

        ts = int(time.time())
        parent_sid = f"parent_guard_{ts}"
        fake_claude_uuid = f"fake-claude-guard-{ts}"

        # Create parent PM session and register it in session registry
        parent = AgentSession.create_pm(
            session_id=parent_sid,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            telegram_message_id=1,
            message_text="build",
        )
        parent_agent_uuid = parent.agent_session_id

        _reset_for_testing()
        register_pending(parent_sid)
        from agent.hooks import session_registry

        session_registry._registry[fake_claude_uuid] = parent_sid

        # Create a failed local-* session linked via agent_session_id UUID
        dev = AgentSession.create_local(
            session_id=f"local-guard-{parent_sid}",
            project_key="default",
            working_dir="/tmp/test",
            parent_agent_session_id=parent_agent_uuid,
            message_text="building",
            status="failed",
        )

        # SubagentStop should NOT overwrite failed → completed
        _register_dev_session_completion("agent-456", claude_uuid=fake_claude_uuid)

        _reset_for_testing()

        # Should still be failed, not overwritten to completed
        failed = list(
            AgentSession.query.filter(session_id=f"local-guard-{parent_sid}", status="failed")
        )
        completed = list(
            AgentSession.query.filter(session_id=f"local-guard-{parent_sid}", status="completed")
        )
        assert len(failed) >= 1
        assert len(completed) == 0

    def test_multiple_dev_sessions_under_one_parent(self):
        """Multiple Dev sessions can exist under one parent PM session.

        Uses the VALOR_PARENT_SESSION_ID env var approach (issue #808):
        child local-* sessions store the parent's agent_session_id UUID in
        parent_agent_session_id, not the bridge session_id.
        """
        from agent.hooks.session_registry import _reset_for_testing, register_pending

        ts = int(time.time())
        parent_sid = f"parent_multi_{ts}"

        parent = AgentSession.create_pm(
            session_id=parent_sid,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            telegram_message_id=1,
            message_text="complex build",
        )
        parent_agent_uuid = parent.agent_session_id

        # Register parent in session registry (as the bridge does)
        _reset_for_testing()
        register_pending(parent_sid)

        # Create child local-* sessions with parent linked via agent_session_id UUID
        # (simulating child subprocess self-registration via VALOR_PARENT_SESSION_ID)
        for i in range(3):
            dev = AgentSession.create_local(
                session_id=f"local-{parent_sid}-{i}",
                project_key="default",
                working_dir="/tmp/test",
                parent_agent_session_id=parent_agent_uuid,
                message_text=f"subtask {i}",
                status="running",
            )

        # All should be findable by parent's agent_session_id UUID
        all_devs = list(AgentSession.query.filter(parent_agent_session_id=parent_agent_uuid))
        assert len(all_devs) >= 3

        # Complete them via the hook (uses two-lookup pattern: session_id → agent_session_id → children)
        from agent.hooks.subagent_stop import _register_dev_session_completion

        FAKE_UUID = f"fake-claude-uuid-{ts}"
        _register_dev_session_completion("agent-multi", claude_uuid=FAKE_UUID)

        # All running devs should now be completed — check each by session_id
        for i in range(3):
            completed = list(
                AgentSession.query.filter(session_id=f"local-{parent_sid}-{i}", status="completed")
            )
            assert len(completed) >= 1, f"local-{parent_sid}-{i} should be completed"

        _reset_for_testing()
