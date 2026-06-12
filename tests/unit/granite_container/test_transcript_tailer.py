"""Unit tests for the transcript tailer module.

Tests the incremental JSONL-reading logic that populates dashboard
telemetry for granite PTY sessions (#1536 seam / sdlc-1648).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent.granite_container.transcript_tailer import (
    TranscriptTelemetry,
    fold_events,
    read_transcript_telemetry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user_entry() -> dict:
    return {"type": "user", "timestamp": "2024-01-01T00:00:00.000Z"}


def _make_assistant_entry(
    *, tool_names: list[str] | None = None, thinking: str | None = None, usage: dict | None = None
) -> dict:
    content = []
    if tool_names:
        for name in tool_names:
            content.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_{name}",
                    "name": name,
                    "input": {},
                }
            )
    if thinking is not None:
        content.append({"type": "thinking", "thinking": thinking})
    obj: dict = {
        "type": "assistant",
        "timestamp": "2024-01-01T00:01:00.000Z",
        "message": {"content": content, "role": "assistant"},
    }
    if usage is not None:
        obj["message"]["usage"] = usage
    return obj


def _write_jsonl(f, entries: list[dict]) -> None:
    """Write JSONL entries to a file object."""
    for entry in entries:
        f.write(json.dumps(entry) + "\n")
    f.flush()


# ---------------------------------------------------------------------------
# 1. Empty file → zero/None telemetry, no exception
# ---------------------------------------------------------------------------


class TestEmptyFile(unittest.TestCase):
    def test_empty_file_returns_zero_telemetry(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            result = read_transcript_telemetry(path)
            self.assertEqual(result.turn_count, 0)
            self.assertEqual(result.tool_call_count, 0)
            self.assertEqual(result.total_input_tokens, 0)
            self.assertEqual(result.total_output_tokens, 0)
            self.assertEqual(result.total_cache_read_tokens, 0)
            self.assertIsNone(result.current_tool_name)
            self.assertIsNone(result.last_tool_use_at)
            self.assertIsNone(result.recent_thinking_excerpt)
            self.assertIsNone(result.tailer_last_read_at)
        finally:
            os.unlink(path)

    def test_empty_file_no_exception(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            # Must not raise
            result = read_transcript_telemetry(path)
            self.assertIsInstance(result, TranscriptTelemetry)
        finally:
            os.unlink(path)

    def test_empty_file_with_prev_state_preserves_offset(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            prev = TranscriptTelemetry(byte_offset=0)
            result = read_transcript_telemetry(path, prev_state=prev)
            self.assertEqual(result.byte_offset, 0)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 2. Non-existent file → no exception, returns empty telemetry
# ---------------------------------------------------------------------------


class TestNonExistentFile(unittest.TestCase):
    def test_missing_file_no_exception(self) -> None:
        result = read_transcript_telemetry("/nonexistent/path/to/file.jsonl")
        self.assertIsInstance(result, TranscriptTelemetry)

    def test_missing_file_returns_zero_counters(self) -> None:
        result = read_transcript_telemetry("/nonexistent/path.jsonl")
        self.assertEqual(result.turn_count, 0)
        self.assertEqual(result.tool_call_count, 0)
        self.assertIsNone(result.current_tool_name)

    def test_missing_file_with_prev_state_returns_prev(self) -> None:
        prev = TranscriptTelemetry(turn_count=5, tool_call_count=3, byte_offset=100)
        result = read_transcript_telemetry("/nonexistent/path.jsonl", prev_state=prev)
        # When file is missing, preserve previous state
        self.assertEqual(result.turn_count, 5)
        self.assertEqual(result.tool_call_count, 3)


# ---------------------------------------------------------------------------
# 3. Non-telemetry lines → counters 0, current_tool_name=None
# ---------------------------------------------------------------------------


class TestNonTelemetryLines(unittest.TestCase):
    def test_non_telemetry_lines_zero_counters(self) -> None:
        entries = [
            {"type": "ai-title", "title": "Some session"},
            {"type": "queue-operation", "op": "enqueue"},
            {"type": "permission-mode", "mode": "default"},
            {"type": "mode", "value": "auto"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(f, entries)
            path = f.name
        try:
            result = read_transcript_telemetry(path)
            self.assertEqual(result.turn_count, 0)
            self.assertEqual(result.tool_call_count, 0)
            self.assertIsNone(result.current_tool_name)
            self.assertIsNone(result.last_tool_use_at)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 4. Byte-offset incremental read: second call processes only new lines
# ---------------------------------------------------------------------------


class TestIncrementalRead(unittest.TestCase):
    def test_incremental_no_double_count(self) -> None:
        """Feed a transcript, call once, append more, call again.

        Second call only counts new lines.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
            # First batch: 2 user entries + 1 tool use
            _write_jsonl(
                f,
                [
                    _make_user_entry(),
                    _make_user_entry(),
                    _make_assistant_entry(tool_names=["Bash"]),
                ],
            )

        try:
            # First read
            state1 = read_transcript_telemetry(path)
            self.assertEqual(state1.turn_count, 2)
            self.assertEqual(state1.tool_call_count, 1)
            self.assertGreater(state1.byte_offset, 0)

            # Append more lines
            with open(path, "a") as f:
                _write_jsonl(
                    f,
                    [
                        _make_user_entry(),
                        _make_assistant_entry(tool_names=["Read", "Edit"]),
                    ],
                )

            # Second read from previous state
            state2 = read_transcript_telemetry(path, prev_state=state1)
            # Should only count the NEW lines
            self.assertEqual(state2.turn_count, 3)  # 2 + 1 new user
            self.assertEqual(state2.tool_call_count, 3)  # 1 + 2 new tools
            self.assertGreater(state2.byte_offset, state1.byte_offset)
        finally:
            os.unlink(path)

    def test_third_call_no_new_data_preserves_state(self) -> None:
        """A third call with no new data preserves counters and advances byte_offset by 0."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(f, [_make_user_entry()])
            path = f.name
        try:
            state1 = read_transcript_telemetry(path)
            state2 = read_transcript_telemetry(path, prev_state=state1)
            # No new data — counters unchanged
            self.assertEqual(state2.turn_count, state1.turn_count)
            self.assertEqual(state2.byte_offset, state1.byte_offset)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 5. Truncated final line: skip partial, process complete lines
# ---------------------------------------------------------------------------


class TestTruncatedLine(unittest.TestCase):
    def test_truncated_final_line_no_exception(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as f:
            path = f.name
            complete_line = json.dumps(_make_user_entry()) + "\n"
            partial_line = '{"type": "user", "timestamp": "2024'  # no closing brace or newline
            f.write(complete_line.encode())
            f.write(partial_line.encode())

        try:
            result = read_transcript_telemetry(path)
            # Complete line should be counted
            self.assertEqual(result.turn_count, 1)
        finally:
            os.unlink(path)

    def test_truncated_line_is_skipped_not_raised(self) -> None:
        """No exception — the partial line is silently skipped."""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as f:
            path = f.name
            complete_line = json.dumps(_make_assistant_entry(tool_names=["Bash"])) + "\n"
            partial_line = b'{"type": "assistant", "message": {"content": [{"type"'
            f.write(complete_line.encode())
            f.write(partial_line)
        try:
            # Must not raise
            result = read_transcript_telemetry(path)
            self.assertEqual(result.tool_call_count, 1)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 6. fold_events with tool_use entries
# ---------------------------------------------------------------------------


class TestFoldEventsToolUse(unittest.TestCase):
    def test_tool_use_increments_tool_call_count(self) -> None:
        events = [_make_assistant_entry(tool_names=["Bash"])]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.tool_call_count, 1)

    def test_tool_use_sets_current_tool_name(self) -> None:
        events = [_make_assistant_entry(tool_names=["Edit"])]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.current_tool_name, "Edit")

    def test_multiple_tool_uses_picks_last(self) -> None:
        """The most recent tool name wins."""
        events = [_make_assistant_entry(tool_names=["Read", "Edit"])]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.current_tool_name, "Edit")
        self.assertEqual(result.tool_call_count, 2)

    def test_tool_use_sets_last_tool_use_at(self) -> None:
        events = [_make_assistant_entry(tool_names=["Bash"])]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertIsNotNone(result.last_tool_use_at)

    def test_tool_use_accumulates_across_calls(self) -> None:
        events1 = [_make_assistant_entry(tool_names=["Bash"])]
        totals = TranscriptTelemetry()
        state1 = fold_events(events1, totals)
        events2 = [_make_assistant_entry(tool_names=["Read"])]
        state2 = fold_events(events2, state1)
        self.assertEqual(state2.tool_call_count, 2)
        self.assertEqual(state2.current_tool_name, "Read")

    def test_fold_does_not_mutate_input(self) -> None:
        """fold_events is pure — it returns a new dataclass, not mutated in-place."""
        events = [_make_assistant_entry(tool_names=["Bash"])]
        original = TranscriptTelemetry()
        result = fold_events(events, original)
        # Original should be unchanged
        self.assertEqual(original.tool_call_count, 0)
        # Result should be changed
        self.assertEqual(result.tool_call_count, 1)


# ---------------------------------------------------------------------------
# 7. fold_events with user entries → turn_count incremented
# ---------------------------------------------------------------------------


class TestFoldEventsUserEntries(unittest.TestCase):
    def test_user_entry_increments_turn_count(self) -> None:
        events = [_make_user_entry()]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.turn_count, 1)

    def test_multiple_user_entries_accumulate(self) -> None:
        events = [_make_user_entry(), _make_user_entry(), _make_user_entry()]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.turn_count, 3)

    def test_non_user_entries_do_not_increment_turn_count(self) -> None:
        events = [
            {"type": "ai-title"},
            {"type": "assistant", "timestamp": "2024-01-01T00:00:00Z", "message": {"content": []}},
        ]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.turn_count, 0)

    def test_mixed_user_and_tool_both_accumulate(self) -> None:
        events = [
            _make_user_entry(),
            _make_assistant_entry(tool_names=["Bash"]),
            _make_user_entry(),
        ]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.turn_count, 2)
        self.assertEqual(result.tool_call_count, 1)


# ---------------------------------------------------------------------------
# 8. tailer_last_read_at is set when ≥1 event parsed, not on empty ticks
# ---------------------------------------------------------------------------


class TestTailerLastReadAt(unittest.TestCase):
    def test_tailer_last_read_at_set_after_parsing_events(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(f, [_make_user_entry()])
            path = f.name
        try:
            result = read_transcript_telemetry(path)
            self.assertIsNotNone(result.tailer_last_read_at)
        finally:
            os.unlink(path)

    def test_tailer_last_read_at_not_set_on_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            result = read_transcript_telemetry(path)
            self.assertIsNone(result.tailer_last_read_at)
        finally:
            os.unlink(path)

    def test_tailer_last_read_at_not_set_on_non_telemetry_lines(self) -> None:
        """Lines that parse as JSON but aren't user/assistant don't count as 'events'."""
        entries = [{"type": "ai-title"}, {"type": "queue-operation"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(f, entries)
            path = f.name
        try:
            result = read_transcript_telemetry(path)
            # Non-telemetry types don't trigger tailer_last_read_at
            self.assertIsNone(result.tailer_last_read_at)
        finally:
            os.unlink(path)

    def test_tailer_last_read_at_set_for_tool_use_events(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(f, [_make_assistant_entry(tool_names=["Bash"])])
            path = f.name
        try:
            result = read_transcript_telemetry(path)
            self.assertIsNotNone(result.tailer_last_read_at)
        finally:
            os.unlink(path)

    def test_tailer_last_read_at_not_set_on_no_op_tick(self) -> None:
        """Calling again with no new data: last_read_at stays as it was."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(f, [_make_user_entry()])
            path = f.name
        try:
            state1 = read_transcript_telemetry(path)
            first_read = state1.tailer_last_read_at
            self.assertIsNotNone(first_read)
            # Second tick, no new data
            state2 = read_transcript_telemetry(path, prev_state=state1)
            # last_read_at should not be updated on a no-op tick
            self.assertEqual(state2.tailer_last_read_at, first_read)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 9. Token accumulation (stretch goal)
# ---------------------------------------------------------------------------


class TestTokenAccumulation(unittest.TestCase):
    def test_tokens_accumulated_from_usage(self) -> None:
        usage = {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 500,
        }
        events = [_make_assistant_entry(usage=usage)]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.total_input_tokens, 1000)
        self.assertEqual(result.total_output_tokens, 200)
        self.assertEqual(result.total_cache_read_tokens, 500)

    def test_tokens_accumulate_across_multiple_entries(self) -> None:
        usage1 = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 0}
        usage2 = {"input_tokens": 200, "output_tokens": 80, "cache_read_input_tokens": 300}
        events = [
            _make_assistant_entry(usage=usage1),
            _make_assistant_entry(usage=usage2),
        ]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertEqual(result.total_input_tokens, 300)
        self.assertEqual(result.total_output_tokens, 130)
        self.assertEqual(result.total_cache_read_tokens, 300)


# ---------------------------------------------------------------------------
# 10. Thinking excerpt (stretch goal)
# ---------------------------------------------------------------------------


class TestThinkingExcerpt(unittest.TestCase):
    def test_thinking_excerpt_captured(self) -> None:
        long_thinking = "x" * 300
        events = [_make_assistant_entry(thinking=long_thinking)]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertIsNotNone(result.recent_thinking_excerpt)
        # Capped at 200 chars
        self.assertLessEqual(len(result.recent_thinking_excerpt), 200)

    def test_empty_thinking_block_ignored(self) -> None:
        events = [_make_assistant_entry(thinking="")]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertIsNone(result.recent_thinking_excerpt)


# ---------------------------------------------------------------------------
# 11. Malformed / invalid JSON lines are silently skipped
# ---------------------------------------------------------------------------


class TestMalformedLines(unittest.TestCase):
    def test_malformed_json_skipped(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as f:
            path = f.name
            f.write(b'{"type": "user", "timestamp": "2024"}\n')  # valid
            f.write(b"NOT JSON AT ALL\n")  # invalid
            f.write(b'{"type": "user", "timestamp": "2024"}\n')  # valid

        try:
            result = read_transcript_telemetry(path)
            self.assertEqual(result.turn_count, 2)  # Only the 2 valid entries
        finally:
            os.unlink(path)

    def test_completely_malformed_file_no_exception(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as f:
            path = f.name
            f.write(b"NOT JSON\n" * 10)
        try:
            result = read_transcript_telemetry(path)
            self.assertEqual(result.turn_count, 0)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 12. Path arg accepts both str and Path objects
# ---------------------------------------------------------------------------


class TestPathTypes(unittest.TestCase):
    def test_accepts_string_path(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(f, [_make_user_entry()])
            path = f.name
        try:
            result = read_transcript_telemetry(path)  # str
            self.assertEqual(result.turn_count, 1)
        finally:
            os.unlink(path)

    def test_accepts_path_object(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(f, [_make_user_entry()])
            path = Path(f.name)
        try:
            result = read_transcript_telemetry(path)  # pathlib.Path
            self.assertEqual(result.turn_count, 1)
        finally:
            os.unlink(str(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
