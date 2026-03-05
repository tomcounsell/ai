"""Tests for reflections Redis integration: ReflectionRun, ReflectionIgnore, LessonLearned."""

from __future__ import annotations

import time

import pytest


class TestReflectionRunModel:
    """Tests for ReflectionRun Popoto model."""

    def test_create_and_query(self):
        """Create a ReflectionRun and query it back."""
        from models.reflections import ReflectionRun

        run = ReflectionRun.create(
            date="2026-02-25",
            current_step=3,
            completed_steps=[1, 2],
            daily_report=["Done step 1", "Done step 2"],
            findings={"legacy": ["found TODO"]},
            session_analysis={},
            reflections=[],
            auto_fix_attempts=[],
            step_progress={"clean_legacy": {"findings": 1}},
            started_at=time.time(),
            dry_run=False,
        )
        assert run.date == "2026-02-25"
        assert run.current_step == 3

        # Query back
        results = ReflectionRun.query.filter(date="2026-02-25")
        assert len(results) == 1
        assert results[0].completed_steps == [1, 2]

    def test_load_or_create_new(self):
        """load_or_create creates a new run if none exists."""
        from models.reflections import ReflectionRun

        run = ReflectionRun.load_or_create("2026-03-01")
        assert run.date == "2026-03-01"
        assert run.current_step == 1
        assert run.completed_steps == []

    def test_load_or_create_existing(self):
        """load_or_create returns existing run for same date."""
        from models.reflections import ReflectionRun

        ReflectionRun.create(
            date="2026-03-02",
            current_step=5,
            completed_steps=[1, 2, 3, 4],
            daily_report=[],
            findings={},
            session_analysis={},
            reflections=[],
            auto_fix_attempts=[],
            step_progress={},
            started_at=time.time(),
            dry_run=False,
        )

        run = ReflectionRun.load_or_create("2026-03-02")
        assert run.current_step == 5
        assert run.completed_steps == [1, 2, 3, 4]

    def test_save_checkpoint(self):
        """save_checkpoint persists updated state."""
        from models.reflections import ReflectionRun

        run = ReflectionRun.load_or_create("2026-03-03")
        run.current_step = 7
        run.completed_steps = [1, 2, 3, 4, 5, 6]
        run.save_checkpoint()

        # Query back to verify
        results = ReflectionRun.query.filter(date="2026-03-03")
        assert len(results) == 1
        assert results[0].current_step == 7

    def test_cleanup_expired(self):
        """cleanup_expired removes old runs."""
        from models.reflections import ReflectionRun

        # Create old run
        ReflectionRun.create(
            date="2025-01-01",
            current_step=1,
            completed_steps=[],
            daily_report=[],
            findings={},
            session_analysis={},
            reflections=[],
            auto_fix_attempts=[],
            step_progress={},
            started_at=time.time() - (60 * 86400),  # 60 days ago
            dry_run=False,
        )
        # Create recent run
        ReflectionRun.create(
            date="2026-02-25",
            current_step=1,
            completed_steps=[],
            daily_report=[],
            findings={},
            session_analysis={},
            reflections=[],
            auto_fix_attempts=[],
            step_progress={},
            started_at=time.time(),
            dry_run=False,
        )

        deleted = ReflectionRun.cleanup_expired(max_age_days=30)
        assert deleted == 1
        assert len(ReflectionRun.query.all()) == 1


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


class TestLessonLearnedModel:
    """Tests for LessonLearned Popoto model."""

    def test_add_lesson(self):
        """Add a lesson and query it back."""
        from models.reflections import LessonLearned

        lesson = LessonLearned.add_lesson(
            date="2026-02-25",
            category="code_bug",
            summary="Missing null check",
            pattern="Always validate before dereferencing",
            prevention="Add null check",
            source_session="abc123",
        )
        assert lesson is not None
        assert lesson.category == "code_bug"

    def test_deduplication_by_pattern(self):
        """Duplicate patterns are rejected."""
        from models.reflections import LessonLearned

        LessonLearned.add_lesson(
            date="2026-02-25",
            category="code_bug",
            summary="First",
            pattern="Same pattern",
        )
        result = LessonLearned.add_lesson(
            date="2026-02-25",
            category="code_bug",
            summary="Second",
            pattern="Same pattern",
        )
        assert result is None
        assert len(LessonLearned.query.all()) == 1

    def test_cleanup_expired(self):
        """cleanup_expired removes old lessons."""
        from models.reflections import LessonLearned

        # Create old lesson
        LessonLearned.create(
            date="2025-01-01",
            category="old",
            summary="Ancient lesson",
            pattern="old pattern",
            prevention="",
            source_session="",
            validated=0,
            created_at=time.time() - (100 * 86400),
        )
        # Create recent lesson
        LessonLearned.add_lesson(
            date="2026-02-25",
            category="recent",
            summary="Fresh lesson",
            pattern="new pattern",
        )

        deleted = LessonLearned.cleanup_expired(max_age_days=90)
        assert deleted == 1
        remaining = LessonLearned.query.all()
        assert len(remaining) == 1
        assert remaining[0].category == "recent"

    def test_get_recent(self):
        """get_recent returns only recent lessons."""
        from models.reflections import LessonLearned

        # Old lesson
        LessonLearned.create(
            date="2025-01-01",
            category="old",
            summary="Old",
            pattern="old pattern",
            prevention="",
            source_session="",
            validated=0,
            created_at=time.time() - (100 * 86400),
        )
        # Recent lesson
        LessonLearned.add_lesson(
            date="2026-02-25",
            category="recent",
            summary="Recent",
            pattern="recent pattern",
        )

        recent = LessonLearned.get_recent(days=90)
        assert len(recent) == 1
        assert recent[0].category == "recent"


class TestAnalyzeSessionsFromRedis:
    """Tests for Redis-backed session analysis."""

    def test_analyzes_sessions_from_redis(self):
        """analyze_sessions_from_redis queries AgentSession model."""
        from models.agent_session import AgentSession
        from scripts.reflections import analyze_sessions_from_redis

        # Create a session for today
        AgentSession.create(
            session_id="test-session-1",
            project_key="ai",
            status="completed",
            created_at=time.time(),
            started_at=time.time(),
            last_activity=time.time(),
            turn_count=5,
            tool_call_count=20,  # High ratio = thrashing
        )

        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        result = analyze_sessions_from_redis(today)
        assert result["sessions_analyzed"] == 1
        assert len(result["thrash_sessions"]) == 1

    def test_detects_failed_sessions(self):
        """Failed sessions appear in error_patterns."""
        from models.agent_session import AgentSession
        from scripts.reflections import analyze_sessions_from_redis

        AgentSession.create(
            session_id="failed-session",
            project_key="ai",
            status="failed",
            created_at=time.time(),
            started_at=time.time(),
            last_activity=time.time(),
            turn_count=2,
            tool_call_count=3,
            summary="Crashed during build step",
        )

        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        result = analyze_sessions_from_redis(today)
        assert len(result.get("error_patterns", [])) >= 1

    def test_empty_when_no_sessions(self):
        """Returns empty analysis when no sessions match."""
        from scripts.reflections import analyze_sessions_from_redis

        result = analyze_sessions_from_redis("2099-01-01")
        assert result["sessions_analyzed"] == 0


class TestConsolidateMemoryRedis:
    """Tests for Redis-backed memory consolidation."""

    def test_consolidate_to_redis(self):
        """consolidate_memory writes to LessonLearned model."""
        from models.reflections import LessonLearned
        from scripts.reflections import consolidate_memory

        reflections = [
            {
                "category": "code_bug",
                "summary": "Missing check",
                "pattern": "Always validate input before processing",
                "prevention": "Add validation",
                "source_session": "s1",
            }
        ]

        consolidate_memory(reflections, "2026-02-25")

        lessons = LessonLearned.query.all()
        assert len(lessons) == 1
        assert lessons[0].category == "code_bug"
        assert lessons[0].pattern == "Always validate input before processing"

    def test_consolidate_deduplicates(self):
        """consolidate_memory skips duplicate patterns."""
        from models.reflections import LessonLearned
        from scripts.reflections import consolidate_memory

        reflections = [
            {
                "category": "code_bug",
                "summary": "First entry",
                "pattern": "Duplicate pattern",
                "prevention": "",
                "source_session": "",
            }
        ]

        consolidate_memory(reflections, "2026-02-25")
        consolidate_memory(reflections, "2026-02-26")  # same pattern

        lessons = LessonLearned.query.all()
        assert len(lessons) == 1


class TestIgnoreLogRedis:
    """Tests for Redis-backed ignore log functions."""

    def test_load_ignore_log_from_redis(self):
        """load_ignore_log reads from ReflectionIgnore model."""
        from models.reflections import ReflectionIgnore
        from scripts.reflections import load_ignore_log

        ReflectionIgnore.add_ignore("test pattern", reason="testing", days=14)

        entries = load_ignore_log()
        assert len(entries) == 1
        assert entries[0]["pattern"] == "test pattern"

    def test_prune_ignore_log_uses_redis(self):
        """prune_ignore_log cleans up expired entries in Redis."""
        from models.reflections import ReflectionIgnore
        from scripts.reflections import prune_ignore_log

        # Create expired entry
        ReflectionIgnore.create(
            pattern="expired",
            reason="",
            created_at=time.time() - 86400,
            expires_at=time.time() - 3600,
        )

        prune_ignore_log()
        assert len(ReflectionIgnore.query.all()) == 0


class TestReflectionsStateSave:
    """Tests for ReflectionsState Redis-backed save."""

    def test_save_to_redis(self):
        """ReflectionsState.save() persists to Redis ReflectionRun model."""
        from models.reflections import ReflectionRun
        from scripts.reflections import ReflectionsState

        # Create initial run so save can find it
        ReflectionRun.load_or_create("2026-02-25")

        state = ReflectionsState(date="2026-02-25")
        state.current_step = 5
        state.completed_steps = [1, 2, 3, 4]
        state.save()

        runs = ReflectionRun.query.filter(date="2026-02-25")
        assert len(runs) == 1
        assert runs[0].current_step == 5


class TestRedisDataQuality:
    """Tests for step 14: Redis data quality checks."""

    def test_step_registered_as_step_14(self):
        """step_redis_data_quality is registered as step 14."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        step_names = {s[0]: s[1] for s in runner.steps}
        assert step_names.get(14) == "Redis Data Quality"

    @pytest.mark.asyncio
    async def test_detects_unsummarized_links(self):
        """Finds links with no ai_summary in last 7 days."""
        import time

        from models.link import Link
        from scripts.reflections import ReflectionRunner

        # Create a recent link with no summary
        Link.create(
            url="https://example.com/no-summary",
            chat_id="123",
            domain="example.com",
            sender="user1",
            status="unread",
            timestamp=time.time(),
        )
        # Create a recent link WITH summary
        Link.create(
            url="https://example.com/summarized",
            chat_id="123",
            domain="example.com",
            sender="user1",
            status="read",
            timestamp=time.time(),
            ai_summary="This is a summary",
        )

        runner = ReflectionRunner()
        runner.state.findings = {}
        await runner.step_redis_data_quality()

        findings = runner.state.findings.get("redis_data_quality", [])
        assert any("1 links" in f and "no AI summary" in f for f in findings)

    @pytest.mark.asyncio
    async def test_detects_dead_channels(self):
        """Finds chats with no recent activity."""
        import time

        from models.chat import Chat
        from scripts.reflections import ReflectionRunner

        # Create a stale chat (40 days old)
        Chat.create(
            chat_id="stale-chat",
            chat_name="Dead Channel",
            chat_type="group",
            updated_at=time.time() - (40 * 86400),
        )
        # Create an active chat
        Chat.create(
            chat_id="active-chat",
            chat_name="Active Channel",
            chat_type="group",
            updated_at=time.time(),
        )

        runner = ReflectionRunner()
        runner.state.findings = {}
        await runner.step_redis_data_quality()

        findings = runner.state.findings.get("redis_data_quality", [])
        assert any("1 chat" in f and "no activity" in f for f in findings)

    @pytest.mark.asyncio
    async def test_empty_when_no_data(self):
        """No findings when Redis has no data."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        runner.state.findings = {}
        await runner.step_redis_data_quality()

        findings = runner.state.findings.get("redis_data_quality", [])
        # Should have no unsummarized/dead channel findings
        assert not any("no AI summary" in f for f in findings)
        assert not any("no activity" in f for f in findings)
