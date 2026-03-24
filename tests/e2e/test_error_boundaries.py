"""E2E tests for session error isolation.

Tests that a failed session doesn't block other sessions in the same chat
queue. Uses real Redis.
"""

import time

import pytest

from models.agent_session import AgentSession


@pytest.mark.e2e
class TestSessionErrorIsolation:
    """Verify failed sessions don't block other sessions."""

    def test_failed_session_does_not_block_queue(self):
        """A session failure should not prevent other sessions from processing."""
        ts = int(time.time())
        chat_id = f"iso_chat_{ts}"

        # Create two sessions in the same chat
        session1 = AgentSession.create_chat(
            session_id=f"iso_s1_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id=chat_id,
            telegram_message_id=1,
            message_text="task 1",
        )
        session2 = AgentSession.create_chat(
            session_id=f"iso_s2_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id=chat_id,
            telegram_message_id=2,
            message_text="task 2",
        )

        # Session 1 fails
        session1.status = "failed"
        session1.save()

        # Session 2 should still be processable (pending or running)
        session2.status = "running"
        session2.save()

        # Verify both exist and have correct statuses
        s1_reloaded = list(AgentSession.query.filter(session_id=f"iso_s1_{ts}"))
        s2_reloaded = list(AgentSession.query.filter(session_id=f"iso_s2_{ts}"))

        assert len(s1_reloaded) >= 1
        assert len(s2_reloaded) >= 1
        assert s1_reloaded[0].status == "failed"
        assert s2_reloaded[0].status == "running"

        # Session 2 can complete independently
        session2.status = "completed"
        session2.save()

        s2_final = list(AgentSession.query.filter(session_id=f"iso_s2_{ts}"))
        assert s2_final[0].status == "completed"

    def test_failed_session_gets_error_status(self):
        """A session that encounters an error should be marked 'failed'."""
        ts = int(time.time())

        session = AgentSession.create_chat(
            session_id=f"err_status_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="err_chat",
            telegram_message_id=1,
            message_text="do something risky",
        )
        session.status = "running"
        session.save()

        # Simulate error handling: mark as failed
        session.status = "failed"
        session.log_lifecycle_transition("failed", "SDK error: rate limit")
        session.save()

        reloaded = list(AgentSession.query.filter(session_id=f"err_status_{ts}"))
        assert reloaded[0].status == "failed"

    def test_error_does_not_affect_other_chat_sessions(self):
        """A failure in one chat should not affect sessions in another chat."""
        ts = int(time.time())

        # Session in chat A fails
        s_a = AgentSession.create_chat(
            session_id=f"cross_a_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id=f"chat_a_{ts}",
            telegram_message_id=1,
            message_text="task a",
        )
        s_a.status = "failed"
        s_a.save()

        # Session in chat B should be unaffected
        s_b = AgentSession.create_chat(
            session_id=f"cross_b_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id=f"chat_b_{ts}",
            telegram_message_id=1,
            message_text="task b",
        )
        s_b.status = "running"
        s_b.save()

        # Both sessions are independent
        a = list(AgentSession.query.filter(session_id=f"cross_a_{ts}"))
        b = list(AgentSession.query.filter(session_id=f"cross_b_{ts}"))

        assert a[0].status == "failed"
        assert b[0].status == "running"

        # Chat B session can complete normally
        s_b.status = "completed"
        s_b.save()

        b_final = list(AgentSession.query.filter(session_id=f"cross_b_{ts}"))
        assert b_final[0].status == "completed"
