"""Unit tests for the hook-driven turn-boundary authority (plan #1688, Task 2).

Exercises ``Container._await_turn_end`` / ``_cycle_turn`` directly with a fake
PTY + a scripted ``HookEdgeConsumer``, sub-second, no ollama, no real spawn:

- parent ``Stop`` edge → turn complete, reads from the payload's transcript_path
- ``SubagentStop`` never ends the parent turn (Practice 5) — the wedge that used
  to truncate a Dev turn the instant a subagent finished
- ``needs_human`` edge → the deterministic ``[/user]`` route (no classifier)
- ``compaction`` edge → ignored, keeps waiting (Practice 8)
- crash (PTY EOF / !isalive, no Stop) → bounded resume + ``continue``; retry cap
  exhausted → escalate (no infinite loop)
- Race 2: a late ``Stop`` preceding a clean EOF is honored, not treated as crash
- the turn-detection-wedge green-swap: with the Stop edge present, turn-end is
  detected even though the idle bar was stripped (read_until_idle saw_idle=False)
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from agent.granite_container.container import Container
from agent.granite_container.hook_edge import (
    COMPACTION,
    NEEDS_HUMAN,
    SUBAGENT_END,
    TURN_END,
    HookEdge,
)
from agent.granite_container.pty_driver import IdleResult

PM_SID = "pm-session-uuid"


def _edge(kind: str, *, session_id: str = PM_SID, ts: float = 1.0, **payload) -> HookEdge:
    payload.setdefault("session_id", session_id)
    return HookEdge(kind=kind, event=kind, payload=payload, ts=ts)


class _ScriptedConsumer:
    """A fake HookEdgeConsumer whose poll() yields scripted edge batches."""

    def __init__(self, batches: list[list[HookEdge]]) -> None:
        self._batches = list(batches)

    def poll(self) -> list[HookEdge]:
        if self._batches:
            return self._batches.pop(0)
        return []


def _fake_pty(*, alive_sequence: list[bool] | None = None, resume_uuid: str | None = None):
    """A fake PTY: read_until_idle returns a stripped-bar idle (saw_idle=False),
    isalive follows alive_sequence (default: always alive)."""
    pty = MagicMock()
    pty.read_until_idle.return_value = IdleResult(
        saw_idle=False, buffer="", idle_marker="", elapsed_ms=1, turn_buffer=""
    )
    if alive_sequence is None:
        pty.isalive.return_value = True
    else:
        pty.isalive.side_effect = list(alive_sequence)
    pty.last_resume_uuid.return_value = resume_uuid
    pty.cwd = "/tmp/cwd"
    pty._explicit_model = "opus"
    pty._extra_env = None
    pty._settings_path = "/tmp/settings.json"
    return pty


def _container(**kwargs) -> Container:
    # Fast wait so a "no Stop" path returns quickly in the timeout tests.
    kwargs.setdefault("hook_driven", True)
    kwargs.setdefault("hook_turn_end_wait_s", 0.5)
    kwargs.setdefault("crash_resume_cap", 2)
    return Container(user_message="hi", pm_session_id=PM_SID, **kwargs)


class TestAwaitTurnEnd(unittest.TestCase):
    def test_parent_stop_completes_turn(self) -> None:
        consumer = _ScriptedConsumer([[_edge(TURN_END, transcript_path="/t.jsonl")]])
        c = _container()
        res = c._await_turn_end(_fake_pty(), consumer, PM_SID, role="pm")
        self.assertTrue(res.saw_turn)
        self.assertEqual(res.transcript_path, "/t.jsonl")
        self.assertIsNone(res.needs_human)
        self.assertFalse(res.escalated)

    def test_subagent_stop_never_ends_parent_turn(self) -> None:
        """Practice 5: a SubagentStop must not complete the turn; it times out."""
        consumer = _ScriptedConsumer([[_edge(SUBAGENT_END, agent_id="a1")]])
        c = _container(hook_turn_end_wait_s=0.3)
        res = c._await_turn_end(_fake_pty(), consumer, PM_SID, role="pm")
        self.assertFalse(res.saw_turn, "SubagentStop alone must not end the parent turn")

    def test_stop_for_other_session_ignored(self) -> None:
        """A Stop for a different session_id (Dev) does not end the PM turn."""
        consumer = _ScriptedConsumer([[_edge(TURN_END, session_id="dev-sid")]])
        c = _container(hook_turn_end_wait_s=0.3)
        res = c._await_turn_end(_fake_pty(), consumer, PM_SID, role="pm")
        self.assertFalse(res.saw_turn)

    def test_needs_human_routes_to_user(self) -> None:
        consumer = _ScriptedConsumer([[_edge(NEEDS_HUMAN, message="Which env?")]])
        c = _container()
        res = c._await_turn_end(_fake_pty(), consumer, PM_SID, role="pm")
        self.assertFalse(res.saw_turn)
        self.assertIsNotNone(res.needs_human)
        self.assertEqual(c._needs_human_message(res.needs_human), "Which env?")

    def test_compaction_edge_ignored(self) -> None:
        """Practice 8: a compaction edge is not turn-end; the wait continues."""
        consumer = _ScriptedConsumer(
            [[_edge(COMPACTION)], [_edge(TURN_END, transcript_path="/t.jsonl")]]
        )
        c = _container(hook_turn_end_wait_s=2.0)
        res = c._await_turn_end(_fake_pty(), consumer, PM_SID, role="pm")
        self.assertTrue(res.saw_turn)
        self.assertEqual(res.transcript_path, "/t.jsonl")

    def test_timeout_while_alive_returns_no_turn(self) -> None:
        consumer = _ScriptedConsumer([])  # no edges ever
        c = _container(hook_turn_end_wait_s=0.3)
        res = c._await_turn_end(_fake_pty(), consumer, PM_SID, role="pm")
        self.assertFalse(res.saw_turn)
        self.assertIsNone(res.needs_human)
        self.assertFalse(res.escalated)


class TestCrashResume(unittest.TestCase):
    def test_crash_then_resume_then_complete(self) -> None:
        """PTY dies with no Stop → resume via --resume + continue → next Stop completes."""
        # Batch 1: nothing. After the liveness read, isalive=False → crash → resume.
        # Batch 2 (after resume re-arm): the Stop lands.
        consumer = _ScriptedConsumer([[], [], [_edge(TURN_END, transcript_path="/t.jsonl")]])
        c = _container(hook_turn_end_wait_s=2.0)
        # Stub the resume so no real spawn happens; return a fresh alive PTY.
        resumed_pty = _fake_pty()
        c._resume_crashed_pty = MagicMock(return_value=resumed_pty)  # type: ignore[method-assign]
        dead = _fake_pty(alive_sequence=[False, True, True, True])
        res = c._await_turn_end(dead, consumer, PM_SID, role="pm")
        self.assertTrue(res.saw_turn)
        c._resume_crashed_pty.assert_called_once()

    def test_retry_cap_exhausted_escalates(self) -> None:
        """Repeated crashes beyond the cap escalate — no infinite loop."""
        consumer = _ScriptedConsumer([])  # never a Stop
        c = _container(hook_turn_end_wait_s=5.0, crash_resume_cap=2)
        # Every resumed PTY is also dead → crash every tick; cap=2 → 3rd crash escalates.
        c._resume_crashed_pty = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda p, r: _fake_pty(alive_sequence=[False] * 10)
        )
        dead = _fake_pty(alive_sequence=[False] * 10)
        res = c._await_turn_end(dead, consumer, PM_SID, role="pm")
        self.assertTrue(res.escalated)
        self.assertFalse(res.saw_turn)
        self.assertEqual(c._resume_crashed_pty.call_count, 2)

    def test_no_resume_handle_escalates(self) -> None:
        """A crash with no captured --resume uuid escalates instead of looping."""
        consumer = _ScriptedConsumer([])
        c = _container(hook_turn_end_wait_s=5.0)
        dead = _fake_pty(alive_sequence=[False, False], resume_uuid=None)
        res = c._await_turn_end(dead, consumer, PM_SID, role="pm")
        self.assertTrue(res.escalated)

    def test_late_stop_before_eof_is_not_crash(self) -> None:
        """Race 2: a Stop edge draining just as the PTY hits EOF is honored."""
        # First poll: empty. Liveness read → isalive False. Re-drain → the late Stop.
        consumer = _ScriptedConsumer([[], [_edge(TURN_END, transcript_path="/late.jsonl")]])
        c = _container(hook_turn_end_wait_s=2.0)
        c._resume_crashed_pty = MagicMock(return_value=None)  # type: ignore[method-assign]
        dead = _fake_pty(alive_sequence=[False, False])
        res = c._await_turn_end(dead, consumer, PM_SID, role="pm")
        self.assertTrue(res.saw_turn, "late Stop before EOF must complete, not crash")
        self.assertEqual(res.transcript_path, "/late.jsonl")
        c._resume_crashed_pty.assert_not_called()


class TestTurnDetectionWedgeGreen(unittest.TestCase):
    """The failure_class-1 wedge (idle bar stripped) no longer wedges: the Stop
    edge supplies turn-end independent of the bar (plan Success Criterion)."""

    def test_turn_detection_wedge_green_via_hook_edge(self) -> None:
        # The PTY never reports saw_idle (bar stripped) — the pre-#1688 authority
        # would wedge forever. The Stop edge completes the turn deterministically.
        consumer = _ScriptedConsumer([[_edge(TURN_END, transcript_path="/t.jsonl")]])
        c = _container()
        pty = _fake_pty()  # read_until_idle always saw_idle=False
        res = c._cycle_turn(pty, consumer, PM_SID, role="pm")
        self.assertTrue(res.saw_turn, "hook edge detects turn-end despite the stripped idle bar")

    def test_idle_fallback_used_when_flag_off(self) -> None:
        """Flag off → _cycle_turn delegates to the idle heuristic (fallback path)."""
        c = _container(hook_driven=False)
        pty = MagicMock()
        pty.read_until_idle.return_value = IdleResult(
            saw_idle=True, buffer="done", idle_marker="bar", elapsed_ms=5, turn_buffer="done"
        )
        res = c._cycle_turn(pty, None, PM_SID, role="pm")
        self.assertTrue(res.saw_turn)
        self.assertEqual(res.buffer, "done")


class TestContainerConsumerWiring(unittest.TestCase):
    def test_consumers_none_when_flag_off(self) -> None:
        c = Container(
            user_message="hi",
            pm_session_id=PM_SID,
            pm_hook_edge_file="/tmp/pm.ndjson",
            hook_driven=False,
        )
        self.assertIsNone(c._pm_consumer)

    def test_pm_consumer_built_when_edge_file_and_flag_on(self) -> None:
        c = Container(
            user_message="hi",
            pm_session_id=PM_SID,
            pm_hook_edge_file="/tmp/pm.ndjson",
            hook_driven=True,
        )
        self.assertIsNotNone(c._pm_consumer)
        self.assertEqual(c._pm_consumer.session_id, PM_SID)


if __name__ == "__main__":
    unittest.main()
