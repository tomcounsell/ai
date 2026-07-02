"""End-to-end transport dispatch test for the headless Dev leg (plan #1842).

This is the test that would have caught the two review BLOCKERS on PR #1848:

* **B1** — ``HeadlessRoleDriver`` was fully built + unit-tested but NEVER wired
  into the container's turn dispatch, so a headless-configured Dev role silently
  ran through the PTY builder harness (dead code in production). This test drives
  a Dev turn through the real container orchestration
  (``Container._route_pm_classification``) with ``dev`` transport = ``headless``
  and asserts the ``HeadlessRoleDriver`` was actually used (a fake harness_fn
  records the metered call; the PTY builder path is poisoned so any fall-through
  fails loud).

* **B2** — a headless-configured session yields a ``None`` pool member, which
  crashed on unguarded dereferences (``_released_to_pool``, both-PTY spawn
  requirement). This test constructs the container with a prewarmed PM PTY and a
  ``None`` Dev PTY and asserts ``_spawn_pair`` / ``_uses_pool_pair`` carry the
  ``None`` cleanly (no self-spawn, no crash).

The dispatch is driven with a FAKE ``harness_fn`` + a real ``HookEdgeConsumer``
over a temp NDJSON edge file, so no real ``claude -p`` subprocess is spawned.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from agent.granite_container.container import (
    DEV_REPORT_UNAVAILABLE,
    PM_TURN_CONTRACT_REMINDER,
    Container,
    ContainerResult,
)
from agent.granite_container.granite_classifier import ClassificationResult
from agent.granite_container.role_driver import HeadlessRoleDriver

pytestmark = pytest.mark.sdlc


def _dev_classification(payload: str) -> ClassificationResult:
    """Build a ``[/dev]`` classification routing ``payload`` to the Dev role."""
    return ClassificationResult(
        destination="dev",
        payload=payload,
        compliance_miss=False,
        raw_first_line=f"[/dev] {payload}",
        harness=None,
    )


def _make_recording_harness(reply="Dev did the work.", record=None, edge_path=None):
    """An async fake harness_fn that records kwargs and (optionally) writes a
    fresh Stop edge as a real subprocess Stop hook would."""

    async def _fake(message, working_dir, **kwargs):
        if record is not None:
            record.append({"message": message, "working_dir": working_dir, **kwargs})
        if edge_path is not None:
            envelope = {
                "event": "Stop",
                "payload": {"hook_event_name": "Stop"},
                "ts": time.time() + 1,
            }
            with open(edge_path, "a") as f:
                f.write(json.dumps(envelope) + "\n")
        return reply

    return _fake


def _make_headless_dev_container(tmp_path, harness_fn, *, pm_pty=None):
    """Construct a container with dev=headless on a prewarmed PM PTY."""
    edge_file = tmp_path / "dev_hook_edges.ndjson"
    edge_file.touch()
    settings_file = tmp_path / "dev_settings.json"
    settings_file.write_text("{}")
    pm = pm_pty if pm_pty is not None else MagicMock()
    container = Container(
        user_message="do the task",
        cwd=str(tmp_path),
        pm_pty=pm,
        dev_pty=None,  # headless Dev — no PTY member (plan #1842 B2)
        role_transports={"pm": "pty", "dev": "headless"},
        dev_session_id="dev-claude-uuid",
        dev_hook_edge_file=str(edge_file),
        dev_settings_path=str(settings_file),
        agent_session_id="agent-sess-1842",
        headless_harness_fn=harness_fn,
        hook_driven=True,
    )
    return container, pm, edge_file


# ---------------------------------------------------------------------------
# B2 — a None pool member flows through spawn/pool logic cleanly
# ---------------------------------------------------------------------------


def test_spawn_pair_carries_none_dev_member_without_self_spawning(tmp_path):
    """B2: with a prewarmed PM PTY and headless Dev, _spawn_pair reuses the pool
    pair (PM present, Dev None) and never self-spawns a fresh PTY pair."""
    container, pm, _ = _make_headless_dev_container(tmp_path, _make_recording_harness())
    # A headless-configured pool session is recognized as pool-backed even though
    # the Dev member is None (the fix for _uses_pool_pair).
    assert container._uses_pool_pair() is True
    container._spawn_pair()
    assert container._pm_pty is pm  # prewarmed PM reused verbatim
    assert container._dev_pty is None  # headless Dev carries None cleanly


# ---------------------------------------------------------------------------
# B1 — a headless Dev turn is dispatched through HeadlessRoleDriver
# ---------------------------------------------------------------------------


def test_headless_dev_turn_dispatched_through_role_driver(tmp_path):
    """B1: a ``[/dev]`` turn with dev=headless runs through HeadlessRoleDriver,
    NOT the PTY builder — lifecycle fields are set through the shared path and
    accounting is metered (never total_*)."""
    calls: list[dict] = []
    harness = _make_recording_harness(
        reply="Dev did the work.", record=calls, edge_path=str(tmp_path / "dev_hook_edges.ndjson")
    )
    container, pm, _ = _make_headless_dev_container(tmp_path, harness)
    container._spawn_pair()

    # Poison the PTY builder path so any fall-through to the dead-code branch
    # fails loud (this is the B1 regression guard).
    def _boom(*a, **k):
        raise AssertionError("PTY builder must NOT be used for a headless Dev role")

    container._get_builder = _boom  # type: ignore[assignment]
    # PM relay idle read is a PTY concern — stub it idle so the shared post-turn
    # relay path runs deterministically without a real PM TUI.
    container._cycle_idle = lambda pty: (True, "", "", 0)  # type: ignore[assignment]

    result = ContainerResult(session_id="s1", user_message="do the task")
    outcome = container._route_pm_classification(
        _dev_classification("implement the feature"),
        pm_buf="[/dev] implement the feature",
        turn_index=0,
        result=result,
    )

    # The turn completed and the loop continues (dev turns do not break).
    assert outcome.should_break is False
    # HeadlessRoleDriver was actually constructed + used (not the PTY builder).
    assert isinstance(container._dev_role_driver, HeadlessRoleDriver)
    assert len(calls) == 1
    # Accounting is metered → lands in metered_* on the AgentSession, never total_*.
    assert calls[0]["metered"] is True
    assert calls[0]["role"] == "dev"
    assert result.total_dev_pty_bytes == 0  # no PTY bytes on the headless leg
    # First-turn persona priming applied via the slash path (default).
    assert calls[0]["message"].startswith("/granite:prime-dev-role")
    # Lifecycle fields set through the shared post-turn path.
    assert container._last_dev_report == "Dev did the work."
    assert len(result.turns) == 1
    assert result.turns[0].classification == "dev"
    assert result.turns[0].dev_idle_marker == "hook_edge"
    # Dev's verbatim text was relayed to PM with the per-turn contract reminder.
    pm.write.assert_called_once_with("Dev did the work." + PM_TURN_CONTRACT_REMINDER)


def test_headless_driver_reused_across_turns_for_resume(tmp_path):
    """The driver is constructed once and reused so --resume + first-turn-only
    priming hold: turn 2 sends the bare message (no prime prefix)."""
    calls: list[dict] = []
    harness = _make_recording_harness(reply="ok", record=calls)
    container, _, _ = _make_headless_dev_container(tmp_path, harness)
    container._spawn_pair()
    container._cycle_idle = lambda pty: (True, "", "", 0)  # type: ignore[assignment]

    result = ContainerResult(session_id="s2", user_message="do the task")
    container._route_pm_classification(_dev_classification("first task"), "b", 0, result)
    driver_after_turn1 = container._dev_role_driver
    container._route_pm_classification(_dev_classification("second task"), "b", 1, result)

    # Same driver object across turns (captured claude_session_id enables resume).
    assert container._dev_role_driver is driver_after_turn1
    assert calls[0]["message"].startswith("/granite:prime-dev-role")  # primed once
    assert calls[1]["message"] == "second task"  # no re-prime on turn 2


def test_headless_empty_reply_falls_back_to_placeholder(tmp_path):
    """An empty headless reply takes the DEV_REPORT_UNAVAILABLE fallback (mirrors
    the PTY empty-transcript path) rather than breaking the loop."""
    harness = _make_recording_harness(reply="")
    container, pm, _ = _make_headless_dev_container(tmp_path, harness)
    container._spawn_pair()
    container._cycle_idle = lambda pty: (True, "", "", 0)  # type: ignore[assignment]

    result = ContainerResult(session_id="s3", user_message="do the task")
    outcome = container._route_pm_classification(_dev_classification("do it"), "b", 0, result)
    assert outcome.should_break is False
    assert result.transcript_fallback_count == 1
    assert container._last_dev_report == DEV_REPORT_UNAVAILABLE
    pm.write.assert_called_once_with(DEV_REPORT_UNAVAILABLE + PM_TURN_CONTRACT_REMINDER)


def test_headless_hung_turn_breaks_with_dev_hang(tmp_path):
    """A hung headless subprocess (bounded-wait timeout) breaks the loop with a
    dev_hang exit_reason, mirroring the PTY builder.last_hung branch."""
    import asyncio

    async def _never(message, working_dir, **kwargs):
        await asyncio.sleep(10)
        return "never"

    container, _, _ = _make_headless_dev_container(tmp_path, _never)
    container._spawn_pair()
    # Shrink the driver's turn timeout so the test is fast.
    container._get_dev_headless_driver().turn_timeout_s = 0.2

    result = ContainerResult(session_id="s4", user_message="do the task")
    outcome = container._route_pm_classification(_dev_classification("do it"), "b", 0, result)
    assert outcome.should_break is True
    assert outcome.exit_reason == "dev_hang"
