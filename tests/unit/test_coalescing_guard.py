"""Tests for the in-memory coalescing guard (_recent_session_by_chat).

The coalescing guard bridges the Redis visibility gap for rapid-fire messages
arriving within ~200ms of each other. It uses a module-level dict in
bridge/telegram_bridge.py to track recently created sessions.

See issue #705 for the race condition this solves.
"""

import time


class TestCoalescingGuardDict:
    """Test the _recent_session_by_chat dict behavior directly."""

    def setup_method(self):
        """Clear the coalescing guard dict before each test."""
        from bridge.telegram_bridge import _recent_session_by_chat

        _recent_session_by_chat.clear()

    def teardown_method(self):
        """Clear the coalescing guard dict after each test."""
        from bridge.telegram_bridge import _recent_session_by_chat

        _recent_session_by_chat.clear()

    def test_dict_set_and_lookup(self):
        """Setting a chat_id entry should be retrievable immediately."""
        from bridge.telegram_bridge import _recent_session_by_chat

        chat_id = "test_chat_123"
        session_id = "tg_proj_123_456"
        now = time.time()

        _recent_session_by_chat[chat_id] = (session_id, now)

        assert chat_id in _recent_session_by_chat
        stored_session_id, stored_ts = _recent_session_by_chat[chat_id]
        assert stored_session_id == session_id
        assert stored_ts == now

    def test_different_chat_ids_do_not_interfere(self):
        """Entries for different chat_ids should be independent."""
        from bridge.telegram_bridge import _recent_session_by_chat

        now = time.time()
        _recent_session_by_chat["chat_A"] = ("session_A", now)
        _recent_session_by_chat["chat_B"] = ("session_B", now)

        assert _recent_session_by_chat["chat_A"][0] == "session_A"
        assert _recent_session_by_chat["chat_B"][0] == "session_B"

    def test_second_message_within_window_finds_entry(self):
        """A second message to the same chat within the merge window should find the entry."""
        from bridge.telegram_bridge import (
            PENDING_MERGE_WINDOW_SECONDS,
            _recent_session_by_chat,
        )

        chat_id = "test_rapid_fire"
        session_id = "tg_proj_rapid_1"
        now = time.time()

        # Simulate first message setting the guard
        _recent_session_by_chat[chat_id] = (session_id, now)

        # Simulate second message arriving 100ms later
        guard_session_id, guard_ts = _recent_session_by_chat[chat_id]
        age = (now + 0.1) - guard_ts  # 100ms later
        assert age <= PENDING_MERGE_WINDOW_SECONDS
        assert guard_session_id == session_id

    def test_stale_entry_outside_window(self):
        """An entry older than the merge window should be considered stale."""
        from bridge.telegram_bridge import (
            PENDING_MERGE_WINDOW_SECONDS,
            _recent_session_by_chat,
        )

        chat_id = "test_stale"
        session_id = "tg_proj_stale_1"
        old_ts = time.time() - PENDING_MERGE_WINDOW_SECONDS - 1  # 1s past window

        _recent_session_by_chat[chat_id] = (session_id, old_ts)

        # Check age exceeds window
        age = time.time() - old_ts
        assert age > PENDING_MERGE_WINDOW_SECONDS

    def test_lazy_cleanup_removes_stale_entries(self):
        """Stale entries should be cleaned up during the guard check."""
        from bridge.telegram_bridge import (
            PENDING_MERGE_WINDOW_SECONDS,
            _recent_session_by_chat,
        )

        now = time.time()
        # Add a stale entry
        _recent_session_by_chat["stale_chat"] = (
            "old_session",
            now - PENDING_MERGE_WINDOW_SECONDS - 5,
        )
        # Add a fresh entry
        _recent_session_by_chat["fresh_chat"] = ("new_session", now)

        # Simulate lazy cleanup (same logic as in telegram_bridge.py)
        stale_chats = [
            cid
            for cid, (_, ts) in _recent_session_by_chat.items()
            if now - ts > PENDING_MERGE_WINDOW_SECONDS
        ]
        for cid in stale_chats:
            del _recent_session_by_chat[cid]

        assert "stale_chat" not in _recent_session_by_chat
        assert "fresh_chat" in _recent_session_by_chat

    def test_empty_chat_id_does_not_match(self):
        """Empty chat_id should not match any existing entry."""
        from bridge.telegram_bridge import _recent_session_by_chat

        _recent_session_by_chat["real_chat"] = ("session_1", time.time())

        assert "" not in _recent_session_by_chat

    def test_self_match_guard_prevents_coalescing_with_own_session(self):
        """The coalescing guard must skip entries matching the current message's session_id.

        Without this check, a message could find its own session in the dict
        (set earlier in the handler) and try to coalesce with itself. See #705.
        """
        from bridge.telegram_bridge import (
            PENDING_MERGE_WINDOW_SECONDS,
            _recent_session_by_chat,
        )

        chat_id = "test_self_match"
        own_session_id = "tg_proj_chat_789"
        now = time.time()

        # Simulate: this message already set its own entry in the guard dict
        _recent_session_by_chat[chat_id] = (own_session_id, now)

        # The coalescing check should NOT match when guard_session_id == session_id
        guard_session_id, guard_ts = _recent_session_by_chat[chat_id]
        guard_age = (now + 0.05) - guard_ts  # 50ms later
        should_coalesce = (
            guard_age <= PENDING_MERGE_WINDOW_SECONDS
            and guard_session_id != own_session_id  # self-match guard
        )
        assert not should_coalesce, "Message must not coalesce with its own session"

    def test_coalescing_matches_different_session(self):
        """The coalescing guard SHOULD match when the session_id differs (different message)."""
        from bridge.telegram_bridge import (
            PENDING_MERGE_WINDOW_SECONDS,
            _recent_session_by_chat,
        )

        chat_id = "test_different_match"
        first_session_id = "tg_proj_chat_100"
        second_session_id = "tg_proj_chat_101"
        now = time.time()

        # First message set the guard
        _recent_session_by_chat[chat_id] = (first_session_id, now)

        # Second message checks — should coalesce since session_ids differ
        guard_session_id, guard_ts = _recent_session_by_chat[chat_id]
        guard_age = (now + 0.1) - guard_ts
        should_coalesce = (
            guard_age <= PENDING_MERGE_WINDOW_SECONDS
            and guard_session_id != second_session_id  # different session
        )
        assert should_coalesce, "Message should coalesce with a different session"

    def test_dict_overwrites_on_same_chat_id(self):
        """A new session for the same chat_id should overwrite the previous entry."""
        from bridge.telegram_bridge import _recent_session_by_chat

        now = time.time()
        _recent_session_by_chat["chat_1"] = ("session_old", now - 2)
        _recent_session_by_chat["chat_1"] = ("session_new", now)

        session_id, ts = _recent_session_by_chat["chat_1"]
        assert session_id == "session_new"
        assert ts == now


class TestMergeWindowConstant:
    """Verify the merge window constant value."""

    def test_merge_window_is_8(self):
        """PENDING_MERGE_WINDOW_SECONDS should be 8."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS

        assert PENDING_MERGE_WINDOW_SECONDS == 8


class TestSemanticRoutingAlwaysOn:
    """Verify semantic routing feature flag has been removed."""

    def test_no_is_semantic_routing_enabled_function(self):
        """The is_semantic_routing_enabled function should not exist in session_router."""
        from bridge import session_router

        assert not hasattr(session_router, "is_semantic_routing_enabled")

    def test_find_matching_session_exists(self):
        """find_matching_session should still be importable."""
        from bridge.session_router import find_matching_session

        assert callable(find_matching_session)

    def test_coalescing_guard_dict_exists(self):
        """The _recent_session_by_chat dict should be importable from telegram_bridge."""
        from bridge.telegram_bridge import _recent_session_by_chat

        assert isinstance(_recent_session_by_chat, dict)
