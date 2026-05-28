"""Unit tests for `agent.granite_router.GraniteRouter` (granite PoC).

`ollama.chat` is patched on `agent.granite_router.ollama_chat`. The fake
returns ollama-shaped dicts/objects so we exercise both code paths in
`_serialize_tool_calls` and `_stringify_arguments`.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest

from agent.granite_router import (
    GraniteRouter,
    GraniteRoutingError,
    RouterDecision,
    summarize_events,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_response(name: str, arguments: dict[str, Any]) -> dict:
    """Build an ollama-style response with a single tool call."""
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


def _fake_response_no_tool() -> dict:
    return {"message": {"role": "assistant", "content": "no tool here", "tool_calls": None}}


# ---------------------------------------------------------------------------
# Event summarization
# ---------------------------------------------------------------------------


def test_summarize_events_extracts_result_text():
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "result": "the answer"},
    ]
    summary = summarize_events(events, "PM")
    assert "result_text: the answer" in summary
    assert summary.startswith("PM summary:")


def test_summarize_events_counts_tool_use_and_surfaces_synthetic():
    events = [
        {"type": "tool_use", "name": "Bash"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "thinking..."}]},
        },
        {"type": "timeout", "reason": "no data"},
        {"type": "result", "result": "done"},
    ]
    summary = summarize_events(events, "DEV")
    assert "tool_use events: 1" in summary
    assert "operator_events:" in summary
    assert "interim text:" in summary


def test_summarize_events_handles_empty():
    assert summarize_events(None, "PM") == "PM: no events"
    assert summarize_events([], "DEV") == "DEV: no events"


# ---------------------------------------------------------------------------
# RouterDecision dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,args,expected_action,expected_target,expected_payload_substring",
    [
        (
            "extract_dev_prompt",
            {"dev_prompt": "make a hello world file"},
            "send_to_dev",
            "dev",
            "hello world",
        ),
        (
            "summarize_for_pm",
            {"summary": "Dev wrote 3 files."},
            "send_to_pm",
            "pm",
            "Dev wrote",
        ),
        (
            "handle_choice",
            {"choice": "1"},
            "send_to_dev",
            "dev",
            "1",
        ),
        (
            "probe_session",
            {"reason": "silent for 130s"},
            "probe",
            "dev",
            "wrapped",
        ),
        (
            "signal_done",
            {"result_summary": "task complete"},
            "done",
            "none",
            "task complete",
        ),
    ],
)
def test_route_dispatches_each_tool(
    tool_name, args, expected_action, expected_target, expected_payload_substring
):
    router = GraniteRouter()
    with mock.patch(
        "agent.granite_router.ollama_chat", return_value=_fake_response(tool_name, args)
    ) as chat:
        decision = router.route(pm_events=[], task="do a thing")
    assert isinstance(decision, RouterDecision)
    assert decision.action == expected_action
    assert decision.target == expected_target
    assert expected_payload_substring.lower() in decision.payload.lower()
    chat.assert_called_once()
    # history should now contain system + user + assistant + tool
    roles = [m["role"] for m in router.messages]
    assert roles[0] == "system"
    assert "tool" in roles


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_route_raises_when_no_tool_call():
    router = GraniteRouter()
    with mock.patch("agent.granite_router.ollama_chat", return_value=_fake_response_no_tool()):
        with pytest.raises(GraniteRoutingError):
            router.route(pm_events=[], task="x")


def test_route_raises_when_tool_name_unknown():
    router = GraniteRouter()
    with mock.patch(
        "agent.granite_router.ollama_chat",
        return_value=_fake_response("frobnicate", {"foo": "bar"}),
    ):
        with pytest.raises(GraniteRoutingError):
            router.route(pm_events=[], task="x")


def test_route_raises_when_ollama_throws():
    router = GraniteRouter()
    with mock.patch("agent.granite_router.ollama_chat", side_effect=RuntimeError("ollama dead")):
        with pytest.raises(GraniteRoutingError):
            router.route(pm_events=[], task="x")


def test_arguments_can_be_json_string():
    """Some ollama backends serialize arguments as a JSON string."""
    router = GraniteRouter()
    response = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "extract_dev_prompt",
                        "arguments": json.dumps({"dev_prompt": "hi dev"}),
                    }
                }
            ],
        }
    }
    with mock.patch("agent.granite_router.ollama_chat", return_value=response):
        decision = router.route(pm_events=[])
    assert decision.action == "send_to_dev"
    assert decision.payload == "hi dev"


# ---------------------------------------------------------------------------
# History management
# ---------------------------------------------------------------------------


def test_history_truncation_preserves_system_message():
    router = GraniteRouter()
    fake = _fake_response("signal_done", {"result_summary": "ok"})
    with mock.patch("agent.granite_router.ollama_chat", return_value=fake):
        for _ in range(12):
            router.route(pm_events=[{"type": "result", "result": "x"}])
    assert router.messages[0]["role"] == "system"
    # 1 system + at most HISTORY_KEEP_LAST_N tail entries
    assert len(router.messages) <= 1 + 8


def test_route_handles_missing_events_gracefully():
    """If no events and no task, granite still gets a user message."""
    router = GraniteRouter()
    fake = _fake_response("probe_session", {"reason": "no input"})
    with mock.patch("agent.granite_router.ollama_chat", return_value=fake) as chat:
        decision = router.route()
    assert decision.action == "probe"
    user_msg = next(m for m in router.messages if m["role"] == "user")
    assert "No new session output" in user_msg["content"]
    chat.assert_called_once()
