"""Tests for daydream core: session analysis, LLM reflection, memory consolidation."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# --- Session Analysis Tests (Step 7) ---


class TestSessionAnalysis:
    """Tests for session analysis logic."""

    def test_analyze_empty_sessions_dir(self, tmp_path):
        """No sessions directory returns empty analysis."""
        from scripts.daydream import analyze_sessions

        result = analyze_sessions(tmp_path / "sessions", "2026-02-16")
        assert result["sessions_analyzed"] == 0
        assert result["corrections"] == []
        assert result["thrash_sessions"] == []

    def test_analyze_filters_to_target_date(self, tmp_path):
        """Only sessions from the target date are analyzed."""
        from scripts.daydream import analyze_sessions

        sessions_dir = tmp_path / "sessions"

        # Create a session with yesterday's date in chat.json
        session_dir = sessions_dir / "session_abc"
        session_dir.mkdir(parents=True)
        chat_data = {
            "session_id": "abc",
            "started_at": "2026-02-16T10:00:00",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
        }
        (session_dir / "chat.json").write_text(json.dumps(chat_data))

        # Create a session from a different day
        session_dir2 = sessions_dir / "session_def"
        session_dir2.mkdir(parents=True)
        chat_data2 = {
            "session_id": "def",
            "started_at": "2026-02-10T10:00:00",
            "messages": [{"role": "user", "content": "Old session"}],
        }
        (session_dir2 / "chat.json").write_text(json.dumps(chat_data2))

        result = analyze_sessions(sessions_dir, "2026-02-16")
        assert result["sessions_analyzed"] == 1

    def test_detect_user_corrections(self, tmp_path):
        """Detects correction patterns in user messages."""
        from scripts.daydream import analyze_sessions

        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "session_corrections"
        session_dir.mkdir(parents=True)

        chat_data = {
            "session_id": "corrections",
            "started_at": "2026-02-16T10:00:00",
            "messages": [
                {"role": "user", "content": "Do X"},
                {"role": "assistant", "content": "Did X"},
                {"role": "user", "content": "No, I meant Y"},
                {"role": "assistant", "content": "OK doing Y"},
                {"role": "user", "content": "That's wrong, do Z"},
            ],
        }
        (session_dir / "chat.json").write_text(json.dumps(chat_data))

        result = analyze_sessions(sessions_dir, "2026-02-16")
        assert len(result["corrections"]) >= 2

    def test_thrash_ratio_computation(self, tmp_path):
        """Computes thrash ratio from tool_use.jsonl."""
        from scripts.daydream import analyze_sessions

        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "session_thrash"
        session_dir.mkdir(parents=True)

        chat_data = {
            "session_id": "thrash",
            "started_at": "2026-02-16T10:00:00",
            "messages": [{"role": "user", "content": "Do stuff"}],
        }
        (session_dir / "chat.json").write_text(json.dumps(chat_data))

        # 10 tool calls, only 2 successes = high thrash
        tool_lines = []
        for i in range(10):
            entry = {
                "tool": "bash",
                "success": i < 2,
                "timestamp": "2026-02-16T10:00:00",
            }
            tool_lines.append(json.dumps(entry))
        (session_dir / "tool_use.jsonl").write_text("\n".join(tool_lines))

        result = analyze_sessions(sessions_dir, "2026-02-16")
        assert len(result["thrash_sessions"]) == 1
        session_info = result["thrash_sessions"][0]
        assert session_info["tool_calls"] == 10
        assert session_info["successes"] == 2

    def test_caps_at_10_sessions(self, tmp_path):
        """Caps analysis at 10 most interesting sessions."""
        from scripts.daydream import analyze_sessions

        sessions_dir = tmp_path / "sessions"
        for i in range(15):
            session_dir = sessions_dir / f"session_{i:03d}"
            session_dir.mkdir(parents=True)
            chat_data = {
                "session_id": f"s{i}",
                "started_at": "2026-02-16T10:00:00",
                "messages": [
                    {"role": "user", "content": "Do something"},
                    {"role": "user", "content": "No, I meant something else"},
                ],
            }
            (session_dir / "chat.json").write_text(json.dumps(chat_data))

        result = analyze_sessions(sessions_dir, "2026-02-16")
        assert result["sessions_analyzed"] <= 10

    def test_handles_malformed_chat_json(self, tmp_path):
        """Gracefully handles invalid JSON in chat.json."""
        from scripts.daydream import analyze_sessions

        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "session_bad"
        session_dir.mkdir(parents=True)
        (session_dir / "chat.json").write_text("NOT VALID JSON {{{")

        result = analyze_sessions(sessions_dir, "2026-02-16")
        assert result["sessions_analyzed"] == 0


# --- LLM Reflection Tests (Step 8) ---


class TestLLMReflection:
    """Tests for LLM reflection step."""

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("scripts.daydream.anthropic")
    def test_reflection_calls_haiku(self, mock_anthropic_module):
        """Calls Claude Haiku with session analysis data."""
        from scripts.daydream import run_llm_reflection

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
        from scripts.daydream import run_llm_reflection

        # Remove the key if present
        os.environ.pop("ANTHROPIC_API_KEY", None)
        result = run_llm_reflection(
            {"sessions_analyzed": 0, "corrections": [], "thrash_sessions": []}
        )
        assert result == []

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("scripts.daydream.anthropic")
    def test_reflection_handles_api_error(self, mock_anthropic_module):
        """Returns empty list on API failure."""
        from scripts.daydream import run_llm_reflection

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API down")

        result = run_llm_reflection(
            {"sessions_analyzed": 1, "corrections": [], "thrash_sessions": []}
        )
        assert result == []

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("scripts.daydream.anthropic")
    def test_reflection_handles_malformed_response(self, mock_anthropic_module):
        """Returns empty list when API response is not valid JSON."""
        from scripts.daydream import run_llm_reflection

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
        from scripts.daydream import run_llm_reflection

        result = run_llm_reflection(
            {"sessions_analyzed": 0, "corrections": [], "thrash_sessions": []}
        )
        assert result == []


# --- Memory Consolidation Tests (Step 9) ---


class TestMemoryConsolidation:
    """Tests for lessons learned consolidation."""

    def test_appends_lessons_to_jsonl(self, tmp_path):
        """Writes reflection output to lessons_learned.jsonl."""
        from scripts.daydream import consolidate_memory

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        lessons_file = data_dir / "lessons_learned.jsonl"

        reflections = [
            {
                "category": "misunderstanding",
                "summary": "User correction needed",
                "pattern": "Jumped to code before confirming",
                "prevention": "Ask first",
                "source_session": "abc",
            }
        ]

        consolidate_memory(reflections, "2026-02-16", lessons_file)

        lines = lessons_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["date"] == "2026-02-16"
        assert entry["category"] == "misunderstanding"
        assert entry["validated"] == 0

    def test_deduplicates_by_pattern(self, tmp_path):
        """Does not add duplicate patterns."""
        from scripts.daydream import consolidate_memory

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        lessons_file = data_dir / "lessons_learned.jsonl"

        # Write existing entry
        existing = {
            "date": "2026-02-15",
            "category": "misunderstanding",
            "summary": "Old entry",
            "pattern": "Jumped to code before confirming",
            "prevention": "Ask first",
            "source_session": "old",
            "validated": 0,
        }
        lessons_file.write_text(json.dumps(existing) + "\n")

        reflections = [
            {
                "category": "misunderstanding",
                "summary": "New but same pattern",
                "pattern": "Jumped to code before confirming",
                "prevention": "Ask first",
                "source_session": "abc",
            }
        ]

        consolidate_memory(reflections, "2026-02-16", lessons_file)

        lines = lessons_file.read_text().strip().split("\n")
        assert len(lines) == 1  # Still just one entry

    def test_prunes_old_entries(self, tmp_path):
        """Removes entries older than 90 days."""
        from scripts.daydream import consolidate_memory

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        lessons_file = data_dir / "lessons_learned.jsonl"

        old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        recent_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

        entries = [
            {
                "date": old_date,
                "category": "old",
                "summary": "Should be pruned",
                "pattern": "old pattern",
                "prevention": "n/a",
                "source_session": "old",
                "validated": 0,
            },
            {
                "date": recent_date,
                "category": "recent",
                "summary": "Should remain",
                "pattern": "recent pattern",
                "prevention": "n/a",
                "source_session": "recent",
                "validated": 0,
            },
        ]
        lessons_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        consolidate_memory([], "2026-02-16", lessons_file)

        lines = lessons_file.read_text().strip().split("\n")
        assert len(lines) == 1
        remaining = json.loads(lines[0])
        assert remaining["category"] == "recent"

    def test_creates_file_if_not_exists(self, tmp_path):
        """Creates lessons_learned.jsonl if it does not exist."""
        from scripts.daydream import consolidate_memory

        lessons_file = tmp_path / "data" / "lessons_learned.jsonl"
        reflections = [
            {
                "category": "test",
                "summary": "Test entry",
                "pattern": "test pattern",
                "prevention": "n/a",
                "source_session": "test",
            }
        ]
        consolidate_memory(reflections, "2026-02-16", lessons_file)
        assert lessons_file.exists()

    def test_handles_empty_reflections(self, tmp_path):
        """No-op with empty reflections (but still prunes)."""
        from scripts.daydream import consolidate_memory

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        lessons_file = data_dir / "lessons_learned.jsonl"
        lessons_file.write_text("")

        consolidate_memory([], "2026-02-16", lessons_file)
        # File should exist but be empty (or have no valid lines)
        content = lessons_file.read_text().strip()
        assert content == ""


# --- Step 3 Sentry Check ---


class TestSentryCheck:
    """Tests for Sentry check step."""

    @pytest.mark.asyncio
    async def test_sentry_skips_gracefully(self, tmp_path):
        """Sentry check logs skip message and continues."""
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        runner.state.findings = {}
        await runner.step_check_sentry()
        assert "sentry" in runner.state.findings
        assert any("skipped" in f.lower() for f in runner.state.findings["sentry"])


# --- Step 4 Task Cleanup ---


class TestTaskCleanup:
    """Tests for task cleanup step using gh CLI."""

    @pytest.mark.asyncio
    @patch("scripts.daydream.subprocess.run")
    async def test_task_cleanup_calls_gh(self, mock_run):
        """Step 4 calls gh issue list."""
        from scripts.daydream import DaydreamRunner

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="42\tbug title\topen\tbug\n",
        )

        runner = DaydreamRunner()
        runner.state.findings = {}
        await runner.step_clean_tasks()

        # Should have called gh
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "list" in call_args

    @pytest.mark.asyncio
    @patch("scripts.daydream.subprocess.run")
    async def test_task_cleanup_handles_gh_failure(self, mock_run):
        """Step 4 handles gh CLI failure gracefully."""
        from scripts.daydream import DaydreamRunner

        mock_run.side_effect = FileNotFoundError("gh not found")

        runner = DaydreamRunner()
        runner.state.findings = {}
        await runner.step_clean_tasks()
        # Should not raise


# --- Step 10 GitHub Issue ---


class TestGitHubIssueStep:
    """Tests for GitHub issue creation step."""

    @pytest.mark.asyncio
    @patch("scripts.daydream.create_daydream_issue")
    async def test_creates_issue_per_project_with_findings(self, mock_create):
        """Step 10 creates GitHub issue for each project that has namespaced findings."""
        from unittest.mock import AsyncMock

        from scripts.daydream import DaydreamRunner

        mock_create.return_value = "https://github.com/org/repo/issues/1"
        runner = DaydreamRunner()

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
    @patch("scripts.daydream.create_daydream_issue")
    async def test_skips_issue_when_no_findings(self, mock_create):
        """Step 10 skips when no findings."""
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        runner.state.findings = {}
        await runner.step_create_github_issue()
        mock_create.assert_not_called()


# --- Step 5 Audit Docs ---


class TestStepAuditDocs:
    """Tests for step_audit_docs registration and delegation to DocsAuditor."""

    def test_step_audit_docs_is_registered(self):
        """step_audit_docs is registered as step 5 in runner.steps."""
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        assert (5, "Audit Documentation", runner.step_audit_docs) in runner.steps

    @pytest.mark.asyncio
    @patch("scripts.daydream.asyncio.to_thread")
    @patch("scripts.daydream.DocsAuditor")
    async def test_step_audit_docs_calls_docs_auditor(
        self, mock_docs_auditor_cls, mock_to_thread
    ):
        """step_audit_docs instantiates DocsAuditor and delegates to run() via asyncio.to_thread."""
        from unittest.mock import MagicMock

        from scripts.daydream import DaydreamRunner

        # Build a fake summary with the attributes step_audit_docs inspects
        mock_summary = MagicMock()
        mock_summary.skipped = False
        mock_summary.skip_reason = ""
        mock_summary.updated = []
        mock_summary.deleted = []
        mock_summary.kept = ["doc1.md"]

        mock_to_thread.return_value = mock_summary

        runner = DaydreamRunner()
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

    def test_load_ignore_log_empty_when_file_missing(self, tmp_path):
        """load_ignore_log returns empty list when file doesn't exist."""
        from scripts.daydream import load_ignore_log, IGNORE_LOG_FILE
        import scripts.daydream as daydream_mod

        original = daydream_mod.IGNORE_LOG_FILE
        daydream_mod.IGNORE_LOG_FILE = tmp_path / "daydream_ignore.jsonl"
        try:
            result = load_ignore_log()
            assert result == []
        finally:
            daydream_mod.IGNORE_LOG_FILE = original

    def test_load_ignore_log_filters_expired(self, tmp_path):
        """load_ignore_log excludes entries past their ignored_until date."""
        import json
        from datetime import date, timedelta
        import scripts.daydream as daydream_mod

        ignore_file = tmp_path / "daydream_ignore.jsonl"
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        entries = [
            {"pattern": "expired", "ignored_until": yesterday, "reason": ""},
            {"pattern": "active", "ignored_until": tomorrow, "reason": ""},
            {"pattern": "today", "ignored_until": today, "reason": ""},
        ]
        ignore_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        original = daydream_mod.IGNORE_LOG_FILE
        daydream_mod.IGNORE_LOG_FILE = ignore_file
        try:
            result = daydream_mod.load_ignore_log()
            patterns = [e["pattern"] for e in result]
            assert "expired" not in patterns
            assert "active" in patterns
            assert "today" in patterns
        finally:
            daydream_mod.IGNORE_LOG_FILE = original

    def test_prune_ignore_log_removes_expired(self, tmp_path):
        """prune_ignore_log removes expired entries and keeps active ones."""
        import json
        from datetime import date, timedelta
        import scripts.daydream as daydream_mod

        ignore_file = tmp_path / "daydream_ignore.jsonl"
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        entries = [
            {"pattern": "expired", "ignored_until": yesterday},
            {"pattern": "active", "ignored_until": tomorrow},
        ]
        ignore_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        original = daydream_mod.IGNORE_LOG_FILE
        daydream_mod.IGNORE_LOG_FILE = ignore_file
        try:
            daydream_mod.prune_ignore_log()
            remaining = [json.loads(l) for l in ignore_file.read_text().splitlines() if l.strip()]
            patterns = [e["pattern"] for e in remaining]
            assert "expired" not in patterns
            assert "active" in patterns
        finally:
            daydream_mod.IGNORE_LOG_FILE = original

    def test_is_ignored_matches_substring(self):
        """is_ignored returns True when entry_pattern is a substring of pattern."""
        from scripts.daydream import is_ignored

        entries = [{"pattern": "null pointer"}]
        assert is_ignored("causes null pointer dereference", entries) is True
        assert is_ignored("unrelated bug", entries) is False

    def test_is_ignored_case_insensitive(self):
        """is_ignored does case-insensitive matching."""
        from scripts.daydream import is_ignored

        entries = [{"pattern": "NULL POINTER"}]
        assert is_ignored("null pointer error", entries) is True


class TestConfidenceScorer:
    """Tests for is_high_confidence helper."""

    def test_high_confidence_all_three_criteria(self):
        """Returns True when all three criteria are met."""
        from scripts.daydream import is_high_confidence

        r = {
            "category": "code_bug",
            "prevention": "Always validate input",
            "pattern": "Missing null check before dereferencing",
        }
        assert is_high_confidence(r) is True

    def test_high_confidence_two_of_three(self):
        """Returns True when exactly two criteria are met."""
        from scripts.daydream import is_high_confidence

        # code_bug + long pattern, no prevention
        r = {
            "category": "code_bug",
            "prevention": "",
            "pattern": "Missing null check before dereferencing pointer",
        }
        assert is_high_confidence(r) is True

    def test_not_high_confidence_one_criterion(self):
        """Returns False when only one criterion is met."""
        from scripts.daydream import is_high_confidence

        r = {
            "category": "misunderstanding",
            "prevention": "",
            "pattern": "short",
        }
        assert is_high_confidence(r) is False

    def test_not_high_confidence_all_missing(self):
        """Returns False when no criteria are met."""
        from scripts.daydream import is_high_confidence

        r = {"category": "misunderstanding", "prevention": "", "pattern": ""}
        assert is_high_confidence(r) is False


class TestAutoFixStep:
    """Tests for step_auto_fix_bugs."""

    @pytest.mark.asyncio
    async def test_auto_fix_disabled_by_env(self, monkeypatch):
        """step_auto_fix_bugs skips when DAYDREAM_AUTO_FIX_ENABLED=false."""
        monkeypatch.setenv("DAYDREAM_AUTO_FIX_ENABLED", "false")
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
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
        monkeypatch.setenv("DAYDREAM_AUTO_FIX_ENABLED", "true")
        from unittest.mock import patch, MagicMock
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        runner.state._dry_run = True
        runner.state.reflections = [
            {
                "category": "code_bug",
                "summary": "Null pointer in loop",
                "pattern": "null pointer dereference in loop body",
                "prevention": "Always validate before use",
            }
        ]

        with patch("scripts.daydream.subprocess.run") as mock_run, \
             patch("scripts.daydream.has_existing_github_work", return_value=False), \
             patch("scripts.daydream.load_ignore_log", return_value=[]), \
             patch("scripts.daydream.prune_ignore_log"):
            await runner.step_auto_fix_bugs()
            # subprocess.run should NOT have been called for the fix (only gh dedup checks)
            claude_calls = [
                c for c in mock_run.call_args_list
                if c[0][0] and c[0][0][0] == "claude"
            ]
            assert len(claude_calls) == 0

        attempts = runner.state.auto_fix_attempts
        assert len(attempts) == 1
        assert attempts[0]["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_auto_fix_skips_ignored_pattern(self, monkeypatch):
        """Skips a reflection whose pattern is in the ignore log."""
        monkeypatch.setenv("DAYDREAM_AUTO_FIX_ENABLED", "true")
        from unittest.mock import patch
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
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

        with patch("scripts.daydream.load_ignore_log", return_value=ignore_entries), \
             patch("scripts.daydream.prune_ignore_log"), \
             patch("scripts.daydream.subprocess.run") as mock_run:
            await runner.step_auto_fix_bugs()
            # subprocess.run should NOT be called with claude
            claude_calls = [
                c for c in mock_run.call_args_list
                if c[0][0] and c[0][0][0] == "claude"
            ]
            assert len(claude_calls) == 0

        attempts = runner.state.auto_fix_attempts
        assert len(attempts) == 1
        assert attempts[0]["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_auto_fix_skips_low_confidence(self, monkeypatch):
        """Skips reflections that don't meet the 2-of-3 confidence criteria."""
        monkeypatch.setenv("DAYDREAM_AUTO_FIX_ENABLED", "true")
        from unittest.mock import patch
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        runner.state._dry_run = True
        runner.state.reflections = [
            {
                "category": "misunderstanding",  # not code_bug
                "summary": "User misunderstood",
                "pattern": "short",  # too short
                "prevention": "",  # no prevention
            }
        ]

        with patch("scripts.daydream.load_ignore_log", return_value=[]), \
             patch("scripts.daydream.prune_ignore_log"):
            await runner.step_auto_fix_bugs()

        # No attempts because no candidates passed confidence filter
        assert runner.state.auto_fix_attempts == []
        progress = runner.state.step_progress.get("auto_fix_bugs", {})
        assert progress.get("candidates") == 0

    @pytest.mark.asyncio
    async def test_auto_fix_step_registered_as_step_8(self):
        """step_auto_fix_bugs is registered as step 8."""
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        step_nums = [s[0] for s in runner.steps]
        step_names = {s[0]: s[1] for s in runner.steps}
        assert 8 in step_nums
        assert step_names[8] == "Auto-Fix Bugs"

    @pytest.mark.asyncio
    async def test_steps_renumbered_correctly(self):
        """Memory Consolidation is step 9, Produce Daily Report is 10, GitHub is 11."""
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        step_names = {s[0]: s[1] for s in runner.steps}
        assert step_names.get(9) == "Memory Consolidation"
        assert step_names.get(10) == "Produce Daily Report"
        assert step_names.get(11) == "GitHub Issue Creation"

    @pytest.mark.asyncio
    async def test_auto_fix_skips_duplicate_github_work(self, monkeypatch):
        """Skips patterns with an existing open issue or PR."""
        monkeypatch.setenv("DAYDREAM_AUTO_FIX_ENABLED", "true")
        from unittest.mock import patch
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
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

        with patch("scripts.daydream.load_ignore_log", return_value=[]), \
             patch("scripts.daydream.prune_ignore_log"), \
             patch("scripts.daydream.has_existing_github_work", return_value=True), \
             patch("scripts.daydream.subprocess.run") as mock_run:
            await runner.step_auto_fix_bugs()
            claude_calls = [
                c for c in mock_run.call_args_list
                if c[0][0] and c[0][0][0] == "claude"
            ]
            assert len(claude_calls) == 0

        attempts = runner.state.auto_fix_attempts
        assert len(attempts) == 1
        assert attempts[0]["status"] == "duplicate"


class TestDaydreamStateAutoFixField:
    """Tests for new auto_fix_attempts field on DaydreamState."""

    def test_auto_fix_attempts_defaults_to_empty_list(self):
        """DaydreamState.auto_fix_attempts defaults to []."""
        from scripts.daydream import DaydreamState

        state = DaydreamState()
        assert state.auto_fix_attempts == []

    def test_auto_fix_attempts_persisted_in_serialization(self):
        """auto_fix_attempts survives asdict round-trip."""
        from dataclasses import asdict
        from scripts.daydream import DaydreamState

        state = DaydreamState()
        state.auto_fix_attempts = [{"pattern": "foo", "status": "dry_run"}]
        d = asdict(state)
        assert d["auto_fix_attempts"] == [{"pattern": "foo", "status": "dry_run"}]


class TestCLIFlags:
    """Tests for CLI --dry-run and --ignore flags."""

    def test_ignore_flag_appends_to_ignore_log(self, tmp_path, monkeypatch):
        """--ignore appends a new entry to IGNORE_LOG_FILE."""
        import json
        import sys
        import scripts.daydream as daydream_mod

        ignore_file = tmp_path / "daydream_ignore.jsonl"
        monkeypatch.setattr(daydream_mod, "IGNORE_LOG_FILE", ignore_file)

        # Simulate CLI: python daydream.py --ignore "some bug pattern" --reason "known issue"
        monkeypatch.setattr(sys, "argv", [
            "daydream.py",
            "--ignore", "some bug pattern",
            "--reason", "known issue",
        ])

        import asyncio
        asyncio.run(daydream_mod.main())

        assert ignore_file.exists()
        lines = [l for l in ignore_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["pattern"] == "some bug pattern"
        assert entry["reason"] == "known issue"
        assert "ignored_until" in entry

    def test_dry_run_flag_sets_state(self, tmp_path, monkeypatch):
        """--dry-run sets runner.state._dry_run = True."""
        import sys
        import asyncio
        from unittest.mock import patch, AsyncMock
        import scripts.daydream as daydream_mod

        monkeypatch.setattr(sys, "argv", ["daydream.py", "--dry-run"])

        captured_runner = {}

        original_run = daydream_mod.DaydreamRunner.run

        async def fake_run(self):
            captured_runner["instance"] = self

        with patch.object(daydream_mod.DaydreamRunner, "run", fake_run):
            asyncio.run(daydream_mod.main())

        assert captured_runner["instance"].state._dry_run is True
