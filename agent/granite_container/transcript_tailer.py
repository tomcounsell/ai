"""Incremental JSONL transcript tailer for granite PTY session telemetry.

This module provides byte-offset-stateful reading of Claude Code JSONL transcripts
so that the dashboard can populate telemetry fields for running granite PTY sessions
without re-reading the entire file on every poll.

JSONL format consumed
---------------------
Each line is a JSON object with a ``"type"`` field.  Both ``"type": "assistant"``
and ``"type": "user"`` entries are folded for telemetry:

``"assistant"`` entries contribute:

* ``message.usage.input_tokens``          → ``total_input_tokens``
* ``message.usage.output_tokens``         → ``total_output_tokens``
* ``message.usage.cache_read_input_tokens`` → ``total_cache_read_tokens``
* ``message.content[].type == "tool_use"``  → ``tool_call_count`` / ``current_tool_name``
* ``message.content[].type == "thinking"``  → ``recent_thinking_excerpt``
* ``timestamp`` (ISO-8601 on the outer event)  → ``last_tool_use_at``

``"user"`` entries contribute:

* (presence of entry)  → increments ``turn_count``

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
                # A user event carries the tool_result for the prior tool_use,
                # so any tool the assistant launched has now completed and
                # nothing is in flight. Clear current_tool_name here.
                #
                # Without this, current_tool_name stayed pinned to the last
                # tool the PM ran (e.g. a fast Read during priming) while
                # last_tool_use_at froze at that moment. The PM would then
                # think/respond for >30s — normal opus cadence — and
                # session_health._check_tool_timeout would false-flag it as
                # `tool-wedge: Read (internal tier) older than 30s` and kill
                # the session. A genuinely hung tool produces a tool_use with
                # NO following user/result event, so current_tool_name stays
                # set and the timeout still fires correctly — only the
                # completed-tool false positive is removed.
                result.current_tool_name = None
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

        # Determine the safe commit offset.
        # If the buffer ends with a newline, all bytes are complete — advance to EOF.
        # If not, there is a partial trailing line — only advance to the last \n so
        # the next tick re-reads those bytes once the write is complete.
        if raw.endswith(b"\n"):
            safe_offset = end_offset
        else:
            last_nl = raw.rfind(b"\n")
            if last_nl == -1:
                # Entire buffer is one partial line with no newline at all — nothing to process.
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
            # Advance only to the end of the last complete line (inclusive of its \n).
            safe_offset = base.byte_offset + last_nl + 1

        # Parse only the bytes up to safe_offset (all complete lines).
        complete_bytes = raw[: safe_offset - base.byte_offset]
        text = complete_bytes.decode("utf-8", errors="replace")
        lines = [line for line in text.split("\n") if line.strip()]

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

        # Advance the byte offset to the end of the last complete line only.
        result.byte_offset = safe_offset

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


def _text_bearing_assistant_texts(transcript_path: str) -> list[str]:
    """Return the concatenated text of every text-bearing assistant entry, in order.

    A "text-bearing" assistant entry is one with at least one ``text`` content
    block. The text blocks within a single entry are joined with ``"\\n"`` (not
    ``""``) so that adjacent blocks are not glued into runwords.

    Reads only COMPLETE lines: a partial (non-newline-terminated) trailing line
    is excluded, because the file is appended live and the last line may be
    mid-write.

    Opens with ``encoding="utf-8-sig"`` so a leading UTF-8 BOM is stripped
    rather than silently corrupting (and dropping) the first line.

    Fail-silent: returns ``[]`` on a missing/unreadable file or when every line
    fails to parse. Never raises.
    """
    if not transcript_path:
        return []
    try:
        with open(transcript_path, encoding="utf-8-sig") as f:
            content = f.read()
    except OSError:
        return []
    # Read only complete lines (partial trailing line excluded).
    if content and not content.endswith("\n"):
        content = content[: content.rfind("\n") + 1]
    if not content:
        return []
    texts: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message", {})
        if not isinstance(message, dict):
            continue
        content_blocks = message.get("content", [])
        if not isinstance(content_blocks, list):
            continue
        text_parts = [
            block.get("text", "")
            for block in content_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in text_parts if part)
        if text:
            texts.append(text)
    return texts


def text_bearing_count(transcript_path: str) -> int:
    """Return the number of text-bearing assistant entries in the transcript.

    This is the cheap baseline snapshot a caller captures BEFORE an idle read,
    so that ``last_assistant_text`` can tell whether a genuinely new
    text-bearing assistant entry flushed this cycle. Returns 0 for a
    missing/unreadable/empty transcript. Fail-silent.
    """
    return len(_text_bearing_assistant_texts(transcript_path))


def last_assistant_text(transcript_path: str, *, baseline_text_count: int | None = None) -> str:
    """Return the concatenated text blocks from the most recent text-bearing
    assistant entry in the Claude Code JSONL transcript.

    Walks assistant entries newest-first (implicitly, by taking the last
    text-bearing entry), skipping entries that are pure
    tool_use/tool_result/thinking with no text blocks, so a tool-only final
    entry does not collapse to empty when an earlier textual turn exists.

    Freshness guard (content-identity, not mtime)
    ---------------------------------------------
    When ``baseline_text_count`` is given, this returns "" unless a NEW
    text-bearing assistant entry has appeared since the baseline — i.e. the
    count of text-bearing entries must have grown past the baseline. This
    replaces the old mtime-advancement guard, which was defeated by intra-turn
    writes: a tool round-trip (assistant[tool_use] → user[tool_result] →
    assistant[text]) advances mtime on every line, so an idle read landing
    between the tool lines and the final text line saw an advanced mtime and
    wrongly forwarded the PRIOR turn's text. Counting text-bearing entries is
    immune to that: tool_use / tool_result lines do not increment the count.

    Returns "" when:
    - file is missing / unreadable
    - no assistant entry with text blocks exists
    - all JSONL lines fail to parse
    - ``baseline_text_count`` is given and no new text-bearing entry appeared
      this cycle (``len(texts) <= baseline_text_count``)

    Fail-silent: never raises. Tolerates a partial (non-newline-terminated)
    trailing line by reading only complete lines.

    Residual: an intermediate mid-turn aside (a text-bearing assistant entry
    flushed AFTER the baseline but BEFORE the turn's closing text) can still be
    returned, because it does increment the count. The deterministic fix is the
    hook-driven Stop signal in followup #1688.
    """
    texts = _text_bearing_assistant_texts(transcript_path)
    if baseline_text_count is not None and len(texts) <= baseline_text_count:
        return ""
    return texts[-1] if texts else ""
