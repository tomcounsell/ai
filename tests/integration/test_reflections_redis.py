"""Tests for reflections Redis integration: ReflectionIgnore, session analysis.

ReflectionRun, ReflectionsState, and ReflectionRunner tests were removed as
part of issue #748 (monolith deletion). The ReflectionRun model no longer
exists; per-step state is now tracked via the Reflection model in reflections.yaml.
The helpers that previously lived in scripts/reflections.py are now in
reflections/ package modules.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime


class TestReflectionIgnoreModel:
    """Tests for ReflectionIgnore Popoto model."""

    def test_add_ignore(self):
        """Add an ignore entry and verify it's active."""
        from models.reflections import ReflectionIgnore

        entry = ReflectionIgnore.add_ignore("null pointer", reason="known issue", days=14)
        assert entry.pattern == "null pointer"

        active = ReflectionIgnore.get_active()
        assert len(active) == 1
        assert active[0].pattern == "null pointer"

    def test_expired_entries_excluded(self):
        """Expired entries are not returned by get_active()."""
        from models.reflections import ReflectionIgnore

        # Create expired entry
        ReflectionIgnore.create(
            pattern="old bug",
            reason="",
            created_at=time.time() - (30 * 86400),
            expires_at=time.time() - 86400,  # expired yesterday
        )
        # Create active entry
        ReflectionIgnore.add_ignore("new bug", days=14)

        active = ReflectionIgnore.get_active()
        patterns = [e.pattern for e in active]
        assert "old bug" not in patterns
        assert "new bug" in patterns

    def test_cleanup_expired(self):
        """cleanup_expired removes only expired entries."""
        from models.reflections import ReflectionIgnore

        ReflectionIgnore.create(
            pattern="expired",
            reason="",
            created_at=time.time() - 86400,
            expires_at=time.time() - 3600,  # expired 1 hour ago
        )
        ReflectionIgnore.add_ignore("active", days=14)

        deleted = ReflectionIgnore.cleanup_expired()
        assert deleted == 1
        assert len(ReflectionIgnore.query.all()) == 1

    def test_is_ignored_case_insensitive(self):
        """is_ignored does case-insensitive substring matching."""
        from models.reflections import ReflectionIgnore

        ReflectionIgnore.add_ignore("NULL POINTER", days=14)
        assert ReflectionIgnore.is_ignored("null pointer error") is True
        assert ReflectionIgnore.is_ignored("unrelated") is False

    def test_is_ignored_substring_match(self):
        """is_ignored matches when entry pattern is substring of query."""
        from models.reflections import ReflectionIgnore

        ReflectionIgnore.add_ignore("timeout", days=14)
        assert ReflectionIgnore.is_ignored("connection timeout in bridge") is True


class TestAnalyzeSessionsFromRedis:
    """Tests for Redis-backed session analysis via reflections.session_intelligence."""

    def test_analyzes_sessions_from_redis(self):
        """_analyze_sessions_from_redis queries AgentSession model."""
        from models.agent_session import AgentSession
        from reflections.session_intelligence import _analyze_sessions_from_redis

        # Create a session for today
        AgentSession.create(
            session_id="test-session-1",
            project_key="ai",
            status="completed",
            created_at=time.time(),
            started_at=time.time(),
            updated_at=datetime.now(tz=UTC),
            turn_count=5,
            tool_call_count=20,  # High ratio = thrashing
        )

        today = __import__("bridge.utc", fromlist=["utc_now"]).utc_now().strftime("%Y-%m-%d")
        result = _analyze_sessions_from_redis(today)
        assert result["sessions_analyzed"] == 1
        assert len(result["thrash_sessions"]) == 1

    def test_detects_failed_sessions(self):
        """Failed sessions appear in error_patterns."""
        from models.agent_session import AgentSession
        from reflections.session_intelligence import _analyze_sessions_from_redis

        AgentSession.create(
            session_id="failed-session",
            project_key="ai",
            status="failed",
            created_at=time.time(),
            started_at=time.time(),
            updated_at=datetime.now(tz=UTC),
            turn_count=2,
            tool_call_count=3,
            summary="Crashed during build step",
        )

        today = __import__("bridge.utc", fromlist=["utc_now"]).utc_now().strftime("%Y-%m-%d")
        result = _analyze_sessions_from_redis(today)
        assert len(result.get("error_patterns", [])) >= 1

    def test_empty_when_no_sessions(self):
        """Returns empty analysis when no sessions match."""
        from reflections.session_intelligence import _analyze_sessions_from_redis

        result = _analyze_sessions_from_redis("2099-01-01")
        assert result["sessions_analyzed"] == 0


class TestIgnoreLogRedis:
    """Tests for Redis-backed ignore log via reflections.utils."""

    def test_load_ignore_entries_from_redis(self):
        """load_ignore_entries reads active entries from ReflectionIgnore model."""
        from models.reflections import ReflectionIgnore
        from reflections.utils import load_ignore_entries

        ReflectionIgnore.add_ignore("test pattern", reason="testing", days=14)

        entries = load_ignore_entries()
        assert len(entries) == 1
        assert entries[0]["pattern"] == "test pattern"

    def test_cleanup_expired_via_model(self):
        """ReflectionIgnore.cleanup_expired() cleans up expired entries in Redis."""
        from models.reflections import ReflectionIgnore

        # Create expired entry
        ReflectionIgnore.create(
            pattern="expired",
            reason="",
            created_at=time.time() - 86400,
            expires_at=time.time() - 3600,
        )

        ReflectionIgnore.cleanup_expired()
        assert len(ReflectionIgnore.query.all()) == 0


class TestPopotoIndexCleanupReflection:
    """Tests for popoto-index-cleanup reflection registration."""

    def test_reflection_registered_in_yaml(self):
        """Verify popoto-index-cleanup exists in reflections.yaml."""
        from pathlib import Path

        import yaml

        config_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        names = [r["name"] for r in config["reflections"]]
        assert "popoto-index-cleanup" in names

    def test_reflection_entry_structure(self):
        """Verify the reflection entry has required fields."""
        from pathlib import Path

        import yaml

        config_path = Path(__file__).parent.parent.parent / "config" / "reflections.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        entry = next(r for r in config["reflections"] if r["name"] == "popoto-index-cleanup")
        assert entry["execution_type"] == "function"
        assert entry["callable"] == "scripts.popoto_index_cleanup.run_cleanup"
        assert entry["enabled"] is True
        assert entry["interval"] == 86400
        assert entry["priority"] == "low"

    def test_cleanup_callable_importable(self):
        """Verify the cleanup function can be imported."""
        from scripts.popoto_index_cleanup import run_cleanup

        assert callable(run_cleanup)
