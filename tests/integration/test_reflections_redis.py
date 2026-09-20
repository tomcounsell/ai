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
        from models.reflection_ignore import ReflectionIgnore

        entry = ReflectionIgnore.add_ignore("null pointer", reason="known issue", days=14)
        assert entry.pattern == "null pointer"

        active = ReflectionIgnore.get_active()
        assert len(active) == 1
        assert active[0].pattern == "null pointer"

    def test_expired_entries_excluded(self):
        """Expired entries are not returned by get_active()."""
        from models.reflection_ignore import ReflectionIgnore

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
        from models.reflection_ignore import ReflectionIgnore

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
        from models.reflection_ignore import ReflectionIgnore

        ReflectionIgnore.add_ignore("NULL POINTER", days=14)
        assert ReflectionIgnore.is_ignored("null pointer error") is True
        assert ReflectionIgnore.is_ignored("unrelated") is False

    def test_is_ignored_substring_match(self):
        """is_ignored matches when entry pattern is substring of query."""
        from models.reflection_ignore import ReflectionIgnore

        ReflectionIgnore.add_ignore("timeout", days=14)
        assert ReflectionIgnore.is_ignored("connection timeout in bridge") is True


class TestAnalyzeSessionsFromRedis:
    """Tests for Redis-backed session analysis via reflections.session_intelligence."""

    def test_analyzes_sessions_from_redis(self):
        """_analyze_sessions_from_redis queries AgentSession model.

        A high tool-call-to-turn ratio must NOT produce any thrash finding:
        the false-positive thrash detector was removed (see #1414), so the
        analysis dict no longer carries a ``thrash_sessions`` key.
        """
        from models.agent_session import AgentSession
        from reflections.session_intelligence import _analyze_sessions_from_redis

        # Create a session for today with a high tool-call-to-turn ratio that
        # the old detector would have flagged as "thrashing".
        AgentSession.create(
            session_id="test-session-1",
            project_key="ai",
            status="completed",
            created_at=time.time(),
            started_at=time.time(),
            updated_at=datetime.now(tz=UTC),
            turn_count=5,
            tool_call_count=20,
        )

        today = __import__("utils.utc", fromlist=["utc_now"]).utc_now().strftime("%Y-%m-%d")
        result = _analyze_sessions_from_redis(today)
        assert result["sessions_analyzed"] == 1
        assert "thrash_sessions" not in result

    def test_detects_failed_sessions(self):
        """Failed sessions appear in error_patterns.

        ``summary`` is a derived property backed by ``session_events`` (it is
        computed from the most recent ``summary`` event, not a plain Field —
        see models/agent_session.py), so it must be set via attribute
        assignment on an already-saved instance rather than passed as a
        ``create()`` kwarg: the setter appends a session_event and does a
        partial save keyed on the record already existing, which a
        constructor-time kwarg predates.
        """
        from models.agent_session import AgentSession
        from reflections.session_intelligence import _analyze_sessions_from_redis

        session = AgentSession.create(
            session_id="failed-session",
            project_key="ai",
            status="failed",
            created_at=time.time(),
            started_at=time.time(),
            updated_at=datetime.now(tz=UTC),
            turn_count=2,
            tool_call_count=3,
        )
        session.summary = "Crashed during build step"

        today = __import__("utils.utc", fromlist=["utc_now"]).utc_now().strftime("%Y-%m-%d")
        result = _analyze_sessions_from_redis(today)
        assert len(result.get("error_patterns", [])) >= 1

    def test_empty_when_no_sessions(self):
        """Returns empty analysis when no sessions match."""
        from reflections.session_intelligence import _analyze_sessions_from_redis

        result = _analyze_sessions_from_redis("2099-01-01")
        assert result["sessions_analyzed"] == 0


class TestIgnoreLogRedis:
    """Tests for Redis-backed ignore log via reflections.utilities."""

    def test_load_ignore_entries_from_redis(self):
        """load_ignore_entries reads active entries from ReflectionIgnore model."""
        from models.reflection_ignore import ReflectionIgnore
        from reflections.utilities import load_ignore_entries

        ReflectionIgnore.add_ignore("test pattern", reason="testing", days=14)

        entries = load_ignore_entries()
        assert len(entries) == 1
        assert entries[0]["pattern"] == "test pattern"

    def test_cleanup_expired_via_model(self):
        """ReflectionIgnore.cleanup_expired() cleans up expired entries in Redis."""
        from models.reflection_ignore import ReflectionIgnore

        # Create expired entry
        ReflectionIgnore.create(
            pattern="expired",
            reason="",
            created_at=time.time() - 86400,
            expires_at=time.time() - 3600,
        )

        ReflectionIgnore.cleanup_expired()
        assert len(ReflectionIgnore.query.all()) == 0


# TestRedisIndexCleanupReflection was removed entirely. It held three tests; all
# three had lost their subject, and the root cause is one the node-list-driven
# triage in this PR reached only for the third.
#
# - test_reflection_entry_structure and test_reflection_registered_in_yaml both
#   read config/reflections.yaml. That file moved to the private, iCloud-synced
#   vault (~/Desktop/Valor/reflections.yaml) in c2af09602, is gitignored
#   (.gitignore:8), and is copied into a checkout only by machine provisioning
#   (scripts/install_email_bridge.sh:150). test_reflection_registered_in_yaml
#   therefore raises FileNotFoundError in any clean checkout -- verified here,
#   not inferred -- and passed on the nightly machine only because that machine
#   is provisioned. It is not retargetable while staying test-only: the repo
#   holds no reflections registry to assert against, and the vault file is
#   private, absent from CI, and evolves independently of this repo, so a test
#   pointed at it would assert operator config rather than a repo contract.
#
# - test_cleanup_callable_importable asserted that
#   scripts.popoto_index_cleanup.run_cleanup is importable, framed as the
#   redis-index-cleanup reflection's callable. That framing is false: the live
#   vault entry for redis-index-cleanup dispatches
#   agent.session_health.cleanup_corrupted_agent_sessions, and the vault has
#   zero references to popoto_index_cleanup, so nothing schedules run_cleanup.
#   Its only surviving claim -- that the symbol imports -- is already proven by
#   the module-level `from scripts.popoto_index_cleanup import run_cleanup` in
#   tests/unit/test_popoto_cleanup_reflection.py, which additionally exercises
#   run_cleanup behaviourally (orphan reaping, slow-rebuild abandonment, count
#   accuracy). Deleting it loses no coverage.
#
# parse_every_duration's "Ns" parsing, the only code-under-test the removed
# structural assertions touched, stays covered by
# tests/unit/test_reflection_schedule_grammar.py::TestDurationHelpers.
