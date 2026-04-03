"""E2E tests for per-chat queue isolation.

Verifies that different chat groups get independent serial queues
and that messages from one chat never bleed into another.
Uses real Redis via conftest redis_test_db.
"""

import time

import pytest

from models.agent_session import AgentSession


@pytest.mark.e2e
class TestPerChatQueueIsolation:
    """Verify that jobs from different chats are fully isolated."""

    def test_sessions_from_different_chats_are_independent(self):
        """Two chats creating sessions should not interfere."""
        AgentSession.create_pm(
            session_id=f"iso_a_{int(time.time())}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="chat_111",
            telegram_message_id=1,
            message_text="Hello from chat A",
            sender_name="Alice",
        )
        AgentSession.create_pm(
            session_id=f"iso_b_{int(time.time())}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="chat_222",
            telegram_message_id=2,
            message_text="Hello from chat B",
            sender_name="Bob",
        )

        # Each chat_id query returns only its own session
        a_sessions = list(AgentSession.query.filter(chat_id="chat_111"))
        b_sessions = list(AgentSession.query.filter(chat_id="chat_222"))

        assert len(a_sessions) == 1
        assert len(b_sessions) == 1
        assert a_sessions[0].sender_name == "Alice"
        assert b_sessions[0].sender_name == "Bob"
        assert a_sessions[0].agent_session_id != b_sessions[0].agent_session_id

    def test_steering_messages_isolated_between_sessions(self):
        """Steering messages pushed to one session must not appear in another."""
        s1 = AgentSession.create_pm(
            session_id=f"steer_a_{int(time.time())}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="chat_333",
            telegram_message_id=10,
            message_text="msg1",
        )
        s2 = AgentSession.create_pm(
            session_id=f"steer_b_{int(time.time())}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="chat_444",
            telegram_message_id=11,
            message_text="msg2",
        )

        s1.push_steering_message("fix the bug")
        s2.push_steering_message("add the feature")

        # Reload from Redis
        s1_reloaded = list(AgentSession.query.filter(session_id=s1.session_id))[0]
        s2_reloaded = list(AgentSession.query.filter(session_id=s2.session_id))[0]

        msgs1 = s1_reloaded.pop_steering_messages()
        msgs2 = s2_reloaded.pop_steering_messages()

        assert msgs1 == ["fix the bug"]
        assert msgs2 == ["add the feature"]

    def test_same_project_different_chats_are_parallel(self):
        """Two chats for the same project should create independent sessions."""
        ts = int(time.time())
        AgentSession.create_pm(
            session_id=f"par_a_{ts}",
            project_key="shared_project",
            working_dir="/tmp/test",
            chat_id="group_aaa",
            telegram_message_id=100,
            message_text="task 1",
        )
        AgentSession.create_pm(
            session_id=f"par_b_{ts}",
            project_key="shared_project",
            working_dir="/tmp/test",
            chat_id="group_bbb",
            telegram_message_id=101,
            message_text="task 2",
        )

        # Both exist independently
        all_project = list(AgentSession.query.filter(project_key="shared_project"))
        assert len(all_project) == 2

        chat_ids = {s.chat_id for s in all_project}
        assert "group_aaa" in chat_ids
        assert "group_bbb" in chat_ids

    def test_dedup_scoped_to_chat(self):
        """Message dedup should be per-chat, not global."""
        ts = int(time.time())
        # Same message_id in two different chats — both should be created
        s1 = AgentSession.create_pm(
            session_id=f"dedup_a_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="dedup_chat_1",
            telegram_message_id=999,
            message_text="same message",
        )
        s2 = AgentSession.create_pm(
            session_id=f"dedup_b_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="dedup_chat_2",
            telegram_message_id=999,
            message_text="same message",
        )

        assert s1.agent_session_id != s2.agent_session_id
        assert s1.chat_id != s2.chat_id
