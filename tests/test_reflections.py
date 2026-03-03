"""Tests for reflections core: LLM reflection, memory consolidation, auto-fix, data quality."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# --- LLM Reflection Tests (Step 8) ---


class TestLLMReflection:
    """Tests for LLM reflection step."""

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("scripts.reflections.anthropic")
    def test_reflection_calls_haiku(self, mock_anthropic_module):
        """Calls Claude Haiku with session analysis data."""
        from scripts.reflections import run_llm_reflection

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    [
                        {
                            "category": "misunderstanding",
                            "summary": "User had to correct direction",
                            "pattern": "Jumped to implementation before confirming",
                            "prevention": "Ask clarifying question first",
                            "source_session": "abc",
                        }
                    ]
                )
            )
        ]
        mock_client.messages.create.return_value = mock_response

        analysis = {
            "sessions_analyzed": 1,
            "corrections": [{"session_id": "abc", "message": "No, I meant Y"}],
            "thrash_sessions": [],
        }

        result = run_llm_reflection(analysis)
        assert len(result) == 1
        assert result[0]["category"] == "misunderstanding"
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "haiku" in call_kwargs["model"]

    @patch.dict(os.environ, {}, clear=True)
    def test_reflection_skips_without_api_key(self):
        """Skips gracefully when no ANTHROPIC_API_KEY."""
        from scripts.reflections import run_llm_reflection

        # Remove the key if present
        os.environ.pop("ANTHROPIC_API_KEY", None)
        result = run_llm_reflection(
            {"sessions_analyzed": 0, "corrections": [], "thrash_sessions": []}
        )
        assert result == []

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("scripts.reflections.anthropic")
    def test_reflection_handles_api_error(self, mock_anthropic_module):
        """Returns empty list on API failure."""
        from scripts.reflections import run_llm_reflection

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API down")

        result = run_llm_reflection(
            {"sessions_analyzed": 1, "corrections": [], "thrash_sessions": []}
        )
        assert result == []

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("scripts.reflections.anthropic")
    def test_reflection_handles_malformed_response(self, mock_anthropic_module):
        """Returns empty list when API response is not valid JSON."""
        from scripts.reflections import run_llm_reflection

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not json at all")]
        mock_client.messages.create.return_value = mock_response

        result = run_llm_reflection(
            {"sessions_analyzed": 1, "corrections": [], "thrash_sessions": []}
        )
        assert result == []

    def test_reflection_skips_when_no_findings(self):
        """Skips LLM call when analysis has nothing interesting."""
        from scripts.reflections import run_llm_reflection

        result = run_llm_reflection(
            {"sessions_analyzed": 0, "corrections": [], "thrash_sessions": []}
        )
        assert result == []


# --- Memory Consolidation Tests (Step 9) ---


class TestMemoryConsolidation:
    """Tests for lessons learned consolidation."""

    def test_appends_lessons_to_redis(self):
        """Writes reflection output to Redis LessonLearned model."""
        from models.reflections import LessonLearned
        from scripts.reflections import consolidate_memory

        reflections = [
            {
                "category": "misunderstanding",
                "summary": "User correction needed",
                "pattern": "Jumped to code before confirming",
                "prevention": "Ask first",
                "source_session": "abc",
            }
        ]

        consolidate_memory(reflections, "2026-02-16")

        lessons = LessonLearned.query.all()
        assert len(lessons) == 1
        assert lessons[0].date == "2026-02-16"
        assert lessons[0].category == "misunderstanding"
        assert lessons[0].validated == 0

    def test_deduplicates_by_pattern(self):
        """Does not add duplicate patterns."""
        from models.reflections import LessonLearned
        from scripts.reflections import consolidate_memory

        # Add first entry
        reflections1 = [
            {
                "category": "misunderstanding",
                "summary": "Old entry",
                "pattern": "Jumped to code before confirming",
                "prevention": "Ask first",
                "source_session": "old",
            }
        ]
        consolidate_memory(reflections1, "2026-02-15")

        # Try to add duplicate
        reflections2 = [
            {
                "category": "misunderstanding",
                "summary": "New but same pattern",
                "pattern": "Jumped to code before confirming",
                "prevention": "Ask first",
                "source_session": "abc",
            }
        ]
        consolidate_memory(reflections2, "2026-02-16")

        lessons = LessonLearned.query.all()
        assert len(lessons) == 1  # Still just one entry

    def test_prunes_old_entries(self):
        """Removes entries older than 90 days."""
        import time

        from models.reflections import LessonLearned
        from scripts.reflections import consolidate_memory

        # Create old lesson directly in Redis
        LessonLearned.create(
            date="2025-01-01",
            category="old",
            summary="Should be pruned",
            pattern="old pattern",
            prevention="n/a",
            source_session="old",
            validated=0,
            created_at=time.time() - (100 * 86400),
        )
        # Create recent lesson
        LessonLearned.create(
            date=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
            category="recent",
            summary="Should remain",
            pattern="recent pattern",
            prevention="n/a",
            source_session="recent",
            validated=0,
            created_at=time.time() - (10 * 86400),
        )

        consolidate_memory([], "2026-02-16")

        remaining = LessonLearned.query.all()
        assert len(remaining) == 1
        assert remaining[0].category == "recent"

    def test_creates_lesson_in_redis(self):
        """Creates lesson in Redis even when no prior entries exist."""
        from models.reflections import LessonLearned
        from scripts.reflections import consolidate_memory

        reflections = [
            {
                "category": "test",
                "summary": "Test entry",
                "pattern": "test pattern",
                "prevention": "n/a",
                "source_session": "test",
            }
        ]
        consolidate_memory(reflections, "2026-02-16")
        lessons = LessonLearned.query.all()
        assert len(lessons) == 1

    def test_handles_empty_reflections(self):
        """No-op with empty reflections (but still prunes)."""
        from models.reflections import LessonLearned
        from scripts.reflections import consolidate_memory

        consolidate_memory([], "2026-02-16")
        # No lessons should exist
        assert len(LessonLearned.query.all()) == 0


# --- Step 3 Sentry Check ---


class TestSentryCheck:
    """Tests for Sentry check step."""

    @pytest.mark.asyncio
    async def test_sentry_skips_gracefully(self, tmp_path):
        """Sentry check logs skip message and continues."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        runner.state.findings = {}
        await runner.step_check_sentry()
        assert "sentry" in runner.state.findings
        assert any("skipped" in f.lower() for f in runner.state.findings["sentry"])


# --- Step 4 Task Cleanup ---


class TestTaskCleanup:
    """Tests for task cleanup step using gh CLI."""

    @pytest.mark.asyncio
    @patch("scripts.reflections.subprocess.run")
    async def test_task_cleanup_calls_gh(self, mock_run):
        """Step 4 calls gh issue list."""
        from scripts.reflections import ReflectionRunner

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="42\tbug title\topen\tbug\n",
        )

        runner = ReflectionRunner()
        runner.state.findings = {}
        await runner.step_clean_tasks()

        # Should have called gh
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "list" in call_args

    @pytest.mark.asyncio
    @patch("scripts.reflections.subprocess.run")
    async def test_task_cleanup_handles_gh_failure(self, mock_run):
        """Step 4 handles gh CLI failure gracefully."""
        from scripts.reflections import ReflectionRunner

        mock_run.side_effect = FileNotFoundError("gh not found")

        runner = ReflectionRunner()
        runner.state.findings = {}
        await runner.step_clean_tasks()
        # Should not raise


# --- Step 10 GitHub Issue ---


class TestGitHubIssueStep:
    """Tests for GitHub issue creation step."""

    @pytest.mark.asyncio
    @patch("scripts.reflections.create_reflections_issue")
    async def test_creates_issue_per_project_with_findings(self, mock_create):
        """Step 10 creates GitHub issue for each project that has namespaced findings."""
        from unittest.mock import AsyncMock

        from scripts.reflections import ReflectionRunner

        mock_create.return_value = "https://github.com/org/repo/issues/1"
        runner = ReflectionRunner()

        # Inject a fake project with github config
        runner.projects = [
            {
                "slug": "my-proj",
                "working_directory": "/tmp",
                "github": {"org": "org", "repo": "repo"},
            }
        ]
        # Findings must be namespaced to match the project slug
        runner.state.findings = {"my-proj:log_review": ["finding 1"]}

        with patch.object(runner, "step_post_to_telegram", new=AsyncMock()):
            await runner.step_create_github_issue()

        mock_create.assert_called_once()

    @pytest.mark.asyncio
    @patch("scripts.reflections.create_reflections_issue")
    async def test_skips_issue_when_no_findings(self, mock_create):
        """Step 10 skips when no findings."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        runner.state.findings = {}
        await runner.step_create_github_issue()
        mock_create.assert_not_called()


# --- Step 5 Audit Docs ---


class TestStepAuditDocs:
    """Tests for step_audit_docs registration and delegation to DocsAuditor."""

    def test_step_audit_docs_is_registered(self):
        """step_audit_docs is registered as step 5 in runner.steps."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        assert (5, "Audit Documentation", runner.step_audit_docs) in runner.steps

    @pytest.mark.asyncio
    @patch("scripts.reflections.asyncio.to_thread")
    @patch("scripts.reflections.DocsAuditor")
    async def test_step_audit_docs_calls_docs_auditor(
        self, mock_docs_auditor_cls, mock_to_thread
    ):
        """step_audit_docs instantiates DocsAuditor and delegates to run() via asyncio.to_thread."""
        from unittest.mock import MagicMock

        from scripts.reflections import ReflectionRunner

        # Build a fake summary with the attributes step_audit_docs inspects
        mock_summary = MagicMock()
        mock_summary.skipped = False
        mock_summary.skip_reason = ""
        mock_summary.updated = []
        mock_summary.deleted = []
        mock_summary.kept = ["doc1.md"]

        mock_to_thread.return_value = mock_summary

        runner = ReflectionRunner()
        runner.state.findings = {}
        await runner.step_audit_docs()

        # DocsAuditor should have been instantiated
        mock_docs_auditor_cls.assert_called_once()

        # asyncio.to_thread should have been called with the auditor's run method
        mock_to_thread.assert_called_once()
        call_args = mock_to_thread.call_args[0]
        assert call_args[0] == mock_docs_auditor_cls.return_value.run


# --- Step 8 Auto-Fix Bugs ---


class TestIgnoreLog:
    """Tests for ignore log helpers."""

    def test_load_ignore_log_empty_when_no_entries(self):
        """load_ignore_log returns empty list when no entries exist in Redis."""
        from scripts.reflections import load_ignore_log

        result = load_ignore_log()
        assert result == []

    def test_load_ignore_log_filters_expired(self):
        """load_ignore_log excludes expired entries via Redis."""
        import time

        from models.reflections import ReflectionIgnore
        from scripts.reflections import load_ignore_log

        # Create expired entry
        ReflectionIgnore.create(
            pattern="expired",
            reason="",
            created_at=time.time() - (2 * 86400),
            expires_at=time.time() - 86400,  # expired yesterday
        )
        # Create active entry
        ReflectionIgnore.add_ignore("active", days=14)
        # Create entry expiring today (still active)
        ReflectionIgnore.create(
            pattern="today",
            reason="",
            created_at=time.time() - 86400,
            expires_at=time.time() + 3600,  # expires in 1 hour
        )

        result = load_ignore_log()
        patterns = [e["pattern"] for e in result]
        assert "expired" not in patterns
        assert "active" in patterns
        assert "today" in patterns

    def test_prune_ignore_log_removes_expired(self):
        """prune_ignore_log removes expired entries and keeps active ones in Redis."""
        import time

        from models.reflections import ReflectionIgnore
        from scripts.reflections import prune_ignore_log

        # Create expired entry
        ReflectionIgnore.create(
            pattern="expired",
            reason="",
            created_at=time.time() - (2 * 86400),
            expires_at=time.time() - 86400,
        )
        # Create active entry
        ReflectionIgnore.add_ignore("active", days=14)

        prune_ignore_log()

        remaining = ReflectionIgnore.query.all()
        patterns = [e.pattern for e in remaining]
        assert "expired" not in patterns
        assert "active" in patterns

    def test_is_ignored_matches_substring(self):
        """is_ignored returns True when entry_pattern is a substring of pattern."""
        from scripts.reflections import is_ignored

        entries = [{"pattern": "null pointer"}]
        assert is_ignored("causes null pointer dereference", entries) is True
        assert is_ignored("unrelated bug", entries) is False

    def test_is_ignored_case_insensitive(self):
        """is_ignored does case-insensitive matching."""
        from scripts.reflections import is_ignored

        entries = [{"pattern": "NULL POINTER"}]
        assert is_ignored("null pointer error", entries) is True


class TestConfidenceScorer:
    """Tests for is_high_confidence helper."""

    def test_high_confidence_all_three_criteria(self):
        """Returns True when all three criteria are met."""
        from scripts.reflections import is_high_confidence

        r = {
            "category": "code_bug",
            "prevention": "Always validate input",
            "pattern": "Missing null check before dereferencing",
        }
        assert is_high_confidence(r) is True

    def test_high_confidence_two_of_three(self):
        """Returns True when exactly two criteria are met."""
        from scripts.reflections import is_high_confidence

        # code_bug + long pattern, no prevention
        r = {
            "category": "code_bug",
            "prevention": "",
            "pattern": "Missing null check before dereferencing pointer",
        }
        assert is_high_confidence(r) is True

    def test_not_high_confidence_one_criterion(self):
        """Returns False when only one criterion is met."""
        from scripts.reflections import is_high_confidence

        r = {
            "category": "misunderstanding",
            "prevention": "",
            "pattern": "short",
        }
        assert is_high_confidence(r) is False

    def test_not_high_confidence_all_missing(self):
        """Returns False when no criteria are met."""
        from scripts.reflections import is_high_confidence

        r = {"category": "misunderstanding", "prevention": "", "pattern": ""}
        assert is_high_confidence(r) is False


class TestAutoFixStep:
    """Tests for step_auto_fix_bugs."""

    @pytest.mark.asyncio
    async def test_auto_fix_disabled_by_env(self, monkeypatch):
        """step_auto_fix_bugs skips when REFLECTIONS_AUTO_FIX_ENABLED=false."""
        monkeypatch.setenv("REFLECTIONS_AUTO_FIX_ENABLED", "false")
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        runner.state.reflections = [
            {
                "category": "code_bug",
                "summary": "A bug",
                "pattern": "null pointer dereference in loop",
                "prevention": "Always check for None",
            }
        ]
        await runner.step_auto_fix_bugs()
        progress = runner.state.step_progress.get("auto_fix_bugs", {})
        assert progress.get("skipped") is True

    @pytest.mark.asyncio
    async def test_auto_fix_dry_run_does_not_invoke_claude(self, monkeypatch):
        """Dry run logs intent without calling claude subprocess."""
        monkeypatch.setenv("REFLECTIONS_AUTO_FIX_ENABLED", "true")
        from unittest.mock import patch

        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        runner.state._dry_run = True
        runner.state.reflections = [
            {
                "category": "code_bug",
                "summary": "Null pointer in loop",
                "pattern": "null pointer dereference in loop body",
                "prevention": "Always validate before use",
            }
        ]

        with (
            patch("scripts.reflections.subprocess.run") as mock_run,
            patch("scripts.reflections.has_existing_github_work", return_value=False),
            patch("scripts.reflections.load_ignore_log", return_value=[]),
            patch("scripts.reflections.prune_ignore_log"),
        ):
            await runner.step_auto_fix_bugs()
            # subprocess.run should NOT have been called for the fix (only gh dedup checks)
            claude_calls = [
                c for c in mock_run.call_args_list if c[0][0] and c[0][0][0] == "claude"
            ]
            assert len(claude_calls) == 0

        attempts = runner.state.auto_fix_attempts
        assert len(attempts) == 1
        assert attempts[0]["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_auto_fix_skips_ignored_pattern(self, monkeypatch):
        """Skips a reflection whose pattern is in the ignore log."""
        monkeypatch.setenv("REFLECTIONS_AUTO_FIX_ENABLED", "true")
        from unittest.mock import patch

        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        runner.state._dry_run = False
        runner.state.reflections = [
            {
                "category": "code_bug",
                "summary": "Known ignored bug",
                "pattern": "null pointer dereference in loop body",
                "prevention": "Always validate before use",
            }
        ]

        ignore_entries = [{"pattern": "null pointer", "ignored_until": "2099-01-01"}]

        with (
            patch("scripts.reflections.load_ignore_log", return_value=ignore_entries),
            patch("scripts.reflections.prune_ignore_log"),
            patch("scripts.reflections.subprocess.run") as mock_run,
        ):
            await runner.step_auto_fix_bugs()
            # subprocess.run should NOT be called with claude
            claude_calls = [
                c for c in mock_run.call_args_list if c[0][0] and c[0][0][0] == "claude"
            ]
            assert len(claude_calls) == 0

        attempts = runner.state.auto_fix_attempts
        assert len(attempts) == 1
        assert attempts[0]["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_auto_fix_skips_low_confidence(self, monkeypatch):
        """Skips reflections that don't meet the 2-of-3 confidence criteria."""
        monkeypatch.setenv("REFLECTIONS_AUTO_FIX_ENABLED", "true")
        from unittest.mock import patch

        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        runner.state._dry_run = True
        runner.state.reflections = [
            {
                "category": "misunderstanding",  # not code_bug
                "summary": "User misunderstood",
                "pattern": "short",  # too short
                "prevention": "",  # no prevention
            }
        ]

        with (
            patch("scripts.reflections.load_ignore_log", return_value=[]),
            patch("scripts.reflections.prune_ignore_log"),
        ):
            await runner.step_auto_fix_bugs()

        # No attempts because no candidates passed confidence filter
        assert runner.state.auto_fix_attempts == []
        progress = runner.state.step_progress.get("auto_fix_bugs", {})
        assert progress.get("candidates") == 0

    @pytest.mark.asyncio
    async def test_auto_fix_step_registered_as_step_8(self):
        """step_auto_fix_bugs is registered as step 8."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        step_nums = [s[0] for s in runner.steps]
        step_names = {s[0]: s[1] for s in runner.steps}
        assert 8 in step_nums
        assert step_names[8] == "Auto-Fix Bugs"

    @pytest.mark.asyncio
    async def test_steps_renumbered_correctly(self):
        """Memory Consolidation is step 9, Produce Daily Report is 10, GitHub is 11."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        step_names = {s[0]: s[1] for s in runner.steps}
        assert step_names.get(9) == "Memory Consolidation"
        assert step_names.get(10) == "Produce Daily Report"
        assert step_names.get(11) == "GitHub Issue Creation"

    @pytest.mark.asyncio
    async def test_auto_fix_skips_duplicate_github_work(self, monkeypatch):
        """Skips patterns with an existing open issue or PR."""
        monkeypatch.setenv("REFLECTIONS_AUTO_FIX_ENABLED", "true")
        from unittest.mock import patch

        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()
        runner.state._dry_run = False
        runner.state.reflections = [
            {
                "category": "code_bug",
                "summary": "Already tracked",
                "pattern": "null pointer dereference in loop body",
                "prevention": "Always validate before use",
            }
        ]
        runner.projects = [
            {
                "slug": "ai",
                "working_directory": "/tmp",
                "github": {"org": "org", "repo": "repo"},
            }
        ]

        with (
            patch("scripts.reflections.load_ignore_log", return_value=[]),
            patch("scripts.reflections.prune_ignore_log"),
            patch("scripts.reflections.has_existing_github_work", return_value=True),
            patch("scripts.reflections.subprocess.run") as mock_run,
        ):
            await runner.step_auto_fix_bugs()
            claude_calls = [
                c for c in mock_run.call_args_list if c[0][0] and c[0][0][0] == "claude"
            ]
            assert len(claude_calls) == 0

        attempts = runner.state.auto_fix_attempts
        assert len(attempts) == 1
        assert attempts[0]["status"] == "duplicate"


class TestReflectionsStateAutoFixField:
    """Tests for new auto_fix_attempts field on ReflectionsState."""

    def test_auto_fix_attempts_defaults_to_empty_list(self):
        """ReflectionsState.auto_fix_attempts defaults to []."""
        from scripts.reflections import ReflectionsState

        state = ReflectionsState()
        assert state.auto_fix_attempts == []

    def test_auto_fix_attempts_persisted_in_serialization(self):
        """auto_fix_attempts survives asdict round-trip."""
        from dataclasses import asdict

        from scripts.reflections import ReflectionsState

        state = ReflectionsState()
        state.auto_fix_attempts = [{"pattern": "foo", "status": "dry_run"}]
        d = asdict(state)
        assert d["auto_fix_attempts"] == [{"pattern": "foo", "status": "dry_run"}]


class TestCLIFlags:
    """Tests for CLI --dry-run and --ignore flags."""

    def test_ignore_flag_appends_to_redis(self, tmp_path, monkeypatch):
        """--ignore appends a new entry to Redis ReflectionIgnore model."""
        import sys

        import scripts.reflections as reflections_mod
        from models.reflections import ReflectionIgnore

        # Simulate CLI: python reflections.py --ignore "some bug pattern" --reason "known issue"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "reflections.py",
                "--ignore",
                "some bug pattern",
                "--reason",
                "known issue",
            ],
        )

        import asyncio

        asyncio.run(reflections_mod.main())

        entries = ReflectionIgnore.query.all()
        assert len(entries) == 1
        assert entries[0].pattern == "some bug pattern"
        assert entries[0].reason == "known issue"

    def test_dry_run_flag_sets_state(self, tmp_path, monkeypatch):
        """--dry-run sets runner.state._dry_run = True."""
        import asyncio
        import sys
        from unittest.mock import patch

        import scripts.reflections as reflections_mod

        monkeypatch.setattr(sys, "argv", ["reflections.py", "--dry-run"])

        captured_runner = {}

        async def fake_run(self):
            captured_runner["instance"] = self

        with patch.object(reflections_mod.ReflectionRunner, "run", fake_run):
            asyncio.run(reflections_mod.main())

        assert captured_runner["instance"].state._dry_run is True
