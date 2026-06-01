"""Granite4.1:3b operator/router for the granite-agent-loop PoC.

`GraniteRouter` calls `ollama.chat('granite4.1:3b', messages, tools)` to make
operator decisions: extract a Dev prompt from PM output, summarize Dev output
for PM, handle multiple-choice prompts that surface during a session, probe a
session that has gone silent, and detect when the overall task is done.

Granite is **only** a control-plane operator -- it does not judge code quality
or perform any user-visible reasoning. Quality assessment is the PM session's
responsibility.

Public surface:
    GraniteRouter().route(pm_events=..., dev_events=..., operator_events=...) -> RouterDecision
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

try:
    from ollama import chat as ollama_chat
except ImportError:  # pragma: no cover -- ollama is a hard runtime dep
    ollama_chat = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "granite4.1:3b"
HISTORY_KEEP_LAST_N = 8  # granite4.1:3b ctx is large but PoC keeps it tight


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

OPERATOR_TOOLS: list[dict[str, Any]] = [
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
    {
        "type": "function",
        "function": {
            "name": "handle_choice",
            "description": (
                "Respond to a multiple-choice prompt issued by a Claude Code "
                "session. Pick one of the numbered options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "choice": {
                        "type": "string",
                        "description": "The chosen option, e.g. '1' or '2'.",
                    }
                },
                "required": ["choice"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "probe_session",
            "description": (
                "Send a probe message to a session that has gone silent, asking "
                "whether it is still working or has wrapped up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the session is being probed.",
                    }
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "signal_done",
            "description": (
                "Signal that the overall task is complete because the PM session "
                "explicitly indicated finished work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "result_summary": {
                        "type": "string",
                        "description": "Final summary of what was accomplished.",
                    }
                },
                "required": ["result_summary"],
            },
        },
    },
]

VALID_TOOLS = {t["function"]["name"] for t in OPERATOR_TOOLS}


# ---------------------------------------------------------------------------
# Decision contract
# ---------------------------------------------------------------------------


RouterAction = Literal["send_to_dev", "send_to_pm", "probe", "restart", "done", "noop"]


@dataclass
class RouterDecision:
    """Structured outcome of one granite routing call."""

    action: RouterAction
    payload: str
    target: Literal["pm", "dev", "none"] = "none"
    tool_name: str | None = None
    raw_arguments: dict | None = None


class GraniteRoutingError(RuntimeError):
    """Raised when granite itself fails (ollama exception, no tool dispatched, ...)."""


# ---------------------------------------------------------------------------
# Event summarization helpers
# ---------------------------------------------------------------------------


def _stringify_arguments(arguments: Any) -> dict:
    """Normalize granite's `function.arguments` (sometimes a dict, sometimes a JSON string)."""
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


def summarize_events(events: list[dict] | None, label: str) -> str:
    """Compact, human-readable summary of stream-json events for granite context.

    Keeps the result event's text verbatim (it's the substantive content) and
    notes the count of tool_use/text events. Synthetic events from
    ClaudeSession (timeout, decode_error, broken_pipe) are surfaced explicitly
    so granite can recognize hangs and crashes.
    """
    if not events:
        return f"{label}: no events"
    result_text: str | None = None
    tool_use_count = 0
    text_chunks: list[str] = []
    synthetic: list[str] = []
    for ev in events:
        et = ev.get("type")
        if et == "result":
            res = ev.get("result")
            if isinstance(res, str):
                result_text = res
            else:
                # streaming format wraps result inside a dict sometimes
                result_text = str(res)
        elif et == "assistant":
            msg = ev.get("message", {})
            for chunk in msg.get("content", []) or []:
                if isinstance(chunk, dict):
                    if chunk.get("type") == "text":
                        t = chunk.get("text") or ""
                        if t.strip():
                            text_chunks.append(t.strip()[:400])
                    elif chunk.get("type") == "tool_use":
                        tool_use_count += 1
        elif et == "tool_use":
            tool_use_count += 1
        elif et in {"timeout", "decode_error", "broken_pipe"}:
            synthetic.append(f"[{et}] {ev.get('reason') or ev.get('error') or ''}")

    parts = [f"{label} summary:"]
    if result_text is not None:
        parts.append(f"result_text: {result_text[:1200]}")
    if tool_use_count:
        parts.append(f"tool_use events: {tool_use_count}")
    if text_chunks:
        parts.append("interim text: " + " | ".join(text_chunks[:3]))
    if synthetic:
        parts.append("operator_events: " + "; ".join(synthetic))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# GraniteRouter
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "You are the operator routing messages between two Claude Code sessions: "
    "PM (Opus, planner) and Dev (Sonnet, implementer). You receive event "
    "summaries from one or both sessions plus operator_events (timeouts, "
    "decode errors, broken pipes). For each turn you MUST call exactly one "
    "tool. Choose:\n"
    "  - extract_dev_prompt: PM just produced instructions for Dev.\n"
    "  - summarize_for_pm: Dev finished a turn -- distill it for PM.\n"
    "  - handle_choice: the session emitted a numbered multiple-choice prompt.\n"
    "  - probe_session: a session has been silent / appears stalled.\n"
    "  - signal_done: PM explicitly says the task is complete.\n"
    "Never reply with free-form text -- always call a tool."
)


@dataclass
class GraniteRouter:
    """Stateful ollama+granite4.1:3b router.

    The conversation history is kept in `self.messages`. After every routing
    decision the tool call and the tool result are appended, then the history
    is truncated to the last `HISTORY_KEEP_LAST_N` non-system messages.
    """

    model: str = DEFAULT_MODEL
    messages: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages.append({"role": "system", "content": SYSTEM_PROMPT})

    # --- public API --------------------------------------------------------

    def route(
        self,
        *,
        pm_events: list[dict] | None = None,
        dev_events: list[dict] | None = None,
        operator_events: list[dict] | None = None,
        task: str | None = None,
    ) -> RouterDecision:
        """Make one routing decision.

        Args:
            pm_events: PM session stream-json events (if PM just spoke).
            dev_events: Dev session stream-json events (if Dev just spoke).
            operator_events: Surfaced operator events (timeouts, crashes, ...).
            task: Initial task string -- pass on the very first call only.

        Returns:
            RouterDecision describing the next action.

        Raises:
            GraniteRoutingError: ollama call failed, or granite produced no
                tool call (i.e. fell back to free-form text).
        """
        if ollama_chat is None:
            raise GraniteRoutingError("ollama Python package is not installed")

        user_message = self._build_user_message(
            pm_events=pm_events,
            dev_events=dev_events,
            operator_events=operator_events,
            task=task,
        )
        self.messages.append({"role": "user", "content": user_message})
        self._truncate_history()

        try:
            response = ollama_chat(
                model=self.model,
                messages=self.messages,
                tools=OPERATOR_TOOLS,
            )
        except Exception as exc:  # noqa: BLE001 -- granite failure is a routing error
            raise GraniteRoutingError(f"ollama.chat failed: {exc!r}") from exc

        msg = getattr(response, "message", None) or response.get("message", {})
        if hasattr(msg, "tool_calls"):
            tool_calls = msg.tool_calls
        else:
            tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else None
        content = (
            getattr(msg, "content", None)
            if hasattr(msg, "content")
            else (msg.get("content") if isinstance(msg, dict) else None)
        )

        # Persist the assistant turn so subsequent calls have context.
        self.messages.append(
            {
                "role": "assistant",
                "content": content or "",
                "tool_calls": _serialize_tool_calls(tool_calls),
            }
        )

        if not tool_calls:
            raise GraniteRoutingError(f"granite produced no tool_calls; raw content={content!r}")

        first_call = tool_calls[0]
        fn = getattr(first_call, "function", None) or first_call.get("function", {})
        name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
        args = getattr(fn, "arguments", None) or (
            fn.get("arguments") if isinstance(fn, dict) else None
        )
        arguments = _stringify_arguments(args)

        if not name or name not in VALID_TOOLS:
            raise GraniteRoutingError(f"granite called unknown tool: {name!r}")

        decision = _decision_for_tool(name, arguments)
        # Append a tool-role message so the next chat() turn sees the result.
        self.messages.append(
            {
                "role": "tool",
                "content": json.dumps({"action": decision.action, "payload": decision.payload}),
            }
        )
        self._truncate_history()
        return decision

    # --- internals ---------------------------------------------------------

    def _build_user_message(
        self,
        *,
        pm_events: list[dict] | None,
        dev_events: list[dict] | None,
        operator_events: list[dict] | None,
        task: str | None,
    ) -> str:
        parts: list[str] = []
        if task:
            parts.append(f"INITIAL TASK: {task}")
        if pm_events is not None:
            parts.append(summarize_events(pm_events, "PM"))
        if dev_events is not None:
            parts.append(summarize_events(dev_events, "DEV"))
        if operator_events:
            parts.append("operator_events: " + json.dumps(operator_events, default=str)[:1500])
        if not parts:
            parts.append("No new session output. Decide what to do next.")
        parts.append(
            "Choose exactly ONE tool. If PM produced new instructions, route to Dev. "
            "If Dev finished a turn, summarize for PM. If PM said the task is done, "
            "signal_done."
        )
        return "\n\n".join(parts)

    def _truncate_history(self) -> None:
        if len(self.messages) <= HISTORY_KEEP_LAST_N + 1:  # +1 for system
            return
        system_msg = self.messages[0]
        tail = self.messages[-HISTORY_KEEP_LAST_N:]
        # A role:"tool" message is only valid when preceded by the assistant
        # turn whose tool_calls it answers. If the tail window begins on one or
        # more tool messages, their parent assistant turn was sliced away --
        # drop the orphans so we never emit a tool message with no parent.
        while tail and tail[0].get("role") == "tool":
            tail = tail[1:]
        self.messages = [system_msg, *tail]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_tool_calls(tool_calls: Any) -> list[dict] | None:
    """Convert ollama's tool_calls objects to plain dicts for history storage."""
    if not tool_calls:
        return None
    out: list[dict] = []
    for tc in tool_calls:
        fn = getattr(tc, "function", None) or tc.get("function", {})
        name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
        args = getattr(fn, "arguments", None) or (
            fn.get("arguments") if isinstance(fn, dict) else None
        )
        out.append({"function": {"name": name, "arguments": _stringify_arguments(args)}})
    return out


def _decision_for_tool(name: str, arguments: dict) -> RouterDecision:
    if name == "extract_dev_prompt":
        return RouterDecision(
            action="send_to_dev",
            target="dev",
            payload=str(arguments.get("dev_prompt") or "").strip(),
            tool_name=name,
            raw_arguments=arguments,
        )
    if name == "summarize_for_pm":
        return RouterDecision(
            action="send_to_pm",
            target="pm",
            payload=str(arguments.get("summary") or "").strip(),
            tool_name=name,
            raw_arguments=arguments,
        )
    if name == "handle_choice":
        return RouterDecision(
            action="send_to_dev",  # choices always go back to the session that asked
            target="dev",
            payload=str(arguments.get("choice") or "1").strip(),
            tool_name=name,
            raw_arguments=arguments,
        )
    if name == "probe_session":
        return RouterDecision(
            action="probe",
            target="dev",
            payload="still working or wrapped up?",
            tool_name=name,
            raw_arguments=arguments,
        )
    if name == "signal_done":
        return RouterDecision(
            action="done",
            target="none",
            payload=str(arguments.get("result_summary") or "").strip(),
            tool_name=name,
            raw_arguments=arguments,
        )
    raise GraniteRoutingError(f"_decision_for_tool: unhandled tool name {name!r}")
