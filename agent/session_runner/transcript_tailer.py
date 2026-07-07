"""Last-assistant-text extraction from Claude Code JSONL transcripts.

The session runner mirrors the dev subagent's latest user-visible text into
the turn history (:meth:`SessionRunner._capture_dev_state`) by reading the
subagent's sidechain transcript and taking the most recent text-bearing
assistant entry. :func:`last_assistant_text` is that read; everything here is
fail-silent — a transcript problem must never crash a turn.

Timing guard: the runner only reads a transcript AFTER the ``Stop`` hook edge
fires (``hook_edge.HookEdgeConsumer``) — the Stop payload names the exact
flush-safe ``transcript_path``, so the turn's closing text is guaranteed
present. There is no idle-scrape path.
"""

from __future__ import annotations

import json


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


def last_assistant_text(transcript_path: str) -> str:
    """Return the concatenated text blocks from the most recent text-bearing
    assistant entry in the Claude Code JSONL transcript.

    Takes the last text-bearing entry, skipping entries that are pure
    tool_use/tool_result/thinking with no text blocks, so a tool-only final
    entry does not collapse to empty when an earlier textual turn exists.

    Returns "" when:
    - file is missing / unreadable
    - no assistant entry with text blocks exists
    - all JSONL lines fail to parse

    Fail-silent: never raises. Tolerates a partial (non-newline-terminated)
    trailing line by reading only complete lines.
    """
    texts = _text_bearing_assistant_texts(transcript_path)
    return texts[-1] if texts else ""
