"""Tests for popoto Redis models (DeadLetter, BridgeEvent, TelegramMessage, AgentSession).

All tests use redis_test_db fixture (db=1) for isolation from production data.
"""

import time

import pytest

# ── DeadLetter ──────────────────────────────────────────────────────────────


class TestDeadLetter:
    """Tests for the DeadLetter model and dead_letters.py functions."""

    def test_create_and_query(self):
        from models.dead_letter import DeadLetter

        dl = DeadLetter.create(
            chat_id="111",
            reply_to=42,
            text="failed message",
            created_at=time.time(),
            attempts=0,
        )
        assert dl.letter_id
        assert dl.chat_id == "111"
        assert dl.text == "failed message"
        assert dl.attempts == 0

        found = DeadLetter.query.filter(chat_id="111")
        assert len(found) == 1
        assert found[0].text == "failed message"

    def test_increment_attempts(self):
        from models.dead_letter import DeadLetter

        dl = DeadLetter.create(
            chat_id="222",
            text="retry me",
            created_at=time.time(),
            attempts=0,
        )
        dl.attempts = 3
        dl.save()

        found = DeadLetter.query.filter(chat_id="222")
        assert found[0].attempts == 3

    def test_delete(self):
        from models.dead_letter import DeadLetter

        dl = DeadLetter.create(
            chat_id="333",
            text="delete me",
            created_at=time.time(),
            attempts=0,
        )
        dl.delete()

        found = DeadLetter.query.filter(chat_id="333")
        assert len(found) == 0

    def test_null_reply_to(self):
        from models.dead_letter import DeadLetter

        dl = DeadLetter.create(
            chat_id="444",
            text="no reply",
            created_at=time.time(),
            attempts=0,
        )
        assert dl.reply_to is None

    @pytest.mark.asyncio
    async def test_persist_failed_delivery(self):
        from bridge.dead_letters import persist_failed_delivery
        from models.dead_letter import DeadLetter

        await persist_failed_delivery(chat_id=999, reply_to=10, text="boom")

        found = DeadLetter.query.filter(chat_id="999")
        assert len(found) == 1
        assert found[0].text == "boom"
        assert found[0].reply_to == 10

    @pytest.mark.asyncio
    async def test_replay_deletes_on_success(self):
        """Replay should delete letters that are successfully sent."""
        from bridge.dead_letters import replay_dead_letters
        from models.dead_letter import DeadLetter

        DeadLetter.create(
            chat_id="555",
            text="replay me",
            created_at=time.time(),
            attempts=0,
        )

        class FakeClient:
            sent = []

            async def send_message(self, chat_id, text, reply_to=None):
                self.sent.append((chat_id, text))

        client = FakeClient()
        count = await replay_dead_letters(client)

        assert count == 1
        assert len(client.sent) == 1
        assert client.sent[0] == (555, "replay me")

        remaining = DeadLetter.query.filter(chat_id="555")
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_replay_increments_attempts_on_failure(self):
        """Replay should increment attempts when send fails."""
        from bridge.dead_letters import replay_dead_letters
        from models.dead_letter import DeadLetter

        DeadLetter.create(
            chat_id="666",
            text="fail me",
            created_at=time.time(),
            attempts=0,
        )

        class FailClient:
            async def send_message(self, chat_id, text, reply_to=None):
                raise ConnectionError("Telegram down")

        count = await replay_dead_letters(FailClient())
        assert count == 0

        remaining = DeadLetter.query.filter(chat_id="666")
        assert len(remaining) == 1
        assert remaining[0].attempts == 1


# ── BridgeEvent ─────────────────────────────────────────────────────────────


class TestBridgeEvent:
    """Tests for the BridgeEvent model."""

    def test_log_creates_event(self):
        from models.bridge_event import BridgeEvent

        be = BridgeEvent.log(
            "message_received",
            chat_id=123,
            project="valor",
            sender="Tom",
        )

        assert be.event_id
        assert be.event_type == "message_received"
        assert be.chat_id == "123"
        assert be.project_key == "valor"
        assert be.data == {"sender": "Tom"}
        assert be.timestamp > 0

    def test_log_without_optional_fields(self):
        from models.bridge_event import BridgeEvent

        be = BridgeEvent.log("startup")
        assert be.event_type == "startup"
        assert be.chat_id is None
        assert be.project_key is None
        assert be.data is None

    def test_query_by_event_type(self):
        from models.bridge_event import BridgeEvent

        BridgeEvent.log("agent_response", elapsed_seconds=2.5)
        BridgeEvent.log("agent_timeout", elapsed_seconds=300.0)
        BridgeEvent.log("agent_response", elapsed_seconds=1.2)

        responses = BridgeEvent.query.filter(event_type="agent_response")
        assert len(responses) == 2

        timeouts = BridgeEvent.query.filter(event_type="agent_timeout")
        assert len(timeouts) == 1

    def test_query_by_project(self):
        from models.bridge_event import BridgeEvent

        BridgeEvent.log("message_received", project="valor")
        BridgeEvent.log("message_received", project="popoto")
        BridgeEvent.log("message_received", project="valor")

        valor_events = BridgeEvent.query.filter(project_key="valor")
        assert len(valor_events) == 2

    def test_cleanup_old(self):
        from models.bridge_event import BridgeEvent

        # Create an "old" event by manually setting timestamp
        BridgeEvent.create(
            event_type="ancient",
            timestamp=time.time() - 86400 * 10,  # 10 days ago
        )
        BridgeEvent.log("fresh")

        deleted = BridgeEvent.cleanup_old(max_age_seconds=86400 * 7)
        assert deleted == 1

        remaining = BridgeEvent.query.all()
        assert len(remaining) == 1
        assert remaining[0].event_type == "fresh"

    def test_data_dict_preserves_values(self):
        from models.bridge_event import BridgeEvent

        be = BridgeEvent.log(
            "agent_response",
            elapsed_seconds=5.3,
            response_length=1200,
            session_id="tg_valor_123",
        )
        assert be.data["elapsed_seconds"] == 5.3
        assert be.data["response_length"] == 1200
        assert be.data["session_id"] == "tg_valor_123"


# ── TelegramMessage ─────────────────────────────────────────────────────────


class TestTelegramMessage:
    """Tests for the TelegramMessage model."""

    def test_create_incoming(self):
        from models.telegram import TelegramMessage

        tm = TelegramMessage.create(
            chat_id="100",
            message_id=42,
            direction="in",
            sender="Tom",
            content="hello valor",
            timestamp=time.time(),
            message_type="text",
        )
        assert tm.msg_id
        assert tm.direction == "in"
        assert tm.sender == "Tom"
        assert tm.content == "hello valor"

    def test_create_outgoing(self):
        from models.telegram import TelegramMessage

        tm = TelegramMessage.create(
            chat_id="100",
            direction="out",
            sender="Valor",
            content="hi Tom",
            timestamp=time.time(),
            message_type="response",
        )
        assert tm.direction == "out"
        assert tm.message_type == "response"

    def test_query_by_chat_id(self):
        from models.telegram import TelegramMessage

        now = time.time()
        TelegramMessage.create(
            chat_id="200",
            direction="in",
            sender="A",
            content="msg1",
            timestamp=now,
            message_type="text",
        )
        TelegramMessage.create(
            chat_id="200",
            direction="out",
            sender="Valor",
            content="msg2",
            timestamp=now + 1,
            message_type="response",
        )
        TelegramMessage.create(
            chat_id="300",
            direction="in",
            sender="B",
            content="msg3",
            timestamp=now,
            message_type="text",
        )

        chat_200 = TelegramMessage.query.filter(chat_id="200")
        assert len(chat_200) == 2

        chat_300 = TelegramMessage.query.filter(chat_id="300")
        assert len(chat_300) == 1

    def test_query_by_direction(self):
        from models.telegram import TelegramMessage

        now = time.time()
        TelegramMessage.create(
            chat_id="400",
            direction="in",
            sender="User",
            content="q",
            timestamp=now,
            message_type="text",
        )
        TelegramMessage.create(
            chat_id="400",
            direction="out",
            sender="Valor",
            content="a",
            timestamp=now + 1,
            message_type="response",
        )

        incoming = TelegramMessage.query.filter(direction="in")
        outgoing = TelegramMessage.query.filter(direction="out")
        assert len(incoming) == 1
        assert len(outgoing) == 1

    def test_session_id_optional(self):
        from models.telegram import TelegramMessage

        tm = TelegramMessage.create(
            chat_id="500",
            direction="in",
            sender="User",
            content="test",
            timestamp=time.time(),
            message_type="text",
        )
        assert tm.session_id is None

        tm2 = TelegramMessage.create(
            chat_id="500",
            direction="out",
            sender="Valor",
            content="response",
            timestamp=time.time(),
            message_type="response",
            session_id="tg_valor_500",
        )
        assert tm2.session_id == "tg_valor_500"


# ── AgentSession ────────────────────────────────────────────────────────────


class TestAgentSession:
    """Tests for the AgentSession model."""

    def test_create_active_session(self):
        from models.sessions import AgentSession

        s = AgentSession.create(
            session_id="tg_valor_100",
            project_key="valor",
            status="active",
            chat_id="100",
            sender="Tom",
            started_at=time.time(),
            last_activity=time.time(),
            tool_call_count=0,
            branch_name="session/tg-valor-100",
            message_text="fix the bug",
        )
        assert s.session_id == "tg_valor_100"
        assert s.status == "active"
        assert s.tool_call_count == 0

    def test_update_status_to_completed(self):
        from models.sessions import AgentSession

        now = time.time()
        s = AgentSession.create(
            session_id="tg_valor_200",
            project_key="valor",
            status="active",
            chat_id="200",
            sender="Tom",
            started_at=now,
            last_activity=now,
            tool_call_count=0,
        )
        assert s.status == "active"

        # Popoto UniqueKeyField + KeyField limitation: changing a KeyField value
        # changes the db_key, causing pre_save's is_self check to fail. The
        # workaround is delete + recreate (same pattern used in production code
        # which catches the exception). See agent/job_queue.py:690-694.
        s.delete()
        AgentSession.create(
            session_id="tg_valor_200",
            project_key="valor",
            status="completed",
            chat_id="200",
            sender="Tom",
            started_at=now,
            last_activity=time.time(),
            tool_call_count=0,
        )

        found = AgentSession.query.filter(session_id="tg_valor_200")
        assert len(found) == 1
        assert found[0].status == "completed"

    def test_update_tool_call_count(self):
        from models.sessions import AgentSession

        s = AgentSession.create(
            session_id="tg_valor_300",
            project_key="valor",
            status="active",
            chat_id="300",
            sender="Tom",
            started_at=time.time(),
            last_activity=time.time(),
            tool_call_count=0,
        )
        s.tool_call_count = 20
        s.last_activity = time.time()
        s.save()

        found = AgentSession.query.filter(session_id="tg_valor_300")
        assert found[0].tool_call_count == 20

    def test_query_active_sessions(self):
        from models.sessions import AgentSession

        now = time.time()
        AgentSession.create(
            session_id="s1",
            project_key="valor",
            status="active",
            chat_id="1",
            sender="A",
            started_at=now,
            last_activity=now,
            tool_call_count=5,
        )
        AgentSession.create(
            session_id="s2",
            project_key="valor",
            status="completed",
            chat_id="2",
            sender="B",
            started_at=now,
            last_activity=now,
            tool_call_count=10,
        )
        AgentSession.create(
            session_id="s3",
            project_key="popoto",
            status="active",
            chat_id="3",
            sender="C",
            started_at=now,
            last_activity=now,
            tool_call_count=2,
        )

        active = AgentSession.query.filter(status="active")
        assert len(active) == 2

        valor_active = AgentSession.query.filter(
            project_key="valor",
            status="active",
        )
        assert len(valor_active) == 1
        assert valor_active[0].session_id == "s1"

    def test_query_by_project(self):
        from models.sessions import AgentSession

        now = time.time()
        AgentSession.create(
            session_id="p1",
            project_key="valor",
            status="active",
            chat_id="1",
            sender="A",
            started_at=now,
            last_activity=now,
            tool_call_count=0,
        )
        AgentSession.create(
            session_id="p2",
            project_key="popoto",
            status="active",
            chat_id="2",
            sender="B",
            started_at=now,
            last_activity=now,
            tool_call_count=0,
        )

        valor = AgentSession.query.filter(project_key="valor")
        assert len(valor) == 1

    def test_failed_status(self):
        from models.sessions import AgentSession

        now = time.time()
        s = AgentSession.create(
            session_id="fail_1",
            project_key="valor",
            status="active",
            chat_id="100",
            sender="Tom",
            started_at=now,
            last_activity=now,
            tool_call_count=3,
        )
        # Delete + recreate to change status KeyField (see test_update_status_to_completed)
        s.delete()
        AgentSession.create(
            session_id="fail_1",
            project_key="valor",
            status="failed",
            chat_id="100",
            sender="Tom",
            started_at=now,
            last_activity=now,
            tool_call_count=3,
        )

        found = AgentSession.query.filter(session_id="fail_1")
        assert found[0].status == "failed"

    @pytest.mark.asyncio
    async def test_async_create_and_save(self):
        from models.sessions import AgentSession

        now = time.time()
        s = await AgentSession.async_create(
            session_id="async_1",
            project_key="valor",
            status="active",
            chat_id="100",
            sender="Tom",
            started_at=now,
            last_activity=now,
            tool_call_count=0,
        )
        assert s.session_id == "async_1"

        # Update non-KeyField (tool_call_count) via save — this works fine
        s.tool_call_count = 5
        await s.async_save()

        # Update KeyField (status) via delete + recreate
        # (see test_update_status_to_completed for explanation)
        s.delete()
        await AgentSession.async_create(
            session_id="async_1",
            project_key="valor",
            status="completed",
            chat_id="100",
            sender="Tom",
            started_at=now,
            last_activity=now,
            tool_call_count=5,
        )

        found = await AgentSession.query.async_filter(session_id="async_1")
        assert found[0].status == "completed"
        assert found[0].tool_call_count == 5


# ── Integration: store_message Redis mirror ──────────────────────────────────


class TestStoreMessageMirror:
    """Test that store_message() writes to both SQLite and Redis."""

    def test_store_message_creates_redis_mirror(self, redis_test_db, tmp_path):
        from models.telegram import TelegramMessage
        from tools.telegram_history import store_message

        db_path = tmp_path / "test_history.db"
        result = store_message(
            chat_id="mirror_chat",
            content="hello from test",
            sender="TestUser",
            message_id=99,
            message_type="text",
            db_path=db_path,
        )
        assert result.get("stored") is True

        # Verify Redis mirror was created
        found = TelegramMessage.query.filter(chat_id="mirror_chat")
        assert len(found) == 1
        assert found[0].content == "hello from test"
        assert found[0].direction == "in"
        assert found[0].sender == "TestUser"

    def test_store_message_outgoing_direction(self, redis_test_db, tmp_path):
        from models.telegram import TelegramMessage
        from tools.telegram_history import store_message

        db_path = tmp_path / "test_history.db"
        store_message(
            chat_id="out_chat",
            content="response text",
            sender="Valor",
            message_type="response",
            db_path=db_path,
        )

        found = TelegramMessage.query.filter(chat_id="out_chat")
        assert len(found) == 1
        assert found[0].direction == "out"


# ── Test isolation verification ──────────────────────────────────────────────


class TestIsolation:
    """Verify test DB isolation works correctly."""

    def test_uses_test_db(self):
        """Confirm test data doesn't appear in production db=0."""
        import redis as redis_lib

        from models.dead_letter import DeadLetter

        DeadLetter.create(
            chat_id="isolation_probe",
            text="x",
            created_at=1.0,
            attempts=0,
        )

        # Verify it's NOT in db=0
        r0 = redis_lib.Redis(host="127.0.0.1", port=6379, db=0)
        keys_in_db0 = r0.keys("*isolation_probe*")
        assert len(keys_in_db0) == 0, "Test data leaked into production db=0"

    def test_no_leakage_between_tests(self):
        """Each test starts with a clean db (flushed by fixture)."""
        from models.dead_letter import DeadLetter

        found = DeadLetter.query.filter(chat_id="leak_test")
        assert len(found) == 0

        DeadLetter.create(
            chat_id="leak_test",
            text="x",
            created_at=1.0,
            attempts=0,
        )

    def test_previous_test_data_gone(self):
        """Verify data from test_no_leakage_between_tests was cleaned up."""
        from models.dead_letter import DeadLetter

        found = DeadLetter.query.filter(chat_id="leak_test")
        assert len(found) == 0
