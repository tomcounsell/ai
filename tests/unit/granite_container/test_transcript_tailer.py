"""Unit tests for the transcript tailer module.

Tests the incremental JSONL-reading logic that populates dashboard
telemetry for granite PTY sessions (#1536 seam / sdlc-1648).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC
from pathlib import Path

from agent.granite_container.transcript_tailer import (
    TranscriptTelemetry,
    fold_events,
    last_assistant_text,
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


# ---------------------------------------------------------------------------
# 13. Split-tick offset: partial line written across two ticks is counted once
# ---------------------------------------------------------------------------


class TestSplitTickOffset(unittest.TestCase):
    def test_split_turn_across_two_ticks_is_counted(self) -> None:
        """A user entry written in two halves across two ticks is counted exactly once.

        Regression guard for the partial-line offset bug: previously, `byte_offset`
        was advanced to EOF even when the last line was incomplete, causing the
        now-complete line to be skipped on the next tick entirely.
        """
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as f:
            path = f.name
            # Tick 1: write one complete user entry.
            complete = (json.dumps(_make_user_entry()) + "\n").encode()
            f.write(complete)

        try:
            # First tick — read the complete line.
            state1 = read_transcript_telemetry(path)
            self.assertEqual(state1.turn_count, 1)
            self.assertEqual(state1.byte_offset, len(complete))

            # Simulate mid-write: append a partial (no trailing newline) user entry.
            partial_json = json.dumps(_make_user_entry())
            partial_bytes = partial_json.encode()  # no "\n"
            with open(path, "ab") as f:
                f.write(partial_bytes)

            # Second tick — partial line must NOT be counted, and byte_offset must NOT
            # advance past the partial bytes (so the third tick can re-read them).
            state2 = read_transcript_telemetry(path, prev_state=state1)
            self.assertEqual(state2.turn_count, 1, "partial line must not be counted yet")
            # byte_offset must stay at end of last complete line (i.e. state1.byte_offset)
            self.assertEqual(
                state2.byte_offset,
                state1.byte_offset,
                "byte_offset must not advance into partial line",
            )

            # Complete the partial line by appending the closing newline.
            with open(path, "ab") as f:
                f.write(b"\n")

            # Third tick — the previously-partial line is now complete and must be counted.
            state3 = read_transcript_telemetry(path, prev_state=state2)
            self.assertEqual(
                state3.turn_count,
                2,
                "turn written across two ticks must be counted on the completing tick",
            )
        finally:
            os.unlink(path)

    def test_all_partial_no_newline_returns_same_offset(self) -> None:
        """When the entire buffer has no newline, byte_offset must not advance."""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as f:
            path = f.name
            # Write bytes that have no newline at all.
            f.write(b'{"type": "user", "timestamp": "2024')

        try:
            result = read_transcript_telemetry(path)
            self.assertEqual(result.byte_offset, 0, "no complete line → offset stays at 0")
            self.assertEqual(result.turn_count, 0)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 14. last_tool_use_at contract: tailer returns str, adapter must parse to datetime
# ---------------------------------------------------------------------------


class TestLastToolUseAtIsDatetime(unittest.TestCase):
    def test_last_tool_use_at_iso_string_parses_to_datetime(self) -> None:
        """Verify that ISO strings from TranscriptTelemetry convert to tz-aware datetimes."""
        from datetime import datetime

        # Simulate what bridge_adapter must do before assigning to AgentSession.last_tool_use_at.
        iso_str = "2024-01-01T00:01:00.000Z"
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        self.assertIsInstance(dt, datetime)
        self.assertIsNotNone(dt.tzinfo)
        self.assertEqual(dt.tzinfo, UTC)

    def test_last_tool_use_at_none_handled_gracefully(self) -> None:
        """Verify None ISO string doesn't cause a crash in parse."""
        from datetime import datetime

        iso_str = None
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")) if iso_str else None
        self.assertIsNone(dt)

    def test_last_tool_use_at_is_str_from_tailer(self) -> None:
        """The tailer returns last_tool_use_at as a raw ISO string.

        Parsing to a datetime object is the bridge adapter's responsibility —
        the tailer must not perform that conversion so it stays fail-silent and
        agnostic to tz-handling policy.
        """
        events = [_make_assistant_entry(tool_names=["Bash"])]
        totals = TranscriptTelemetry()
        result = fold_events(events, totals)
        self.assertIsNotNone(result.last_tool_use_at)
        self.assertIsInstance(
            result.last_tool_use_at,
            str,
            "tailer must return last_tool_use_at as a raw ISO string, not a datetime",
        )


# ---------------------------------------------------------------------------
# 15. last_assistant_text: JSONL transcript reader
# ---------------------------------------------------------------------------


class TestLastAssistantText(unittest.TestCase):
    """Tests for last_assistant_text() JSONL transcript reader."""

    def _write_transcript(self, tmp_path: str, lines: list) -> str:
        """Write JSONL lines to a temp transcript file, return path as str."""
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".jsonl", dir=tmp_path)
        with os.fdopen(fd, "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_returns_last_text_bearing_assistant_entry(self) -> None:
        """Picks the last assistant entry that has text blocks."""
        path = self._write_transcript(
            self._tmp,
            [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "first"}]},
                },
                {"type": "user", "message": {"content": [{"type": "text", "text": "user msg"}]}},
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "second"}]},
                },
            ],
        )
        self.assertEqual(last_assistant_text(path), "second")

    def test_concatenates_text_blocks(self) -> None:
        """Concatenates multiple text blocks in a single entry."""
        path = self._write_transcript(
            self._tmp,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "hello "},
                            {"type": "text", "text": "world"},
                        ]
                    },
                },
            ],
        )
        self.assertEqual(last_assistant_text(path), "hello world")

    def test_excludes_tool_use_tool_result_thinking_blocks(self) -> None:
        """Does not include non-text blocks in the result."""
        path = self._write_transcript(
            self._tmp,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                            {"type": "text", "text": "done"},
                            {"type": "thinking", "thinking": "internal"},
                        ]
                    },
                },
            ],
        )
        self.assertEqual(last_assistant_text(path), "done")

    def test_returns_empty_on_missing_file(self) -> None:
        """Returns '' when file doesn't exist."""
        self.assertEqual(last_assistant_text("/nonexistent/path/transcript.jsonl"), "")

    def test_returns_empty_on_empty_file(self) -> None:
        """Returns '' on empty file."""
        import os

        path = os.path.join(self._tmp, "empty.jsonl")
        open(path, "w").close()
        self.assertEqual(last_assistant_text(path), "")

    def test_returns_empty_when_no_assistant_entries(self) -> None:
        """Returns '' when only user/tool_result entries exist."""
        path = self._write_transcript(
            self._tmp,
            [
                {"type": "user", "message": {"content": [{"type": "text", "text": "hello"}]}},
                {"type": "tool_result", "content": "result"},
            ],
        )
        self.assertEqual(last_assistant_text(path), "")

    def test_tolerates_corrupt_partial_jsonl(self) -> None:
        """Returns '' or partial result when JSONL is corrupt or partial."""
        import os

        path = os.path.join(self._tmp, "corrupt.jsonl")
        with open(path, "w") as f:
            f.write(
                '{"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}\n'
            )
            f.write("{corrupt json\n")
        result = last_assistant_text(path)
        # Should not raise; returns the valid entry's text
        self.assertEqual(result, "ok")

    def test_tolerates_partial_trailing_line(self) -> None:
        """Ignores a partial (non-newline-terminated) trailing line."""
        import os

        good_line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "complete"}]},
            }
        )
        partial = '{"type": "assistant", "message": {"content": [{"type": "text"'
        path = os.path.join(self._tmp, "partial.jsonl")
        with open(path, "w") as f:
            f.write(good_line + "\n" + partial)  # no trailing newline
        result = last_assistant_text(path)
        self.assertEqual(result, "complete")

    def test_stale_mtime_returns_empty(self) -> None:
        """Returns '' when file mtime has NOT advanced past mtime_before."""
        import os
        import time

        path = os.path.join(self._tmp, "stale.jsonl")
        with open(path, "w") as f:
            f.write(
                '{"type": "assistant", "message": '
                '{"content": [{"type": "text", "text": "stale"}]}}\n'
            )
        # Use mtime AFTER writing (same or later) as mtime_before
        mtime = os.path.getmtime(path)
        time.sleep(0.01)  # ensure we're past the write
        result = last_assistant_text(path, mtime_before=mtime)
        # mtime has NOT advanced since mtime_before — should return ""
        self.assertEqual(result, "")

    def test_fresh_mtime_returns_text(self) -> None:
        """Returns text when file mtime HAS advanced past mtime_before."""
        import os
        import time

        path = os.path.join(self._tmp, "fresh.jsonl")
        # Capture mtime_before BEFORE writing
        mtime_before = time.time() - 1  # 1 second before now
        with open(path, "w") as f:
            f.write(
                '{"type": "assistant", "message": '
                '{"content": [{"type": "text", "text": "fresh"}]}}\n'
            )
        result = last_assistant_text(path, mtime_before=mtime_before)
        self.assertEqual(result, "fresh")

    def test_skips_tool_only_final_entry_returns_earlier_text(self) -> None:
        """Skips a final entry that is pure tool_use; returns earlier text-bearing entry."""
        path = self._write_transcript(
            self._tmp,
            [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "earlier text"}]},
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]
                    },
                },
            ],
        )
        self.assertEqual(last_assistant_text(path), "earlier text")

    def test_no_text_anywhere_returns_empty(self) -> None:
        """Returns '' when NO assistant entry has any text blocks."""
        path = self._write_transcript(
            self._tmp,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]
                    },
                },
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "thinking", "thinking": "internal"}]},
                },
            ],
        )
        self.assertEqual(last_assistant_text(path), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
