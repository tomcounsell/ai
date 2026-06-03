"""Granite classifier for the granite operator PoC (issue #1546).

The new `granite_classifier` is the PoC's reduced-3-tool
classifier. It replaces the prior PoC's 5-tool `agent.granite_router`
with a narrower surface that separates *classification* (a
deterministic regex parse on PM's prefix token) from *translation*
(two ollama calls: extract a Dev prompt, summarize Dev output for
PM).

Why split classification from translation:
  - The classification decision is a regex parse of the first line
    of PM's tail — the `[/dev]/[/user|/complete]` convention PM was
    primed to follow. It is not an LLM call.
  - The two translation tasks remain LLM calls (granite reads the
    full PM tail and produces either a developer instruction or a
    user-facing reply). The translation quality is what granite
    adds; the classification decision is bookkeeping the operator
    can do deterministically.

This is the Q4/Q6 resolution from the plan's *Open Questions*:
  - Q4 (event-bridge shape): the container maps PTY output to
    `list[dict]` events at the boundary (`[{"type": "pm_output",
    "text": <tail>}]`). Granite consumes this list, same shape
    `agent/granite_router.py:276` consumes today.
  - Q6 (PM prefix-token compliance): the classifier is a
    deterministic regex parse (`classify_pm_prefix`), not an LLM
    call. The results doc reports compliance rate (a parse metric)
    on a synthetic distribution plus live measurements.

The classifier is stateless: each ollama.chat() call sees only the
system prompt + the current turn's content. There is no cross-turn
history (invariant #5). The 2-line SYSTEM_PROMPT is the same
shape as the prior PoC's; the production cutover can adopt the
new substrate without rewriting granite's prompt.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

try:
    from ollama import chat as ollama_chat
except ImportError:  # pragma: no cover -- ollama is a hard runtime dep
    ollama_chat = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "granite4.1:3b"

# The prefix-token convention. The PM persona body (in
# .claude/commands/granite-poc/prime-pm-role.md) primes PM to begin
# every output with one of these three literal tokens on a line of
# its own. The classifier's `classify_pm_prefix` parses the first
# line; if no token is present, the result is `unknown` and the
# container logs a compliance miss.
#
# The strict regex requires the token to be the entire content of
# its line (no trailing text, allowed trailing whitespace). It is
# matched against the **first non-empty line** of PM's tail using
# re.match (which anchors at the start of the line).
PREFIX_TOKEN_RE = re.compile(r"^\[/(dev|user|complete)\]\s*$")
PREFIX_TOKEN_FALLBACK_RE = re.compile(r"\[/(dev|user|complete)\]")

# Destination: which PTY the routed output goes to.
Destination = Literal["dev", "user", "complete", "unknown"]


@dataclass
class ClassificationResult:
    """The classifier's routing decision for a PM turn.

    `destination` is the routing target. `payload` is the routed
    text — for `dev` and `user`, the translation call's output; for
    `complete`, the trailing one-sentence summary from PM; for
    `unknown`, an empty string (the container surfaces a compliance
    miss to the results JSON).

    `compliance_miss` is True iff the PM tail had no prefix token
    on its first line. The container uses this to compute the
    compliance rate; the results doc reports it.
    """

    destination: Destination
    payload: str
    compliance_miss: bool
    raw_first_line: str


# ---------------------------------------------------------------------------
# Tool schema (the 2 translation calls)
# ---------------------------------------------------------------------------

TRANSLATION_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "extract_dev_prompt",
            "description": (
                "Extract the next instruction the Dev session should receive, "
                "based on what the PM session just produced."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dev_prompt": {
                        "type": "string",
                        "description": "The full instruction text to send to Dev.",
                    }
                },
                "required": ["dev_prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_for_pm",
            "description": (
                "Summarize the Dev session output so the PM can evaluate "
                "progress without seeing every raw tool call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "A short summary of what Dev did and produced.",
                    }
                },
                "required": ["summary"],
            },
        },
    },
]


SYSTEM_PROMPT = """\
You are the granite4.1:3b operator. You sit between two Claude Code \
sessions (PM and Dev) and translate between their natural-language \
outputs. You do NOT judge code quality or perform user-visible \
reasoning; the PM session owns that.

You have two tools:
  - extract_dev_prompt: PM just produced output addressed to Dev. \
    Translate the next instruction Dev should receive.
  - summarize_for_pm: Dev just produced output. Summarize what Dev \
    did so PM can evaluate without seeing every raw tool call.

Pick the right tool based on which session just spoke. Your response \
is the tool call; do not add commentary. \
"""


# ---------------------------------------------------------------------------
# Classification (deterministic regex parse)
# ---------------------------------------------------------------------------


def classify_pm_prefix(pm_tail: str) -> ClassificationResult:
    """Classify PM's tail by parsing the first line for a prefix token.

    The PM persona body primes PM to begin every output with one of:
      `[/dev]` — followed by the developer instruction
      `[/user]` — followed by the user-facing message
      `[/complete]` — followed by a one-sentence completion summary

    The first line is parsed with `PREFIX_TOKEN_RE` (strict — token
    must be the only content on the line). If the strict regex
    doesn't match, a fallback regex (PREFIX_TOKEN_FALLBACK_RE) is
    tried on the first 200 chars. If neither matches, the result
    is `unknown` and `compliance_miss=True`.

    The classification is **stateless** — no call history, no PM
    persona context, no ollama call. It is a regex parse.
    """
    # Find the first non-empty line.
    first_line = ""
    for line in pm_tail.splitlines():
        if line.strip():
            first_line = line
            break

    if not first_line:
        return ClassificationResult(
            destination="unknown",
            payload="",
            compliance_miss=True,
            raw_first_line="",
        )

    m = PREFIX_TOKEN_RE.match(first_line)
    if m:
        token = m.group(1)
        # The payload is the rest of the tail (the lines after the
        # prefix token), stripped of leading/trailing whitespace.
        # For complete, the trailing one-sentence summary is the
        # payload; for dev/user, the developer instruction / user
        # message is the payload.
        rest = pm_tail[pm_tail.index(first_line) + len(first_line) :].strip()
        return ClassificationResult(
            destination=token,  # type: ignore[arg-type]
            payload=rest,
            compliance_miss=False,
            raw_first_line=first_line,
        )

    # Strict match failed; try a more permissive fallback. PM may
    # have included the token mid-line or with light surrounding
    # text (e.g., "output: [/dev] please ...") — that's a
    # compliance miss by the strict definition but a correct
    # classification. The fallback's `compliance_miss=True` is
    # the right signal: the persona is not strictly enforcing the
    # convention.
    fallback = PREFIX_TOKEN_FALLBACK_RE.search(pm_tail[:200])
    if fallback:
        return ClassificationResult(
            destination=fallback.group(1),  # type: ignore[arg-type]
            payload=pm_tail.strip(),
            compliance_miss=True,
            raw_first_line=first_line,
        )

    return ClassificationResult(
        destination="unknown",
        payload="",
        compliance_miss=True,
        raw_first_line=first_line,
    )


# ---------------------------------------------------------------------------
# Translation (the 2 ollama calls)
# ---------------------------------------------------------------------------


class GraniteTranslationError(RuntimeError):
    """Raised when granite's translation call fails or produces no tool call."""


def _events_from_text(text: str, label: str) -> list[dict[str, Any]]:
    """Wrap a text tail as the `list[dict]` event shape granite consumes.

    The Q4 resolution: the container maps PTY output to the existing
    stream-json-shaped event list at the boundary. Each text tail
    becomes a single event with `type` and `text` fields.
    """
    return [{"type": label, "text": text}]


def extract_dev_prompt(pm_tail: str, model: str = DEFAULT_MODEL) -> str:
    """Call granite to extract a developer instruction from PM's tail.

    The PM tail is wrapped in a single `pm_output` event and passed
    to granite with the `extract_dev_prompt` tool. Granite's tool
    call's `dev_prompt` argument is returned.

    Raises GraniteTranslationError on ollama failure or no-tool-call.
    """
    if ollama_chat is None:
        raise GraniteTranslationError("ollama is not importable")

    events = _events_from_text(pm_tail, "pm_output")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(events)},
    ]
    try:
        response = ollama_chat(
            model=model,
            messages=messages,
            tools=TRANSLATION_TOOLS,
        )
    except Exception as e:
        raise GraniteTranslationError(f"ollama.chat failed: {e}") from e

    tool_calls = _extract_tool_calls(response)
    extract_calls = [tc for tc in tool_calls if tc["name"] == "extract_dev_prompt"]
    if not extract_calls:
        raise GraniteTranslationError(
            f"granite did not call extract_dev_prompt; got {[tc['name'] for tc in tool_calls]}"
        )
    return str(extract_calls[0]["arguments"].get("dev_prompt", "")).strip()


def summarize_for_pm(dev_tail: str, model: str = DEFAULT_MODEL) -> str:
    """Call granite to summarize Dev's output for PM.

    The Dev tail is wrapped in a single `dev_output` event and
    passed to granite with the `summarize_for_pm` tool. Granite's
    `summary` argument is returned.

    Raises GraniteTranslationError on ollama failure or no-tool-call.
    """
    if ollama_chat is None:
        raise GraniteTranslationError("ollama is not importable")

    events = _events_from_text(dev_tail, "dev_output")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(events)},
    ]
    try:
        response = ollama_chat(
            model=model,
            messages=messages,
            tools=TRANSLATION_TOOLS,
        )
    except Exception as e:
        raise GraniteTranslationError(f"ollama.chat failed: {e}") from e

    tool_calls = _extract_tool_calls(response)
    summarize_calls = [tc for tc in tool_calls if tc["name"] == "summarize_for_pm"]
    if not summarize_calls:
        raise GraniteTranslationError(
            f"granite did not call summarize_for_pm; got {[tc['name'] for tc in tool_calls]}"
        )
    return str(summarize_calls[0]["arguments"].get("summary", "")).strip()


def _extract_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Normalize ollama's response into a list of `{name, arguments}` dicts.

    The ollama Python client's response shape varies across versions
    (the message may carry `tool_calls` directly, or the response may
    nest the calls under `message`). We defensively accept both
    shapes.
    """
    if response is None:
        return []
    message = getattr(response, "message", None) or (
        response.get("message") if isinstance(response, dict) else None
    )
    if message is None:
        return []
    tool_calls = getattr(message, "tool_calls", None) or (
        message.get("tool_calls") if isinstance(message, dict) else None
    )
    if not tool_calls:
        return []
    out: list[dict[str, Any]] = []
    for tc in tool_calls:
        # ollama >= 0.4 uses a `function` namespace.
        if hasattr(tc, "function"):
            fn = tc.function
            name = getattr(fn, "name", None) or (
                fn.get("name") if isinstance(fn, dict) else None
            )
            arguments = getattr(fn, "arguments", None) or (
                fn.get("arguments") if isinstance(fn, dict) else None
            )
            if name:
                out.append(
                    {
                        "name": name,
                        "arguments": _normalize_arguments(arguments),
                    }
                )
            continue
        # Dict shape.
        if isinstance(tc, dict):
            fn = tc.get("function", tc)
            name = fn.get("name")
            arguments = fn.get("arguments", {})
            if name:
                out.append(
                    {
                        "name": name,
                        "arguments": _normalize_arguments(arguments),
                    }
                )
    return out


def _normalize_arguments(arguments: Any) -> dict[str, Any]:
    """Coerce ollama's `function.arguments` to a dict.

    Some ollama versions return a JSON string; some return a dict
    directly. We accept both.
    """
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"_raw": str(arguments)}
