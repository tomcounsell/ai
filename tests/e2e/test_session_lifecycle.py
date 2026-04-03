"""E2E tests for message-to-session lifecycle.

Verifies the full flow: incoming message → session creation → processing
→ response delivery. Mocks at system boundaries (Telegram API, Claude API)
while exercising real internal wiring and Redis.
"""

import time

import pytest

from bridge.dedup import is_duplicate_message, record_message_processed
from models.agent_session import AgentSession


@pytest.mark.e2e
class TestSessionCreationFromMessage:
    """Verify that incoming messages create the correct session type."""

    def test_chat_session_created_with_correct_fields(self, make_telegram_event):
        """Simulates bridge handler creating a ChatSession from a Telegram event."""
        event = make_telegram_event(
            chat_id=-1001111111,
            message_id=101,
            text="build feature X",
            sender_first_name="Tom",
            sender_id=12345,
            chat_title="Dev: Valor",
        )

        ts = int(time.time())
        session = AgentSession.create_pm(
            session_id=f"tg_valor_{event.chat_id}_{event.message.id}_{ts}",
            project_key="valor",
            working_dir="/Users/test/src/ai",
            chat_id=str(event.chat_id),
            telegram_message_id=event.message.id,
            message_text=event.message.text,
            sender_name="Tom",
            sender_id=event.sender_id,
            chat_title="Dev: Valor",
        )

        assert session.status == "pending"
        assert session.is_pm
        assert session.message_text == "build feature X"
        assert session.sender_name == "Tom"
        assert session.chat_id == str(event.chat_id)

    def test_chat_session_for_non_work_message(self, make_telegram_event):
        """Non-SDLC messages should also create ChatSessions (no more simple)."""
        event = make_telegram_event(
            chat_id=-1002222222,
            message_id=202,
            text="what is the weather?",
        )

        ts = int(time.time())
        session = AgentSession.create_pm(
            session_id=f"tg_valor_{event.chat_id}_{event.message.id}_{ts}",
            project_key="valor",
            working_dir="/Users/test/src/ai",
            chat_id=str(event.chat_id),
            telegram_message_id=event.message.id,
            message_text=event.message.text,
        )

        assert session.is_pm
        assert session.message_text == "what is the weather?"


@pytest.mark.e2e
class TestSessionStatusTransitions:
    """Verify status lifecycle: pending → running → active → completed."""

    def test_full_lifecycle(self):
        ts = int(time.time())
        session = AgentSession.create_pm(
            session_id=f"lifecycle_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="lc_chat",
            telegram_message_id=1,
            message_text="do something",
        )

        assert session.status == "pending"

        # Worker picks up session
        session.status = "running"
        session.started_at = time.time()
        session.save()

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        assert reloaded.status == "running"
        assert reloaded.started_at is not None

        # Session becomes active
        session.status = "active"
        session.save()

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        assert reloaded.status == "active"

        # Session completes
        session.status = "completed"
        session.completed_at = time.time()
        session.summary = "Task finished successfully"
        session.save()

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        assert reloaded.status == "completed"
        assert reloaded.completed_at is not None
        assert reloaded.summary == "Task finished successfully"

    def test_dormant_on_open_question(self):
        ts = int(time.time())
        session = AgentSession.create_pm(
            session_id=f"dormant_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="dorm_chat",
            telegram_message_id=1,
            message_text="should I use React or Vue?",
        )

        session.status = "dormant"
        session.expectations = "Waiting for user to choose framework"
        session.save()

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        assert reloaded.status == "dormant"
        assert "framework" in reloaded.expectations

    def test_failed_session(self):
        ts = int(time.time())
        session = AgentSession.create_pm(
            session_id=f"failed_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="fail_chat",
            telegram_message_id=1,
            message_text="crash me",
        )

        session.status = "failed"
        session.summary = "API timeout after 3 retries"
        session.save()

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        assert reloaded.status == "failed"
        assert "timeout" in reloaded.summary


@pytest.mark.e2e
class TestMessageDeduplication:
    """Verify dedup prevents re-processing the same message."""

    @pytest.mark.asyncio
    async def test_first_message_is_not_duplicate(self):
        assert not await is_duplicate_message(chat_id=50001, message_id=1)

    @pytest.mark.asyncio
    async def test_recorded_message_is_duplicate(self):
        await record_message_processed(chat_id=50002, message_id=2)
        assert await is_duplicate_message(chat_id=50002, message_id=2)

    @pytest.mark.asyncio
    async def test_same_msg_id_different_chat_not_duplicate(self):
        """Dedup is scoped per-chat, not global."""
        await record_message_processed(chat_id=60001, message_id=777)
        assert not await is_duplicate_message(chat_id=60002, message_id=777)


@pytest.mark.e2e
class TestHistoryAccumulation:
    """Verify lifecycle history entries accumulate correctly."""

    def test_history_appends_entries(self):
        ts = int(time.time())
        session = AgentSession.create_pm(
            session_id=f"hist_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="hist_chat",
            telegram_message_id=1,
            message_text="track this",
        )

        session.append_history("user", "User sent: track this")
        session.append_history("classify", "Classified as: question")
        session.append_history("system", "Agent started")

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        history = reloaded.get_history_list()
        assert len(history) == 3
        assert "[user]" in history[0]
        assert "[classify]" in history[1]
        assert "[system]" in history[2]

    def test_link_tracking(self):
        ts = int(time.time())
        session = AgentSession.create_pm(
            session_id=f"links_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="link_chat",
            telegram_message_id=1,
            message_text="track links",
        )

        session.set_link("issue", "https://github.com/org/repo/issues/42")
        session.set_link("pr", "https://github.com/org/repo/pull/43")

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        links = reloaded.get_links()
        assert links["issue"] == "https://github.com/org/repo/issues/42"
        assert links["pr"] == "https://github.com/org/repo/pull/43"
        assert "plan" not in links
