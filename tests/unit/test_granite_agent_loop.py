"""Unit tests for `agent.granite_agent_loop.GraniteAgentLoop` (granite PoC).

The loop is the integration point that had zero coverage. These tests drive it
with a scripted `FakeRouter` (no ollama) and `FakeClaudeSession`s (no real
`claude` subprocess), both from `tests.unit.granite_session_emulator`. Every
exit path, the crash/restart path, operator-event forwarding, the teardown
guarantee, and the trace log are locked down here.
"""

from __future__ import annotations

import json
import threading

import pytest

from agent.granite_router import GraniteRoutingError, RouterDecision
from tests.unit.granite_session_emulator import (
    FakeClaudeSession,
    FakeRouter,
    crash_turn,
    patch_sessions,
    result_event,
    timeout_event,
)

# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------


def send_to_dev(payload: str = "do the thing") -> RouterDecision:
    return RouterDecision(
        action="send_to_dev", target="dev", payload=payload, tool_name="extract_dev_prompt"
    )


def send_to_pm(payload: str = "review this") -> RouterDecision:
    return RouterDecision(
        action="send_to_pm", target="pm", payload=payload, tool_name="summarize_for_pm"
    )


def done(payload: str = "finished") -> RouterDecision:
    return RouterDecision(action="done", target="none", payload=payload, tool_name="signal_done")


def _loop(monkeypatch, *, pm_script, dev_script, decisions, tmp_path):
    from agent.granite_agent_loop import GraniteAgentLoop

    pm = FakeClaudeSession(script=pm_script, model="opus")
    dev = FakeClaudeSession(script=dev_script, model="sonnet")
    patch_sessions(monkeypatch, pm, dev)
    router = FakeRouter(decisions=decisions)
    loop = GraniteAgentLoop(router=router, trace_path=str(tmp_path / "trace.jsonl"))
    return loop, pm, dev, router


# ---------------------------------------------------------------------------
# Exit paths
# ---------------------------------------------------------------------------


def test_happy_path_completes_via_task_complete_phrase(monkeypatch, tmp_path):
    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[[result_event("created hello_poc.py")]],
        pm_script=[[result_event("TASK COMPLETE: hello_poc.py created and verified")]],
        decisions=[send_to_dev("create hello_poc.py"), send_to_pm("dev made the file")],
        tmp_path=tmp_path,
    )
    res = loop.run("write hello_poc.py", max_turns=10)
    assert res.status == "done"
    assert "TASK COMPLETE" in res.final_payload
    assert dev.sent_messages == ["create hello_poc.py"]
    assert pm.sent_messages == ["dev made the file"]


def test_done_via_signal_done_tool(monkeypatch, tmp_path):
    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[[result_event("did the work")]],
        pm_script=[[result_event("looks good so far")]],  # no TASK COMPLETE phrase
        decisions=[send_to_dev(), send_to_pm(), done("all wrapped up")],
        tmp_path=tmp_path,
    )
    res = loop.run("do a task")
    assert res.status == "done"
    assert res.final_payload == "all wrapped up"
    assert res.turns == 3


def test_max_turns_reached(monkeypatch, tmp_path):
    # Router only ever routes to dev, never done; PM never reached -> no completion.
    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[],  # falls back to a generic result each turn
        pm_script=[],
        decisions=[send_to_dev(), send_to_dev(), send_to_dev(), send_to_dev()],
        tmp_path=tmp_path,
    )
    res = loop.run("never-ending task", max_turns=3)
    assert res.status == "max_turns_reached"
    assert res.turns == 3


def test_initial_route_error_returns_turns_zero(monkeypatch, tmp_path):
    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[],
        pm_script=[],
        decisions=[GraniteRoutingError("granite refused to route initial task")],
        tmp_path=tmp_path,
    )
    res = loop.run("a task")
    assert res.status == "granite_routing_error"
    assert res.turns == 0
    assert "refused" in (res.error or "")


def test_empty_task_raises(monkeypatch, tmp_path):
    loop, *_ = _loop(
        monkeypatch, dev_script=[], pm_script=[], decisions=[done()], tmp_path=tmp_path
    )
    with pytest.raises(ValueError):
        loop.run("   ")


# ---------------------------------------------------------------------------
# Crash / restart
# ---------------------------------------------------------------------------


def test_send_failure_resumes_when_session_id_known(monkeypatch, tmp_path):
    from agent.granite_agent_loop import GraniteAgentLoop

    # Dev already captured a session_id, so the crash is recovered via resume.
    dev = FakeClaudeSession(script=[crash_turn()], model="sonnet", session_id="dev-uuid-123")
    pm = FakeClaudeSession(script=[], model="opus")
    patch_sessions(monkeypatch, pm, dev)
    router = FakeRouter(decisions=[send_to_dev("will crash"), done("recovered")])
    loop = GraniteAgentLoop(router=router, trace_path=str(tmp_path / "t.jsonl"))

    res = loop.run("task that crashes dev")
    assert res.status == "done"
    assert dev.resume_count == 1
    assert dev.restart_count == 0  # resumed, not respawned fresh
    assert router.calls[-1]["operator_events"] == [
        {"type": "crash", "session": "dev", "recovered_via": "resume"}
    ]


def test_send_failure_restarts_when_no_session_id(monkeypatch, tmp_path):
    from agent.granite_agent_loop import GraniteAgentLoop

    # No session_id captured -> resume() falls back to a fresh session.
    dev = FakeClaudeSession(script=[crash_turn()], model="sonnet", session_id=None)
    pm = FakeClaudeSession(script=[], model="opus")
    patch_sessions(monkeypatch, pm, dev)
    router = FakeRouter(decisions=[send_to_dev("will crash"), done("recovered")])
    loop = GraniteAgentLoop(router=router, trace_path=str(tmp_path / "t.jsonl"))

    res = loop.run("task that crashes dev")
    assert res.status == "done"
    assert dev.resume_count == 1
    assert router.calls[-1]["operator_events"] == [
        {"type": "crash", "session": "dev", "recovered_via": "restart"}
    ]


# ---------------------------------------------------------------------------
# Operator-event forwarding
# ---------------------------------------------------------------------------


def test_operator_events_are_forwarded_to_router(monkeypatch, tmp_path):
    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[[timeout_event(), result_event("partial output")]],
        pm_script=[],
        decisions=[send_to_dev(), done()],
        tmp_path=tmp_path,
    )
    loop.run("task that times out mid-turn")
    # The route() call after the dev turn must carry exactly the timeout event.
    routed = router.calls[1]["operator_events"]
    assert routed == [timeout_event()]


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------


def test_unknown_action_is_probed_not_crashed(monkeypatch, tmp_path):
    noop = RouterDecision(action="noop", target="none", payload="")
    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[],
        pm_script=[],
        decisions=[noop, done("recovered from unknown action")],
        tmp_path=tmp_path,
    )
    res = loop.run("task that yields an unknown action")
    assert res.status == "done"
    assert router.calls[1]["operator_events"] == [{"type": "unknown_action"}]


# ---------------------------------------------------------------------------
# Teardown guarantee
# ---------------------------------------------------------------------------


def test_both_sessions_torn_down_on_normal_exit(monkeypatch, tmp_path):
    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[[result_event("done")]],
        pm_script=[[result_event("TASK COMPLETE done")]],
        decisions=[send_to_dev(), send_to_pm()],
        tmp_path=tmp_path,
    )
    loop.run("a task")
    assert pm.stop_count >= 1
    assert dev.stop_count >= 1


def test_both_sessions_torn_down_on_unexpected_exception(monkeypatch, tmp_path):
    class BoomError(RuntimeError):
        pass

    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[[result_event("ok")]],
        pm_script=[],
        decisions=[send_to_dev(), BoomError("router blew up")],
        tmp_path=tmp_path,
    )
    with pytest.raises(BoomError):
        loop.run("task that explodes")
    assert pm.stop_count >= 1
    assert dev.stop_count >= 1


# ---------------------------------------------------------------------------
# Trace log
# ---------------------------------------------------------------------------


def test_trace_log_is_valid_jsonl(monkeypatch, tmp_path):
    trace = tmp_path / "trace.jsonl"
    loop, pm, dev, router = _loop(
        monkeypatch,
        dev_script=[[result_event("ok")]],
        pm_script=[[result_event("TASK COMPLETE")]],
        decisions=[send_to_dev(), send_to_pm()],
        tmp_path=tmp_path,
    )
    loop.run("a task")
    lines = trace.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "trace log should not be empty"
    stages = set()
    for line in lines:
        entry = json.loads(line)  # raises if any line is not valid JSON
        assert "ts" in entry and "turn" in entry
        stages.add(entry.get("stage"))
    assert any("result" in s for s in stages if s)


# ---------------------------------------------------------------------------
# Signal handler installation off the main thread
# ---------------------------------------------------------------------------


def test_construction_off_main_thread_does_not_raise():
    from agent.granite_agent_loop import GraniteAgentLoop

    errors: list[Exception] = []

    def build():
        try:
            GraniteAgentLoop(router=FakeRouter(decisions=[done()]))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=build)
    t.start()
    t.join()
    assert not errors  # signal.signal ValueError must be swallowed


# ---------------------------------------------------------------------------
# Ctrl-C (SIGINT) handling
# ---------------------------------------------------------------------------


def test_sigint_tears_down_both_sessions_and_exits_130():
    import signal as _signal

    from agent.granite_agent_loop import GraniteAgentLoop

    loop = GraniteAgentLoop(router=FakeRouter(decisions=[done()]))
    pm = FakeClaudeSession(script=[], model="opus", session_id="pm-uuid")
    dev = FakeClaudeSession(script=[], model="sonnet", session_id="dev-uuid")
    pm.start()
    dev.start()
    loop.pm_session = pm
    loop.dev_session = dev

    with pytest.raises(SystemExit) as ei:
        loop._on_signal(_signal.SIGINT, None)
    assert ei.value.code == 128 + _signal.SIGINT  # 130 for ctrl-c
    assert pm.stop_count >= 1
    assert dev.stop_count >= 1


def test_sigint_logs_resume_command_for_each_session(caplog):
    import logging
    import signal as _signal

    from agent.granite_agent_loop import GraniteAgentLoop

    loop = GraniteAgentLoop(router=FakeRouter(decisions=[done()]))
    loop.pm_session = FakeClaudeSession(script=[], model="opus", session_id="pm-uuid-aaa")
    loop.dev_session = FakeClaudeSession(script=[], model="sonnet", session_id="dev-uuid-bbb")

    with caplog.at_level(logging.WARNING), pytest.raises(SystemExit):
        loop._on_signal(_signal.SIGINT, None)

    messages = [r.getMessage() for r in caplog.records]
    assert any("claude --resume pm-uuid-aaa" in m for m in messages)
    assert any("claude --resume dev-uuid-bbb" in m for m in messages)
