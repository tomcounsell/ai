"""Unit tests for agent/health_check.py watchdog logic."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.health_check import (
    CHECK_INTERVAL,
    JUDGE_PROMPT,
    _compute_activity_stats,
    _extract_gh_commands,
    _get_session_context,
    _read_recent_activity,
    _summarize_input,
    _tool_counts,
    _write_activity_stream,
    watchdog_hook,
)


def _make_transcript(tool_calls: list[tuple[str, dict]]) -> Path:
    """Create a temporary transcript JSONL with the given tool calls."""
    lines = []
    for name, inp in tool_calls:
        entry = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]},
        }
        lines.append(json.dumps(entry))

    path = Path(tempfile.mktemp(suffix=".jsonl"))
    path.write_text("\n".join(lines))
    return path


class TestReadRecentActivity:
    def test_returns_tool_summaries(self):
        path = _make_transcript(
            [
                ("Bash", {"command": "git status"}),
                ("Read", {"file_path": "/foo/bar.py"}),
                ("Grep", {"pattern": "def main"}),
            ]
        )
        result = _read_recent_activity(str(path))
        assert "Bash: git status" in result
        assert "Read: /foo/bar.py" in result
        assert 'Grep: pattern="def main"' in result
        path.unlink()

    def test_missing_transcript(self):
        result = _read_recent_activity("/nonexistent/path.jsonl")
        assert "not found" in result

    def test_empty_transcript(self):
        path = Path(tempfile.mktemp(suffix=".jsonl"))
        path.write_text("")
        result = _read_recent_activity(str(path))
        assert "no tool calls" in result
        path.unlink()

    def test_non_assistant_entries_ignored(self):
        path = Path(tempfile.mktemp(suffix=".jsonl"))
        entries = [
            json.dumps({"type": "user", "message": {"content": "hello"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            }
                        ]
                    },
                }
            ),
        ]
        path.write_text("\n".join(entries))
        result = _read_recent_activity(str(path))
        assert "Bash: ls" in result
        # User entry should not appear
        assert "hello" not in result
        path.unlink()


class TestSummarizeInput:
    def test_bash_truncates_long_commands(self):
        cmd = "x" * 200
        result = _summarize_input("Bash", {"command": cmd})
        assert len(result) <= 124  # 120 + "..."
        assert result.endswith("...")

    def test_read_returns_path(self):
        """Read without offset/limit returns just the path."""
        assert _summarize_input("Read", {"file_path": "/a/b.py"}) == "/a/b.py"

    def test_read_with_offset_limit(self):
        """Read with offset/limit includes them in brackets."""
        result = _summarize_input("Read", {"file_path": "/f.py", "offset": 100, "limit": 50})
        assert "offset=100" in result
        assert "limit=50" in result
        assert "/f.py" in result

    def test_read_with_only_offset(self):
        """Read with only offset still includes it."""
        result = _summarize_input("Read", {"file_path": "/f.py", "offset": 200})
        assert "offset=200" in result
        assert "limit" not in result

    def test_edit_with_old_string_length(self):
        """Edit includes old_string length."""
        result = _summarize_input("Edit", {"file_path": "/f.py", "old_string": "hello world"})
        assert "old_string len=11" in result
        assert "/f.py" in result

    def test_edit_without_old_string(self):
        """Edit without old_string returns just the path."""
        result = _summarize_input("Edit", {"file_path": "/f.py"})
        assert result == "/f.py"

    def test_write_returns_path_only(self):
        """Write returns just the path (no extra context)."""
        result = _summarize_input("Write", {"file_path": "/f.py"})
        assert result == "/f.py"

    def test_grep_returns_pattern(self):
        assert _summarize_input("Grep", {"pattern": "foo"}) == 'pattern="foo"'

    def test_unknown_tool_uses_json(self):
        result = _summarize_input("CustomTool", {"key": "value"})
        assert "key" in result


class TestWatchdogHook:
    @pytest.fixture(autouse=True)
    def clear_counts(self):
        _tool_counts.clear()
        yield
        _tool_counts.clear()

    @pytest.fixture(autouse=True)
    def _no_env_session(self, monkeypatch):
        """Ensure VALOR_SESSION_ID env var doesn't override test session IDs."""
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

    @pytest.mark.asyncio
    async def test_skips_before_interval(self):
        """Hook should return continue=True without calling judge before interval."""
        input_data = {"session_id": "test-1", "transcript_path": ""}
        result = await watchdog_hook(input_data, None, None)
        assert result["continue_"] is True
        assert _tool_counts["test-1"] == 1

    @pytest.mark.asyncio
    async def test_fires_at_interval(self):
        """Hook should call judge at CHECK_INTERVAL."""
        transcript = _make_transcript(
            [("Bash", {"command": f"cmd-{i}"}) for i in range(CHECK_INTERVAL)]
        )
        input_data = {"session_id": "test-2", "transcript_path": str(transcript)}

        # Fast-forward counter to CHECK_INTERVAL - 1
        _tool_counts["test-2"] = CHECK_INTERVAL - 1

        with patch("agent.health_check._judge_health", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = {"healthy": True, "reason": "making progress"}
            result = await watchdog_hook(input_data, None, None)

        assert result["continue_"] is True
        mock_judge.assert_called_once()
        transcript.unlink()

    @pytest.mark.asyncio
    async def test_blocks_on_unhealthy(self):
        """Hook should inject stop directive and set unhealthy flag when judge says unhealthy."""
        transcript = _make_transcript(
            [("Bash", {"command": "git status"}) for _ in range(CHECK_INTERVAL)]
        )
        input_data = {"session_id": "test-3", "transcript_path": str(transcript)}
        _tool_counts["test-3"] = CHECK_INTERVAL - 1

        with (
            patch("agent.health_check._judge_health", new_callable=AsyncMock) as mock_judge,
            patch("agent.health_check._set_unhealthy") as mock_set,
        ):
            mock_judge.return_value = {"healthy": False, "reason": "stuck in loop"}
            result = await watchdog_hook(input_data, None, None)

        # PostToolUse can't block, so we inject additionalContext instead
        hook_output = result.get("hookSpecificOutput", {})
        assert hook_output.get("hookEventName") == "PostToolUse"
        assert "STOP" in hook_output.get("additionalContext", "")
        # Unhealthy flag set on model so nudge loop won't auto-continue
        mock_set.assert_called_once_with("test-3", "stuck in loop")
        transcript.unlink()

    @pytest.mark.asyncio
    async def test_continues_on_judge_error(self):
        """Hook should never block due to its own errors."""
        input_data = {"session_id": "test-4", "transcript_path": "/nonexistent"}
        _tool_counts["test-4"] = CHECK_INTERVAL - 1

        with patch("agent.health_check._judge_health", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = RuntimeError("API down")
            result = await watchdog_hook(input_data, None, None)

        assert result["continue_"] is True


class TestActivityStream:
    """Tests for _write_activity_stream."""

    def test_creates_directory_lazily(self):
        """Activity stream should create session directory on first write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                _write_activity_stream("lazy-session", "Bash", "ls", 1)
                session_dir = Path(tmpdir) / "logs" / "sessions" / "lazy-session"
                assert session_dir.exists()
            finally:
                os.chdir(original_cwd)

    def test_writes_valid_jsonl(self):
        """Each write should produce a valid JSON line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                _write_activity_stream("json-session", "Read", "/path/file.py", 5)
                activity_file = (
                    Path(tmpdir) / "logs" / "sessions" / "json-session" / "activity.jsonl"
                )
                assert activity_file.exists()
                entry = json.loads(activity_file.read_text().strip())
                assert entry["tool"] == "Read"
                assert entry["args"] == "/path/file.py"
                assert entry["n"] == 5
                assert "ts" in entry
            finally:
                os.chdir(original_cwd)

    def test_appends_multiple_entries(self):
        """Multiple writes should append to the same file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                _write_activity_stream("multi-session", "Bash", "cmd1", 1)
                _write_activity_stream("multi-session", "Read", "file", 2)
                _write_activity_stream("multi-session", "Grep", "pattern", 3)
                activity_file = (
                    Path(tmpdir) / "logs" / "sessions" / "multi-session" / "activity.jsonl"
                )
                lines = activity_file.read_text().strip().splitlines()
                assert len(lines) == 3
            finally:
                os.chdir(original_cwd)


class TestExtractGhCommands:
    """Tests for _extract_gh_commands."""

    def test_no_activity_file_returns_empty(self):
        result = _extract_gh_commands("nonexistent-session")
        assert result == []

    def test_extracts_gh_from_activity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                session_dir = Path(tmpdir) / "logs" / "sessions" / "gh-session"
                session_dir.mkdir(parents=True)
                entries = [
                    json.dumps({"tool": "Bash", "args": "gh pr list --state open", "n": 1}),
                    json.dumps({"tool": "Read", "args": "/some/file", "n": 2}),
                    json.dumps({"tool": "Bash", "args": "gh issue create --title bug", "n": 3}),
                ]
                (session_dir / "activity.jsonl").write_text("\n".join(entries))
                result = _extract_gh_commands("gh-session")
                assert len(result) == 2
                assert "gh pr list" in result[0]
            finally:
                os.chdir(original_cwd)


class TestGetSessionContext:
    """Tests for _get_session_context health check enrichment."""

    def test_returns_context_with_session_type(self):
        mock_session = MagicMock()
        mock_session.session_type = "dev"
        mock_session.message_text = "Build the auth module"

        mock_as_cls = MagicMock()
        mock_as_cls.query.filter.return_value = [mock_session]
        mock_module = MagicMock()
        mock_module.AgentSession = mock_as_cls

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            with patch("agent.health_check._extract_gh_commands", return_value=[]):
                with patch("agent.health_check._compute_activity_stats", return_value={
                    "tool_distribution": {"Read": 5, "Edit": 3},
                    "commit_count": 1,
                    "total_tool_count": 8,
                }):
                    result = _get_session_context("dev-session")
                    assert "dev" in result
                    assert "Build the auth module" in result
                    assert "Tool distribution:" in result
                    assert "5 Read" in result
                    assert "Total tool calls: 8" in result
                    assert "Commits: 1" in result

    def test_handles_none_fields(self):
        mock_session = MagicMock()
        mock_session.session_type = None
        mock_session.message_text = None

        mock_as_cls = MagicMock()
        mock_as_cls.query.filter.return_value = [mock_session]
        mock_module = MagicMock()
        mock_module.AgentSession = mock_as_cls

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            with patch("agent.health_check._extract_gh_commands", return_value=[]):
                with patch("agent.health_check._compute_activity_stats", return_value={
                    "tool_distribution": {},
                    "commit_count": 0,
                    "total_tool_count": 0,
                }):
                    result = _get_session_context("none-session")
                    assert isinstance(result, str)
                    # Stats block still present even with None fields
                    assert "Total tool calls:" in result

    def test_no_session_returns_empty(self):
        mock_as_cls = MagicMock()
        mock_as_cls.query.filter.return_value = []
        mock_module = MagicMock()
        mock_module.AgentSession = mock_as_cls

        with patch.dict("sys.modules", {"models.agent_session": mock_module}):
            result = _get_session_context("missing-session")
            assert result == ""


class TestComputeActivityStats:
    """Tests for _compute_activity_stats."""

    def test_empty_activity_returns_defaults(self):
        """No activity file returns zero stats."""
        stats = _compute_activity_stats("nonexistent-session-xyz")
        assert stats["tool_distribution"] == {}
        assert stats["commit_count"] == 0
        assert stats["total_tool_count"] == 0

    def test_computes_tool_distribution(self):
        """Correctly counts tool calls by name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                session_dir = Path(tmpdir) / "logs" / "sessions" / "stats-session"
                session_dir.mkdir(parents=True)
                entries = [
                    json.dumps({"tool": "Read", "args": "/f1.py", "n": 1}),
                    json.dumps({"tool": "Read", "args": "/f2.py", "n": 2}),
                    json.dumps({"tool": "Edit", "args": "/f1.py", "n": 3}),
                    json.dumps({"tool": "Bash", "args": "git commit -m fix", "n": 4}),
                    json.dumps({"tool": "Bash", "args": "ls", "n": 5}),
                ]
                (session_dir / "activity.jsonl").write_text("\n".join(entries))
                stats = _compute_activity_stats("stats-session")
                assert stats["tool_distribution"]["Read"] == 2
                assert stats["tool_distribution"]["Edit"] == 1
                assert stats["tool_distribution"]["Bash"] == 2
                assert stats["commit_count"] == 1
            finally:
                os.chdir(original_cwd)

    def test_skips_malformed_jsonl(self):
        """Malformed JSONL lines are skipped gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                session_dir = Path(tmpdir) / "logs" / "sessions" / "bad-json-session"
                session_dir.mkdir(parents=True)
                entries = [
                    "NOT VALID JSON",
                    json.dumps({"tool": "Read", "args": "/f.py", "n": 1}),
                    "{broken",
                ]
                (session_dir / "activity.jsonl").write_text("\n".join(entries))
                stats = _compute_activity_stats("bad-json-session")
                assert stats["tool_distribution"]["Read"] == 1
                assert stats["commit_count"] == 0
            finally:
                os.chdir(original_cwd)

    def test_includes_total_from_tool_counts(self):
        """total_tool_count comes from _tool_counts dict."""
        _tool_counts["counting-session"] = 42
        try:
            stats = _compute_activity_stats("counting-session")
            assert stats["total_tool_count"] == 42
        finally:
            _tool_counts.pop("counting-session", None)


class TestJudgePromptEnrichment:
    """Tests for enriched JUDGE_PROMPT format."""

    def test_prompt_has_session_context_placeholder(self):
        assert "{session_context}" in JUDGE_PROMPT

    def test_prompt_includes_pattern_guidance(self):
        """JUDGE_PROMPT includes legitimate pattern guidance."""
        assert "legitimate" in JUDGE_PROMPT.lower() or "pattern" in JUDGE_PROMPT.lower()
        assert "chunked" in JUDGE_PROMPT.lower() or "offset" in JUDGE_PROMPT.lower()
        assert "commit" in JUDGE_PROMPT.lower()

    def test_prompt_formats_with_context(self):
        formatted = JUDGE_PROMPT.format(
            count=20,
            activity="- Bash: ls",
            session_context="This is a chat session working on: test\n\n",
        )
        assert "chat session" in formatted
        assert "test" in formatted
        assert "legitimate" in formatted.lower() or "pattern" in formatted.lower()

    def test_prompt_formats_without_context(self):
        formatted = JUDGE_PROMPT.format(
            count=20,
            activity="- Bash: ls",
            session_context="",
        )
        assert "Recent activity" in formatted
