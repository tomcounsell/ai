"""Tests for Claude Code session *peculiarities* the operator loop must handle.

Claude Code sessions don't just stream text and finish. They can:

* emit a numbered **multiple-choice question** the operator must answer,
* surface a **permission / feedback approval prompt**,
* **crash** and need to be respawned (and, in a fuller design, resumed).

These tests emulate each peculiarity with the
`tests.unit.granite_session_emulator` builders and assert how
`GraniteAgentLoop` and `GraniteRouter` react. The live counterpart -- whether
granite4.1:3b can actually *recognize* and answer these unaided -- lives in
`scripts/granite_questions_game.py` and the gated integration test
`tests/integration/test_granite_questions_game.py`.
"""

from __future__ import annotations

from unittest import mock

from agent.granite_router import GraniteRouter, RouterDecision, summarize_events
from tests.unit.granite_session_emulator import (
    FakeClaudeSession,
    FakeRouter,
    feedback_prompt_turn,
    multiple_choice_text,
    multiple_choice_turn,
    patch_sessions,
    result_event,
)


def _fake_ollama_response(name: str, arguments: dict) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


# ---------------------------------------------------------------------------
# Multiple-choice questions
# ---------------------------------------------------------------------------


def test_multiple_choice_text_uses_tui_marker():
    menu = multiple_choice_text("Proceed?", ["Yes", "No"], selected=2)
    assert "1. Yes" in menu
    assert "❯ 2. No" in menu  # U+276F marks the highlighted option
    assert menu.startswith("Proceed?")


def test_operator_sees_question_text_in_summary():
    """summarize_events must surface the question + options so granite can act."""
    events = multiple_choice_turn("Which language?", ["Python", "Rust", "Go"])
    summary = summarize_events(events, "DEV")
    assert "Which language?" in summary
    assert "Python" in summary and "Rust" in summary


def test_router_answers_multiple_choice_via_handle_choice():
    """Given a question summary, a handle_choice tool call routes the answer."""
    router = GraniteRouter()
    dev_events = multiple_choice_turn("Overwrite the file?", ["Yes", "Cancel"])
    with mock.patch(
        "agent.granite_router.ollama_chat",
        return_value=_fake_ollama_response("handle_choice", {"choice": "1"}),
    ):
        decision = router.route(dev_events=dev_events)
    assert decision.action == "send_to_dev"
    assert decision.payload == "1"


def test_loop_sends_chosen_answer_back_to_session(monkeypatch, tmp_path):
    """End-to-end (emulated): a question is answered and the choice reaches Dev."""
    from agent.granite_agent_loop import GraniteAgentLoop

    dev = FakeClaudeSession(
        script=[
            multiple_choice_turn("Pick a strategy", ["fast", "safe"]),
            [result_event("proceeded with option 2")],
        ],
        model="sonnet",
    )
    pm = FakeClaudeSession(script=[[result_event("TASK COMPLETE")]], model="opus")
    patch_sessions(monkeypatch, pm, dev)
    router = FakeRouter(
        decisions=[
            RouterDecision(action="send_to_dev", target="dev", payload="run it"),
            RouterDecision(
                action="send_to_dev", target="dev", payload="2", tool_name="handle_choice"
            ),
            RouterDecision(action="send_to_pm", target="pm", payload="done?"),
        ]
    )
    loop = GraniteAgentLoop(router=router, trace_path=str(tmp_path / "t.jsonl"))
    res = loop.run("a task with a choice")
    assert res.status == "done"
    assert "2" in dev.sent_messages  # the chosen option was delivered to Dev


def test_handle_choice_always_targets_dev_is_a_known_limitation():
    """handle_choice hardcodes target='dev'.

    If the *PM* session is the one that asked a question, the answer would be
    misrouted to Dev. This test documents current behaviour so a future fix
    (route the choice back to the asking session) trips it deliberately.
    """
    router = GraniteRouter()
    with mock.patch(
        "agent.granite_router.ollama_chat",
        return_value=_fake_ollama_response("handle_choice", {"choice": "1"}),
    ):
        decision = router.route(pm_events=multiple_choice_turn("PM asks?", ["a", "b"]))
    assert decision.target == "dev"  # NOT 'pm' -- the known misrouting limitation


# ---------------------------------------------------------------------------
# Permission / feedback approval prompts
# ---------------------------------------------------------------------------


def test_feedback_prompt_is_surfaced_to_operator():
    events = feedback_prompt_turn("Do you want to proceed?", ("Yes", "No"))
    summary = summarize_events(events, "DEV")
    assert "Do you want to proceed?" in summary


def test_loop_handles_feedback_prompt(monkeypatch, tmp_path):
    from agent.granite_agent_loop import GraniteAgentLoop

    dev = FakeClaudeSession(
        script=[feedback_prompt_turn(), [result_event("approved and continued")]],
        model="sonnet",
    )
    pm = FakeClaudeSession(script=[[result_event("TASK COMPLETE")]], model="opus")
    patch_sessions(monkeypatch, pm, dev)
    router = FakeRouter(
        decisions=[
            RouterDecision(action="send_to_dev", target="dev", payload="do work"),
            RouterDecision(
                action="send_to_dev", target="dev", payload="1", tool_name="handle_choice"
            ),
            RouterDecision(action="send_to_pm", target="pm", payload="status?"),
        ]
    )
    loop = GraniteAgentLoop(router=router, trace_path=str(tmp_path / "t.jsonl"))
    res = loop.run("a task that triggers an approval prompt")
    assert res.status == "done"
    assert "1" in dev.sent_messages


# ---------------------------------------------------------------------------
# Crash and resume
# ---------------------------------------------------------------------------


def test_poc_resumes_crashed_session_with_captured_id():
    """Crash recovery preserves context via `claude --resume <session_id>`.

    Like siteboon/claudecodeui, `ClaudeSession` captures the `session_id` from
    the stream-json output (or the on-exit `claude --resume <uuid>` hint) and
    reuses it. `resume()` respawns with `--resume`; it only falls back to a
    fresh session when no id has been seen yet. The fresh-start `restart()`
    remains available for the genuinely-unrecoverable case.
    """
    from agent.claude_session import ClaudeSession, ClaudeSessionConfig, _build_cmd

    # A fresh start carries no --resume; resume with a known id does.
    assert "--resume" not in _build_cmd(ClaudeSessionConfig(model="sonnet", cwd="/tmp"))
    resumed = _build_cmd(
        ClaudeSessionConfig(model="sonnet", cwd="/tmp"), resume_session_id="uuid-123"
    )
    assert resumed[resumed.index("--resume") + 1] == "uuid-123"

    session = ClaudeSession(ClaudeSessionConfig(model="sonnet", cwd="/tmp"))
    assert session.session_id is None  # nothing captured before any turn
    assert hasattr(session, "resume")
