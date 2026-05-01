"""Unit tests for AgentSession model methods.

Tests for create_local() behavior, including the chat_id defaulting logic,
and worker_key property behavior.
"""

from unittest.mock import MagicMock, patch

from config.enums import SessionType
from models.agent_session import AgentSession


class TestCreateLocalChatId:
    """Tests for create_local() chat_id assignment."""

    def test_create_local_uses_session_id_as_default_chat_id(self):
        """When no chat_id is provided, create_local() must use session_id as chat_id.

        Previously used f"local{int(now.timestamp()) % 10000}" which caused collisions
        between CLI sessions created within the same 10,000-second window (~2.7 hours).
        Now uses session_id (the Claude Code UUID) which is guaranteed unique.
        """
        session_uuid = "claude-session-abc123-unique-uuid"
        with patch.object(AgentSession, "save", MagicMock()):
            session = AgentSession.__new__(AgentSession)
            # Call the classmethod logic by testing the internal assignment
            # We test create_local's output by checking the chat_id field
            pass

        # Test via the actual method with a mocked save
        saved_sessions = []
        original_save = AgentSession.save

        def mock_save(self):
            saved_sessions.append(self)

        AgentSession.save = mock_save
        try:
            session = AgentSession.create_local(
                session_id=session_uuid,
                project_key="test",
                working_dir="/tmp/test",
            )
            assert session.chat_id == session_uuid, (
                f"Expected chat_id={session_uuid!r}, got {session.chat_id!r}. "
                "create_local() must use session_id as the default chat_id."
            )
        finally:
            AgentSession.save = original_save

    def test_create_local_explicit_chat_id_overrides_session_id(self):
        """Callers that provide an explicit chat_id must not have it overridden."""
        session_uuid = "claude-session-def456"
        explicit_chat_id = "telegram-chat-99999"
        saved_sessions = []

        original_save = AgentSession.save

        def mock_save(self):
            saved_sessions.append(self)

        AgentSession.save = mock_save
        try:
            session = AgentSession.create_local(
                session_id=session_uuid,
                project_key="test",
                working_dir="/tmp/test",
                chat_id=explicit_chat_id,
            )
            assert session.chat_id == explicit_chat_id, (
                f"Explicit chat_id={explicit_chat_id!r} must not be overridden by session_id."
            )
        finally:
            AgentSession.save = original_save

    def test_create_local_no_timestamp_modulo_in_chat_id(self):
        """chat_id must not contain a timestamp modulo pattern.

        Verifies the old collision-prone code path is gone.
        """

        session_uuid = "claude-session-xyz789"
        original_save = AgentSession.save

        def mock_save(self):
            pass

        AgentSession.save = mock_save
        try:
            session = AgentSession.create_local(
                session_id=session_uuid,
                project_key="test",
                working_dir="/tmp/test",
            )
            # The old pattern was f"local{int(now.timestamp()) % 10000}"
            # The new chat_id should be the session_uuid, not a local+number string
            assert not session.chat_id.startswith("local"), (
                f"chat_id={session.chat_id!r} must not start with 'local' — "
                "this was the old collision-prone timestamp pattern."
            )
            assert session.chat_id == session_uuid
        finally:
            AgentSession.save = original_save

    def test_create_local_two_sessions_same_second_get_different_chat_ids(self):
        """Two sessions created in the same second must have different chat_ids.

        This was broken with the old timestamp-modulo approach: two sessions
        created at the same second would share the same chat_id, causing them
        to serialize in the same worker queue instead of running independently.
        """
        uuid_a = "claude-session-aaaa-1111"
        uuid_b = "claude-session-bbbb-2222"

        original_save = AgentSession.save

        def mock_save(self):
            pass

        AgentSession.save = mock_save
        try:
            session_a = AgentSession.create_local(
                session_id=uuid_a,
                project_key="test",
                working_dir="/tmp/test",
            )
            session_b = AgentSession.create_local(
                session_id=uuid_b,
                project_key="test",
                working_dir="/tmp/test",
            )
            assert session_a.chat_id != session_b.chat_id, (
                "Two different CLI sessions must have different chat_ids "
                f"(got {session_a.chat_id!r} and {session_b.chat_id!r})."
            )
        finally:
            AgentSession.save = original_save


def _make_session(**kwargs):
    """Create a minimal AgentSession without saving to Redis."""
    original_save = AgentSession.save
    AgentSession.save = lambda self: None
    try:
        defaults = {
            "project_key": "test-project",
            "chat_id": "chat-123",
            "session_id": "sid-1",
            "working_dir": "/tmp/test",
            "session_type": SessionType.PM,
        }
        defaults.update(kwargs)
        return AgentSession(**defaults)
    finally:
        AgentSession.save = original_save


class TestWorkerKeyProperty:
    """Tests for AgentSession.worker_key computed property."""

    def test_pm_session_uses_project_key(self):
        s = _make_session(session_type=SessionType.PM, chat_id="chat-1")
        assert s.worker_key == "test-project"
        assert s.is_project_keyed is True

    def test_teammate_session_uses_chat_id(self):
        s = _make_session(session_type=SessionType.TEAMMATE, chat_id="chat-1")
        assert s.worker_key == "chat-1"
        assert s.is_project_keyed is False

    def test_dev_with_slug_uses_slug(self):
        """Slugged dev sessions route by slug, not chat_id (issue #1085)."""
        s = _make_session(session_type=SessionType.DEV, chat_id="chat-1", slug="my-feature")
        assert s.worker_key == "my-feature"
        assert s.is_project_keyed is False

    def test_dev_without_slug_uses_project_key(self):
        s = _make_session(session_type=SessionType.DEV, chat_id="chat-1", slug=None)
        assert s.worker_key == "test-project"
        assert s.is_project_keyed is True

    def test_dev_with_empty_slug_falls_through_to_project_key(self):
        """Empty slug string must fall through to project_key, not be treated as the slug."""
        s = _make_session(session_type=SessionType.DEV, chat_id="chat-1", slug="")
        assert s.worker_key == "test-project"
        assert s.is_project_keyed is True

    def test_none_session_type_uses_project_key(self):
        """Legacy sessions without session_type fall through to project_key."""
        s = _make_session(session_type=None, chat_id="chat-1")
        assert s.worker_key == "test-project"

    def test_teammate_no_chat_id_falls_back_to_project_key(self):
        """When chat_id is None, falls back to project_key."""
        s = _make_session(session_type=SessionType.TEAMMATE, chat_id=None)
        assert s.worker_key == "test-project"

    def test_two_pm_sessions_different_chats_same_worker_key(self):
        """PM sessions from different chats share the same project-keyed worker."""
        s1 = _make_session(session_type=SessionType.PM, chat_id="chat-A")
        s2 = _make_session(session_type=SessionType.PM, chat_id="chat-B")
        assert s1.worker_key == s2.worker_key == "test-project"

    def test_two_slugged_dev_sessions_different_chats_different_worker_keys(self):
        """Slugged dev sessions on different chats get distinct worker keys equal to their slugs."""
        s1 = _make_session(session_type=SessionType.DEV, chat_id="chat-A", slug="feat-1")
        s2 = _make_session(session_type=SessionType.DEV, chat_id="chat-B", slug="feat-2")
        assert s1.worker_key != s2.worker_key
        assert s1.worker_key == "feat-1"
        assert s2.worker_key == "feat-2"

    def test_two_slugged_dev_sessions_same_chat_different_slugs_different_worker_keys(self):
        """Two slugged dev sessions sharing a chat_id must still route to distinct workers.

        This is the exact bug scenario from issue #1085: five slugged dev sessions
        created via `valor_session create --role dev` with default chat_id=0 all
        routed to a single project-keyed worker and serialized.
        """
        s1 = _make_session(session_type=SessionType.DEV, chat_id="0", slug="feat-A")
        s2 = _make_session(session_type=SessionType.DEV, chat_id="0", slug="feat-B")
        assert s1.worker_key == "feat-A"
        assert s2.worker_key == "feat-B"
        assert s1.worker_key != s2.worker_key


def _compute_worker_key_inline(session_type, slug, chat_id, project_key):
    """Helper: encodes the same four-branch logic as the inline sites in
    agent/agent_session_queue.py (lines ~362 notify publish, ~1110 enqueue).

    This helper lives at the test level — its job is to detect drift between
    the inline sites and AgentSession.worker_key. If a future PR changes the
    property without updating the inline duplicates (or vice versa), the
    truth-table test below fails with a clear mismatch message pointing at
    the specific permutation.
    """
    if session_type == SessionType.TEAMMATE:
        return chat_id or project_key
    if session_type == SessionType.PM:
        return project_key
    if slug:
        return slug
    return project_key


class TestWorkerKeyTruthTable:
    """Assert every permutation of (session_type, slug, chat_id) produces the
    same worker_key from the AgentSession property as from the inline helper.

    This is the drift-detection test called out in Risk 1: if any of the three
    computation sites (property + two inline duplicates) drift out of sync,
    this test fails with a specific permutation mismatch.
    """

    def test_truth_table_matches_inline_computation(self):
        session_types = [SessionType.PM, SessionType.DEV, SessionType.TEAMMATE, None]
        slugs = [None, "", "feat-X"]
        chat_ids = [None, "chat-1", "0"]
        project_key = "test-project"

        mismatches = []
        for st in session_types:
            for sl in slugs:
                for cid in chat_ids:
                    s = _make_session(
                        session_type=st,
                        chat_id=cid,
                        slug=sl,
                        project_key=project_key,
                    )
                    expected = _compute_worker_key_inline(st, sl, cid, project_key)
                    actual = s.worker_key
                    if actual != expected:
                        mismatches.append(
                            f"(session_type={st!r}, slug={sl!r}, chat_id={cid!r}): "
                            f"property returned {actual!r}, inline helper returned {expected!r}"
                        )
        assert not mismatches, (
            "AgentSession.worker_key drifted from inline computation sites in "
            "agent/agent_session_queue.py. Mismatched permutations:\n" + "\n".join(mismatches)
        )


class TestRecentSentDraftsField:
    """Tests for AgentSession.recent_sent_drafts field and record_recent_sent_draft()
    helper (issue #1205).

    These tests use a stub session (no Redis) to verify field mutation logic
    and the scoped-save contract without requiring a live database.
    """

    def _make_session(self):
        """Create a minimal AgentSession stub with the new field."""
        s = AgentSession.__new__(AgentSession)
        s.recent_sent_drafts = None
        s.session_id = "test-session-drafts"
        s.save = MagicMock()
        return s

    # ── Field declared and in allow-list ──────────────────────────────────────

    def test_field_declared_on_model(self):
        assert hasattr(AgentSession, "recent_sent_drafts")

    def test_field_in_agent_session_fields_allow_list(self):
        from agent.agent_session_queue import _AGENT_SESSION_FIELDS

        assert "recent_sent_drafts" in _AGENT_SESSION_FIELDS

    # ── record_recent_sent_draft: basic append ────────────────────────────────

    def test_record_appends_entry_to_none_field(self):
        """Starting from None, the first call initialises the list."""
        s = self._make_session()
        s.record_recent_sent_draft("Hello world status", {"urls": ["https://example.com"]})
        assert isinstance(s.recent_sent_drafts, list)
        assert len(s.recent_sent_drafts) == 1
        entry = s.recent_sent_drafts[0]
        assert entry["text"] == "Hello world status"
        assert "ts" in entry
        assert "artifacts" in entry

    def test_record_appends_to_existing_list(self):
        s = self._make_session()
        s.recent_sent_drafts = [{"ts": 1.0, "text": "first", "artifacts": {}}]
        s.record_recent_sent_draft("second message", {})
        assert len(s.recent_sent_drafts) == 2
        assert s.recent_sent_drafts[1]["text"] == "second message"

    # ── FIFO cap ──────────────────────────────────────────────────────────────

    def test_fifo_cap_enforced_at_max_n(self):
        """After max_n appends the list never exceeds max_n entries."""
        s = self._make_session()
        s.recent_sent_drafts = []
        for i in range(5):
            s.record_recent_sent_draft(f"message {i}", {})
        assert len(s.recent_sent_drafts) == 3  # default max_n=3

    def test_fifo_drops_oldest_entry(self):
        """The oldest entry is dropped, not the newest."""
        s = self._make_session()
        s.recent_sent_drafts = [
            {"ts": 1.0, "text": "oldest", "artifacts": {}},
            {"ts": 2.0, "text": "middle", "artifacts": {}},
            {"ts": 3.0, "text": "newest", "artifacts": {}},
        ]
        s.record_recent_sent_draft("very new", {})
        texts = [e["text"] for e in s.recent_sent_drafts]
        assert "oldest" not in texts
        assert "very new" in texts

    def test_custom_max_n_respected(self):
        s = self._make_session()
        s.recent_sent_drafts = []
        for i in range(10):
            s.record_recent_sent_draft(f"msg {i}", {}, max_n=5)
        assert len(s.recent_sent_drafts) == 5

    # ── Preview-length cap ────────────────────────────────────────────────────

    def test_text_capped_at_preview_chars(self):
        s = self._make_session()
        long_text = "x" * 1000
        s.record_recent_sent_draft(long_text, {}, preview_chars=500)
        assert len(s.recent_sent_drafts[0]["text"]) == 500

    def test_short_text_not_padded(self):
        s = self._make_session()
        s.record_recent_sent_draft("short", {})
        assert s.recent_sent_drafts[0]["text"] == "short"

    # ── Scoped save contract ──────────────────────────────────────────────────

    def test_save_uses_update_fields(self):
        """save() must be called with update_fields=[recent_sent_drafts, updated_at]."""
        s = self._make_session()
        s.record_recent_sent_draft("test", {})
        s.save.assert_called_once_with(update_fields=["recent_sent_drafts", "updated_at"])

    def test_save_failure_does_not_raise(self):
        """A save() failure must be swallowed — never propagated."""
        s = self._make_session()
        s.save.side_effect = RuntimeError("Redis write failed")
        # Must not raise.
        s.record_recent_sent_draft("some draft", {})
        # The in-memory list was still updated.
        assert len(s.recent_sent_drafts) == 1
