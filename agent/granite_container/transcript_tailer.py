"""Incremental JSONL transcript tailer for granite PTY session telemetry.

This module provides byte-offset-stateful reading of Claude Code JSONL transcripts
so that the dashboard can populate telemetry fields for running granite PTY sessions
without re-reading the entire file on every poll.

JSONL format consumed
---------------------
Each line is a JSON object with a ``"type"`` field.  Only ``"type": "assistant"``
entries are processed for telemetry:

* ``message.usage.input_tokens``          → ``total_input_tokens``
* ``message.usage.output_tokens``         → ``total_output_tokens``
* ``message.usage.cache_read_input_tokens`` → ``total_cache_read_tokens``
* ``message.content[].type == "tool_use"``  → ``tool_call_count`` / ``current_tool_name``
* ``message.content[].type == "thinking"``  → ``recent_thinking_excerpt``
* ``timestamp`` (ISO-8601 on the outer event)  → ``last_tool_use_at``

Fail-silent contract
--------------------
All JSON parse errors, missing keys, and type mismatches are silently swallowed.
A partial or truncated final line (common because the file is appended live) is
treated as a no-op for that line.

Byte-offset semantics
---------------------
``TranscriptTelemetry.byte_offset`` stores the file position of the byte
**immediately after** the last successfully processed chunk.  Callers must
thread the returned object back into ``read_transcript_telemetry`` on the next
poll so the function can ``seek()`` past already-processed bytes.  Passing
``None`` for ``prev_state`` starts fresh from byte 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class TranscriptTelemetry:
    """Accumulated telemetry state from a Claude Code JSONL transcript.

    This object is meant to be threaded through successive ``read_transcript_telemetry``
    calls.  ``byte_offset`` is the critical stateful field that enables incremental reads.
    """

    turn_count: int = 0
    tool_call_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    current_tool_name: str | None = None
    last_tool_use_at: str | None = None  # ISO timestamp string from the JSONL entry
    recent_thinking_excerpt: str | None = None
    tailer_last_read_at: str | None = None  # ISO timestamp string, set on ≥1 parsed event
    byte_offset: int = 0


_TELEMETRY_TYPES = frozenset({"user", "assistant"})


def fold_events(events: list[dict], totals: TranscriptTelemetry) -> TranscriptTelemetry:
    """Fold a list of JSONL events into a new ``TranscriptTelemetry`` and return it.

    This is the #1536 seam: the pure accumulation logic is isolated here so it
    can be unit-tested independently of file I/O.

    Semantics by event type:
    - ``"type": "user"``      → increments ``turn_count``
    - ``"type": "assistant"`` → accumulates token usage, tool calls, and thinking

    All other event types are ignored for telemetry purposes.  Any missing key
    or type mismatch inside an event is silently skipped for that field.

    Args:
        events:  List of already-parsed JSON dicts.
        totals:  The ``TranscriptTelemetry`` to use as the starting values.
                 This object is **not mutated**; a copy is made internally.

    Returns:
        A new ``TranscriptTelemetry`` object with the folded totals.
    """
    # Work on a copy so callers observe no mutation of the input object.
    result = TranscriptTelemetry(
        turn_count=totals.turn_count,
        tool_call_count=totals.tool_call_count,
        total_input_tokens=totals.total_input_tokens,
        total_output_tokens=totals.total_output_tokens,
        total_cache_read_tokens=totals.total_cache_read_tokens,
        current_tool_name=totals.current_tool_name,
        last_tool_use_at=totals.last_tool_use_at,
        recent_thinking_excerpt=totals.recent_thinking_excerpt,
        tailer_last_read_at=totals.tailer_last_read_at,
        byte_offset=totals.byte_offset,
    )

    for event in events:
        try:
            event_type = event.get("type")

            if event_type == "user":
                result.turn_count += 1
                continue

            if event_type != "assistant":
                continue

            message = event.get("message", {})
            if not isinstance(message, dict):
                continue

            # Token accounting
            usage = message.get("usage", {})
            if isinstance(usage, dict):
                result.total_input_tokens += int(usage.get("input_tokens", 0))
                result.total_output_tokens += int(usage.get("output_tokens", 0))
                result.total_cache_read_tokens += int(usage.get("cache_read_input_tokens", 0))

            # Content block scanning
            content = message.get("content", [])
            if not isinstance(content, list):
                continue

            event_had_tool_use = False
            for block in content:
                try:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "tool_use":
                        result.tool_call_count += 1
                        result.current_tool_name = block.get("name")
                        event_had_tool_use = True
                    elif block_type == "thinking":
                        thinking_text = block.get("thinking", "")
                        if thinking_text:
                            result.recent_thinking_excerpt = thinking_text[:200]
                except Exception:
                    continue

            # Record timestamp of the assistant message if it contained tool use.
            # Store as raw ISO string from the JSONL (not parsed to datetime).
            if event_had_tool_use:
                ts_raw = event.get("timestamp")
                if ts_raw:
                    result.last_tool_use_at = str(ts_raw)

        except Exception:
            continue

    return result


def read_transcript_telemetry(
    path: str | None,
    prev_state: TranscriptTelemetry | None = None,
) -> TranscriptTelemetry:
    """Incrementally read a Claude Code JSONL transcript file.

    Consumes only the bytes after ``prev_state.byte_offset`` (i.e. what has been
    appended since the last poll), updates the running totals, advances the offset,
    and returns the new state.

    JSONL fields consumed
    ---------------------
    See module docstring for the full field mapping.

    Fail-silent contract
    --------------------
    Any I/O error, JSON parse error, or missing key is silently ignored.
    The function never raises.

    Byte-offset semantics
    ---------------------
    ``totals.byte_offset`` is set to ``file.tell()`` after reading, so the
    next call can seek past already-processed content.  The offset is only
    advanced when there is something to read; an empty file or a file that has
    not grown returns the unchanged previous offset.

    Args:
        path:        Absolute or relative path to the ``.jsonl`` transcript file.
                     ``None`` is a no-op.
        prev_state:  Telemetry state from the previous poll.  Pass ``None`` to
                     start a fresh accumulation from byte 0.

    Returns:
        Updated ``TranscriptTelemetry`` with the new running totals and byte
        offset.  If the file does not exist or ``path`` is ``None``, returns
        *prev_state* unchanged (or a fresh default instance if *prev_state* is
        also ``None``).
    """
    # Start from a copy of prev_state (or a fresh instance).
    base = prev_state if prev_state is not None else TranscriptTelemetry()

    if path is None:
        # Return a shallow copy so callers cannot mutate the returned object.
        return TranscriptTelemetry(
            turn_count=base.turn_count,
            tool_call_count=base.tool_call_count,
            total_input_tokens=base.total_input_tokens,
            total_output_tokens=base.total_output_tokens,
            total_cache_read_tokens=base.total_cache_read_tokens,
            current_tool_name=base.current_tool_name,
            last_tool_use_at=base.last_tool_use_at,
            recent_thinking_excerpt=base.recent_thinking_excerpt,
            tailer_last_read_at=base.tailer_last_read_at,
            byte_offset=base.byte_offset,
        )

    try:
        import os

        path_str = str(path)
        if not os.path.exists(path_str):
            return TranscriptTelemetry(
                turn_count=base.turn_count,
                tool_call_count=base.tool_call_count,
                total_input_tokens=base.total_input_tokens,
                total_output_tokens=base.total_output_tokens,
                total_cache_read_tokens=base.total_cache_read_tokens,
                current_tool_name=base.current_tool_name,
                last_tool_use_at=base.last_tool_use_at,
                recent_thinking_excerpt=base.recent_thinking_excerpt,
                tailer_last_read_at=base.tailer_last_read_at,
                byte_offset=base.byte_offset,
            )

        with open(path_str, "rb") as fh:
            fh.seek(base.byte_offset)
            raw = fh.read()
            end_offset = fh.tell()

        if not raw:
            return TranscriptTelemetry(
                turn_count=base.turn_count,
                tool_call_count=base.tool_call_count,
                total_input_tokens=base.total_input_tokens,
                total_output_tokens=base.total_output_tokens,
                total_cache_read_tokens=base.total_cache_read_tokens,
                current_tool_name=base.current_tool_name,
                last_tool_use_at=base.last_tool_use_at,
                recent_thinking_excerpt=base.recent_thinking_excerpt,
                tailer_last_read_at=base.tailer_last_read_at,
                byte_offset=base.byte_offset,
            )

        # Split on newlines; the last element may be a partial line — drop it
        # only if it does NOT end with a newline (i.e., file is mid-write).
        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\n")
        if not text.endswith("\n"):
            # Drop incomplete trailing line
            lines = lines[:-1]

        events: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    events.append(obj)
            except json.JSONDecodeError:
                continue

        # fold_events returns a new TranscriptTelemetry (copy semantics)
        result = fold_events(events, base)

        # Always advance the byte offset past what we read
        result.byte_offset = end_offset

        # Only stamp tailer_last_read_at when at least one telemetry event
        # (user or assistant type) was successfully parsed.  Store as ISO string.
        has_telemetry = any(e.get("type") in _TELEMETRY_TYPES for e in events)
        if has_telemetry:
            result.tailer_last_read_at = datetime.now(UTC).isoformat()

        return result

    except Exception:
        # Fail-silent: return a copy of base on any unexpected error
        return TranscriptTelemetry(
            turn_count=base.turn_count,
            tool_call_count=base.tool_call_count,
            total_input_tokens=base.total_input_tokens,
            total_output_tokens=base.total_output_tokens,
            total_cache_read_tokens=base.total_cache_read_tokens,
            current_tool_name=base.current_tool_name,
            last_tool_use_at=base.last_tool_use_at,
            recent_thinking_excerpt=base.recent_thinking_excerpt,
            tailer_last_read_at=base.tailer_last_read_at,
            byte_offset=base.byte_offset,
        )
