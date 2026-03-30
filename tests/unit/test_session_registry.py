"""Unit tests for agent.hooks.session_registry.

Tests the session ID registry that maps Claude Code UUIDs to bridge
session IDs for hook-side resolution (issue #597).
"""

from __future__ import annotations

import time

import pytest

from agent.hooks.session_registry import (
    _STALE_TTL_SECONDS,
    _reset_for_testing,
    cleanup_stale,
    complete_registration,
    get_activity,
    record_tool_use,
    register_pending,
    resolve,
    unregister,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset registry state before and after each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


class TestRegisterAndResolve:
    """Test the register/resolve lifecycle."""

    def test_full_lifecycle(self):
        """Pre-register, complete, resolve, unregister."""
        register_pending("bridge-123")
        bridge_sid = complete_registration("claude-uuid-abc")
        assert bridge_sid == "bridge-123"
        assert resolve("claude-uuid-abc") == "bridge-123"

        unregister("claude-uuid-abc")
        assert resolve("claude-uuid-abc") is None

    def test_resolve_auto_promotes_pending(self):
        """resolve() should auto-promote a pending entry."""
        register_pending("bridge-456")
        # First resolve with a UUID should auto-promote
        assert resolve("claude-uuid-xyz") == "bridge-456"
        # Subsequent resolves should still work
        assert resolve("claude-uuid-xyz") == "bridge-456"

    def test_resolve_returns_none_for_unknown(self):
        """resolve() must return None for unknown UUIDs, not raise."""
        assert resolve("nonexistent-uuid") is None

    def test_resolve_with_none(self):
        """resolve(None) must return None without raising."""
        assert resolve(None) is None

    def test_resolve_with_empty_string(self):
        """resolve('') must return None."""
        assert resolve("") is None

    def test_concurrent_sessions(self):
        """Multiple sessions maintain isolated registry entries."""
        # Register first session
        register_pending("bridge-1")
        complete_registration("uuid-1")

        # Register second session
        register_pending("bridge-2")
        complete_registration("uuid-2")

        assert resolve("uuid-1") == "bridge-1"
        assert resolve("uuid-2") == "bridge-2"

        # Unregister one; other remains
        unregister("uuid-1")
        assert resolve("uuid-1") is None
        assert resolve("uuid-2") == "bridge-2"


class TestRegisterPending:
    """Test pre-registration edge cases."""

    def test_empty_session_id_is_noop(self):
        """register_pending with empty string should not create an entry."""
        register_pending("")
        assert resolve("any-uuid") is None

    def test_none_session_id_is_noop(self):
        """register_pending with None should not create an entry."""
        register_pending(None)
        assert resolve("any-uuid") is None

    def test_overwrite_pending(self):
        """A second register_pending overwrites the first."""
        register_pending("bridge-old")
        register_pending("bridge-new")
        assert resolve("uuid-1") == "bridge-new"


class TestCompleteRegistration:
    """Test complete_registration edge cases."""

    def test_no_pending_entry(self):
        """complete_registration with no pending entry returns None."""
        result = complete_registration("uuid-orphan")
        assert result is None

    def test_empty_uuid(self):
        """complete_registration with empty UUID returns None."""
        register_pending("bridge-1")
        result = complete_registration("")
        assert result is None

    def test_none_uuid(self):
        """complete_registration with None returns None."""
        register_pending("bridge-1")
        result = complete_registration(None)
        assert result is None

    def test_already_registered_returns_existing(self):
        """If UUID is already registered, returns existing mapping."""
        register_pending("bridge-1")
        complete_registration("uuid-1")
        # Second call returns existing
        result = complete_registration("uuid-1")
        assert result == "bridge-1"


class TestUnregister:
    """Test unregistration."""

    def test_unregister_unknown_is_noop(self):
        """unregister() with unknown UUID should not raise."""
        unregister("nonexistent-uuid")  # Should not raise

    def test_unregister_none_is_noop(self):
        """unregister(None) should not raise."""
        unregister(None)  # Should not raise

    def test_unregister_cleans_activity(self):
        """unregister() should also remove activity tracking."""
        register_pending("bridge-1")
        complete_registration("uuid-1")
        record_tool_use("uuid-1", "Bash")
        assert get_activity("bridge-1") != {}

        unregister("uuid-1")
        assert get_activity("bridge-1") == {}


class TestRecordToolUse:
    """Test tool use recording."""

    def test_records_tool_count(self):
        """tool_count increments with each call."""
        register_pending("bridge-1")
        complete_registration("uuid-1")

        record_tool_use("uuid-1", "Bash")
        record_tool_use("uuid-1", "Read")
        record_tool_use("uuid-1", "Edit")

        activity = get_activity("bridge-1")
        assert activity["tool_count"] == 3
        assert activity["last_tools"] == ["Bash", "Read", "Edit"]

    def test_last_tools_capped_at_3(self):
        """Only the last 3 tool names are kept."""
        register_pending("bridge-1")
        complete_registration("uuid-1")

        for name in ["A", "B", "C", "D", "E"]:
            record_tool_use("uuid-1", name)

        activity = get_activity("bridge-1")
        assert activity["tool_count"] == 5
        assert activity["last_tools"] == ["C", "D", "E"]

    def test_record_before_registration(self):
        """record_tool_use before registration should not raise."""
        record_tool_use("unregistered-uuid", "Bash")  # Should not raise

    def test_record_with_none_uuid(self):
        """record_tool_use(None, ...) should not raise."""
        record_tool_use(None, "Bash")  # Should not raise


class TestGetActivity:
    """Test activity retrieval."""

    def test_unknown_session_returns_empty(self):
        """get_activity for unknown session returns empty dict."""
        assert get_activity("nonexistent") == {}

    def test_none_session_returns_empty(self):
        """get_activity(None) returns empty dict."""
        assert get_activity(None) == {}


class TestCleanupStale:
    """Test TTL-based stale entry cleanup."""

    def test_removes_stale_entries(self):
        """Entries older than TTL are removed."""
        register_pending("bridge-1")
        complete_registration("uuid-1")

        # Manually backdate the timestamp
        from agent.hooks import session_registry

        session_registry._timestamps["uuid-1"] = time.time() - _STALE_TTL_SECONDS - 1

        removed = cleanup_stale()
        assert removed == 1
        assert resolve("uuid-1") is None

    def test_keeps_fresh_entries(self):
        """Entries within TTL are kept."""
        register_pending("bridge-1")
        complete_registration("uuid-1")

        removed = cleanup_stale()
        assert removed == 0
        assert resolve("uuid-1") == "bridge-1"

    def test_mixed_stale_and_fresh(self):
        """Only stale entries are removed, fresh ones are kept."""
        register_pending("bridge-1")
        complete_registration("uuid-1")
        register_pending("bridge-2")
        complete_registration("uuid-2")

        from agent.hooks import session_registry

        session_registry._timestamps["uuid-1"] = time.time() - _STALE_TTL_SECONDS - 1
        # uuid-2 stays fresh

        removed = cleanup_stale()
        assert removed == 1
        assert resolve("uuid-1") is None
        assert resolve("uuid-2") == "bridge-2"
