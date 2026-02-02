"""Unit tests for agent/health_check.py watchdog logic."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.health_check import (
    CHECK_INTERVAL,
    _read_recent_activity,
    _summarize_input,
    _tool_counts,
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
        assert _summarize_input("Read", {"file_path": "/a/b.py"}) == "/a/b.py"

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

        with patch(
            "agent.health_check._judge_health", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = {"healthy": True, "reason": "making progress"}
            result = await watchdog_hook(input_data, None, None)

        assert result["continue_"] is True
        mock_judge.assert_called_once()
        transcript.unlink()

    @pytest.mark.asyncio
    async def test_blocks_on_unhealthy(self):
        """Hook should block when judge says unhealthy."""
        transcript = _make_transcript(
            [("Bash", {"command": "git status"}) for _ in range(CHECK_INTERVAL)]
        )
        input_data = {"session_id": "test-3", "transcript_path": str(transcript)}
        _tool_counts["test-3"] = CHECK_INTERVAL - 1

        with patch(
            "agent.health_check._judge_health", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = {"healthy": False, "reason": "stuck in loop"}
            result = await watchdog_hook(input_data, None, None)

        assert result.get("continue_") is False
        assert "block" in result.get("decision", "")
        assert "stuck in loop" in result.get("stopReason", "")
        transcript.unlink()

    @pytest.mark.asyncio
    async def test_continues_on_judge_error(self):
        """Hook should never block due to its own errors."""
        input_data = {"session_id": "test-4", "transcript_path": "/nonexistent"}
        _tool_counts["test-4"] = CHECK_INTERVAL - 1

        with patch(
            "agent.health_check._judge_health", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.side_effect = RuntimeError("API down")
            result = await watchdog_hook(input_data, None, None)

        assert result["continue_"] is True
