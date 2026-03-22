"""E2E tests for hook-driven DevSession spawning and completion.

Tests the full lifecycle: PreToolUse creates DevSession in Redis with
parent linkage → SubagentStop marks it completed → status guards work.

Uses real Redis, mocks only the Claude API boundary.
"""

import os
import time

import pytest

from models.agent_session import AgentSession


@pytest.mark.e2e
class TestDevSessionCreation:
    """Verify PreToolUse hook logic creates DevSessions correctly."""

    def test_dev_session_created_with_parent_linkage(self):
        """Simulating PreToolUse: create_dev with parent_chat_session_id."""
        ts = int(time.time())
        parent_session_id = f"chat_parent_{ts}"

        # Create a parent ChatSession first
        parent = AgentSession.create_chat(
            session_id=parent_session_id,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="test_chat",
            message_id=1,
            message_text="run /do-build",
        )
        parent.status = "running"
        parent.save()

        # Simulate what PreToolUse hook does
        dev = AgentSession.create_dev(
            session_id=f"dev-{parent_session_id}",
            project_key="default",
            working_dir="/tmp/test",
            parent_chat_session_id=parent_session_id,
            message_text="dev-session build task",
        )

        # Verify DevSession exists in Redis with correct linkage
        assert dev.is_dev
        assert dev.parent_chat_session_id == parent_session_id
        assert dev.status == "pending"

        # Verify we can find it via parent lookup
        dev_sessions = list(AgentSession.query.filter(parent_chat_session_id=parent_session_id))
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
class TestDevSessionCompletion:
    """Verify SubagentStop hook logic marks DevSessions completed."""

    def test_subagent_stop_completes_dev_session(self):
        """SubagentStop should transition running DevSession to completed."""
        ts = int(time.time())
        parent_sid = f"parent_completion_{ts}"

        # Create parent ChatSession
        AgentSession.create_chat(
            session_id=parent_sid,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            message_id=1,
            message_text="build",
        )

        # Create a running DevSession
        dev = AgentSession.create_dev(
            session_id=f"dev-{parent_sid}",
            project_key="default",
            working_dir="/tmp/test",
            parent_chat_session_id=parent_sid,
            message_text="building",
        )
        dev.status = "running"
        dev.save()

        # Simulate what SubagentStop hook does
        from agent.hooks.subagent_stop import _register_dev_session_completion

        old_val = os.environ.get("VALOR_SESSION_ID")
        os.environ["VALOR_SESSION_ID"] = parent_sid
        try:
            _register_dev_session_completion("agent-123")
        finally:
            if old_val:
                os.environ["VALOR_SESSION_ID"] = old_val
            else:
                os.environ.pop("VALOR_SESSION_ID", None)

        # Reload and verify — Popoto keeps old status records as KeyField,
        # so filter for the specific completed status
        completed = list(
            AgentSession.query.filter(session_id=f"dev-{parent_sid}", status="completed")
        )
        assert len(completed) >= 1

    def test_status_guard_failed_not_overwritten(self):
        """A 'failed' DevSession should not be overwritten to 'completed'."""
        ts = int(time.time())
        parent_sid = f"parent_guard_{ts}"

        AgentSession.create_chat(
            session_id=parent_sid,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            message_id=1,
            message_text="build",
        )

        dev = AgentSession.create_dev(
            session_id=f"dev-{parent_sid}",
            project_key="default",
            working_dir="/tmp/test",
            parent_chat_session_id=parent_sid,
            message_text="building",
        )
        dev.status = "failed"
        dev.save()

        # SubagentStop should NOT overwrite failed → completed
        from agent.hooks.subagent_stop import _register_dev_session_completion

        old_val = os.environ.get("VALOR_SESSION_ID")
        os.environ["VALOR_SESSION_ID"] = parent_sid
        try:
            _register_dev_session_completion("agent-456")
        finally:
            if old_val:
                os.environ["VALOR_SESSION_ID"] = old_val
            else:
                os.environ.pop("VALOR_SESSION_ID", None)

        # Should still be failed, not overwritten to completed
        failed = list(AgentSession.query.filter(session_id=f"dev-{parent_sid}", status="failed"))
        completed = list(
            AgentSession.query.filter(session_id=f"dev-{parent_sid}", status="completed")
        )
        assert len(failed) >= 1
        assert len(completed) == 0

    def test_multiple_dev_sessions_under_one_parent(self):
        """Multiple DevSessions can exist under one parent ChatSession."""
        ts = int(time.time())
        parent_sid = f"parent_multi_{ts}"

        AgentSession.create_chat(
            session_id=parent_sid,
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="tc",
            message_id=1,
            message_text="complex build",
        )

        # Create multiple dev sessions
        for i in range(3):
            dev = AgentSession.create_dev(
                session_id=f"dev-{parent_sid}-{i}",
                project_key="default",
                working_dir="/tmp/test",
                parent_chat_session_id=parent_sid,
                message_text=f"subtask {i}",
            )
            dev.status = "running"
            dev.save()

        # All should be findable by parent
        all_devs = list(AgentSession.query.filter(parent_chat_session_id=parent_sid))
        assert len(all_devs) >= 3

        # Complete them
        from agent.hooks.subagent_stop import _register_dev_session_completion

        old_val = os.environ.get("VALOR_SESSION_ID")
        os.environ["VALOR_SESSION_ID"] = parent_sid
        try:
            _register_dev_session_completion("agent-multi")
        finally:
            if old_val:
                os.environ["VALOR_SESSION_ID"] = old_val
            else:
                os.environ.pop("VALOR_SESSION_ID", None)

        # All running devs should now be completed — check each by session_id
        for i in range(3):
            completed = list(
                AgentSession.query.filter(session_id=f"dev-{parent_sid}-{i}", status="completed")
            )
            assert len(completed) >= 1, f"dev-{parent_sid}-{i} should be completed"
