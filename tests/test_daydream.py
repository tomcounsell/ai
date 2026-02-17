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
    async def test_creates_issue_with_findings(self, mock_create):
        """Step 10 creates GitHub issue when there are findings."""
        from scripts.daydream import DaydreamRunner

        mock_create.return_value = True
        runner = DaydreamRunner()
        runner.state.findings = {"test": ["finding 1"]}
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
