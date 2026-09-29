"""Reply extraction and parsing for direct Anthropic / OpenRouter API calls.

Tools that call the Messages API (or OpenRouter chat completions) directly go
through this module instead of reading ``content[0]``. The rules, driven by
Claude Sonnet 5.5 reply shapes:

- Only ``type == "text"`` blocks carry the answer. A reply can open with a
  thinking block, so ``content[0]`` is not the answer.
- A reply cut off at ``max_tokens`` is a failure, never a partial answer:
  Anthropic ``stop_reason == "max_tokens"`` and OpenRouter
  ``finish_reason == "length"`` raise :class:`ReplyTruncated`.
- A JSON answer is the last complete JSON value in the text, so the model may
  reason in prose first and end with the JSON (:func:`parse_last_json`).
"""

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```[\w-]*")
_DECODER = json.JSONDecoder()


class ReplyTruncated(Exception):  # noqa: N818 - name fixed by plan #3570
    """The model stopped at the output-token limit before finishing its reply."""


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from an SDK object (attribute) or a raw dict (key)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _truncated_message(max_tokens: int | None) -> str:
    limit = f"max_tokens={max_tokens}" if max_tokens is not None else "max_tokens"
    return f"Reply truncated at {limit} before the model finished"


def anthropic_text(response: Any, max_tokens: int | None = None) -> str:
    """Return the joined text blocks of an Anthropic Messages response.

    Accepts an ``anthropic`` SDK ``Message`` or the raw JSON dict. Thinking and
    other non-text blocks are skipped. Returns ``""`` when there are no text
    blocks (e.g. a thinking-only ``end_turn`` reply).

    Raises:
        ReplyTruncated: ``stop_reason == "max_tokens"``. ``max_tokens`` is the
            request's limit, used only to make the message diagnosable.
    """
    if _field(response, "stop_reason") == "max_tokens":
        raise ReplyTruncated(_truncated_message(max_tokens))
    blocks = _field(response, "content") or []
    return "".join(
        _field(block, "text") or "" for block in blocks if _field(block, "type") == "text"
    )


def openrouter_text(result: dict, max_tokens: int | None = None) -> str:
    """Return ``choices[0].message.content`` of an OpenRouter chat completion.

    Reasoning (``message.reasoning``) is returned separately by OpenRouter and
    ignored here. Returns ``""`` on empty ``choices`` or ``None`` content.

    Raises:
        ReplyTruncated: ``finish_reason == "length"``.
    """
    choices = result.get("choices") or []
    if not choices:
        return ""
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise ReplyTruncated(_truncated_message(max_tokens))
    return (choice.get("message") or {}).get("content") or ""


def parse_last_json(text: str) -> Any:
    """Return the last complete JSON object or array in ``text``.

    Code fences are stripped first. Every ``{`` / ``[`` position is tried with
    ``raw_decode``; the value ending at the end of the text wins, otherwise
    the value that ends last (outermost on ties), so nested values and
    trailing prose are both handled.

    Raises:
        ValueError: no complete JSON value is present.
    """
    stripped = _FENCE_RE.sub("", text or "").strip()
    best: tuple[int, int, Any] | None = None  # (end, -start, value)
    for start in range(len(stripped) - 1, -1, -1):
        if stripped[start] not in "{[":
            continue
        try:
            value, end = _DECODER.raw_decode(stripped, start)
        except json.JSONDecodeError:
            continue
        if end == len(stripped):
            return value
        if best is None or (end, -start) > best[:2]:
            best = (end, -start, value)
    if best is None:
        raise ValueError("No complete JSON value found in reply")
    return best[2]
