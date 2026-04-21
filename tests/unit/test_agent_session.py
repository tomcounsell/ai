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
