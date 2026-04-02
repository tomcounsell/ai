"""Unit tests for get_activity() fallback to health_check._tool_counts.

When the session registry reverse lookup fails (e.g., crash before first
hook fires and pending-to-UUID promotion never happened), get_activity()
should fall back to reading _tool_counts from health_check.py.

See docs/plans/silent-session-death.md Fix 1.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.hooks.session_registry import (
    _reset_for_testing,
    complete_registration,
    get_activity,
    record_tool_use,
    register_pending,
    unregister,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset registry state before and after each test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


class TestGetActivityFallback:
    """Test that get_activity() falls back to health_check._tool_counts."""

    def test_fallback_when_reverse_lookup_fails(self):
        """When no UUID mapping exists, fall back to health_check._tool_counts."""
        with patch("agent.health_check._tool_counts", {"bridge-orphan": 42}):
            activity = get_activity("bridge-orphan")
        assert activity["tool_count"] == 42
        assert activity["last_tools"] == []

    def test_fallback_logs_warning(self, caplog):
        """Fallback path should log a warning about the divergence."""
        import logging

        with (
            patch("agent.health_check._tool_counts", {"bridge-orphan": 10}),
            caplog.at_level(logging.WARNING),
        ):
            get_activity("bridge-orphan")

        assert any("reverse lookup failed" in r.message for r in caplog.records)
        assert any("bridge-orphan" in r.message for r in caplog.records)

    def test_no_fallback_when_primary_succeeds(self):
        """When UUID mapping exists, primary path is used (no fallback)."""
        register_pending("bridge-1")
        complete_registration("uuid-1")
        record_tool_use("uuid-1", "Bash")
        record_tool_use("uuid-1", "Read")

        with patch("agent.health_check._tool_counts", {"bridge-1": 999}):
            activity = get_activity("bridge-1")

        # Should use primary path (2 tools), not fallback (999)
        assert activity["tool_count"] == 2
        assert activity["last_tools"] == ["Bash", "Read"]

    def test_fallback_returns_empty_when_count_zero(self):
        """Fallback with count=0 should return empty dict (not a partial result)."""
        with patch("agent.health_check._tool_counts", {"bridge-zero": 0}):
            activity = get_activity("bridge-zero")
        assert activity == {}

    def test_fallback_returns_empty_when_health_check_missing(self):
        """If health_check has no entry, return empty dict."""
        with patch("agent.health_check._tool_counts", {}):
            activity = get_activity("bridge-unknown")
        assert activity == {}

    def test_fallback_survives_import_error(self):
        """If health_check import fails, return empty dict gracefully."""
        with patch("agent.hooks.session_registry.logger"):
            # Temporarily break the import by patching the import mechanism
            import builtins

            real_import = builtins.__import__

            def broken_import(name, *args, **kwargs):
                if name == "agent.health_check":
                    raise ImportError("no module")
                return real_import(name, *args, **kwargs)

            with patch.object(builtins, "__import__", side_effect=broken_import):
                activity = get_activity("bridge-broken")
            assert activity == {}

    def test_fallback_after_unregister(self):
        """After unregister clears the UUID mapping, fallback should still work."""
        register_pending("bridge-1")
        complete_registration("uuid-1")
        record_tool_use("uuid-1", "Bash")

        # Verify primary works
        assert get_activity("bridge-1")["tool_count"] == 1

        # Unregister clears the mapping
        unregister("uuid-1")

        # Now primary fails, fallback should kick in
        with patch("agent.health_check._tool_counts", {"bridge-1": 15}):
            activity = get_activity("bridge-1")
        assert activity["tool_count"] == 15
