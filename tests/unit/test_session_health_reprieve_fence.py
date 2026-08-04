"""Phase A shadow tests for ``_tier2_reprieve_signal``'s fence (#2518, Task 2).

**These are SHADOW assertions, not behavior assertions, and that is deliberate.**

Fencing this site is strictly kill-INCREASING: it withdraws reprieves the
current code grants, and a wrongly-killed live session is a visible failure. So
the user directed a two-phase rollout. Phase A evaluates the fence and LOGS what
it would withdraw while every return value stays byte-identical to pre-#2518.
Phase B (plan Task 13) deletes the ``# PHASE A — DELETE IN PHASE B`` block and
lets the fence drive the return value — but only after a human reviews the
shadow log from the canary machine.

So each test below asserts a PAIR:

  1. the return value is UNCHANGED (a reprieve is still granted), and
  2. the ``[fence-shadow]`` WARNING did / did not fire.

Task 13 flips (1) from "unchanged" to "withdrawn" and deletes the shadow
assertions in (2). The tests are written so that flip is a small, obvious edit:
each Phase A expectation is stated on its own line with a ``PHASE A``/``PHASE B``
comment naming what it becomes.

**Two reprieve-granting return points exist** and both must shadow-log:

  * ``verdict == "progressing"`` → ``return gate``
  * the fall-through → ``return "alive"`` (guarded by ``pid is not None``,
    which is permanently true for any session that ever spawned since #2494
    stopped clearing the fence, so it no longer discriminates anything)

Seams. ``agent.pid_fence.proc_create_time`` drives the real fence — the seam
spike-4 established as the one that reaches production. ``subprocess_hang_verdict``
is substituted to pin a verdict deterministically: it probes a real process tree
(CPU deltas, live children, established API sockets), which cannot be steered
from a test without a real hung subprocess, and process-timing tests are
parallel-hostile under ``-n auto --dist=loadfile``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import session_health

_RECORDED_CT = 1000.0
_SHADOW_MARKER = "[fence-shadow] would withdraw reprieve"


def _entry(*, pid=4242, create_time=_RECORDED_CT, sdk_ever_output=True, reprieve_count=0):
    """A session eligible for the Tier-2 reprieve gates.

    ``sdk_ever_output=True`` by default so the never-started hard ceiling and
    the count-based escalation guard stay out of the way — this file is about
    the fence, not about those gates.
    """
    return SimpleNamespace(
        agent_session_id="test-reprieve-fence",
        id="test-reprieve-fence",
        session_id="test-reprieve-fence",
        project_key="test-reprieve-fence",
        last_compaction_ts=None,
        reprieve_count=reprieve_count,
        last_turn_at="2026-08-04T00:00:00Z" if sdk_ever_output else None,
        last_tool_use_at=None,
        last_stdout_at=None,
        turn_count=1,
        log_path="/tmp/x.log",
        claude_session_uuid="uuid-1",
        started_at=None,
        created_at=None,
        live_fence=({"pid": pid, "create_time": create_time} if pid is not None else None),
    )


def _run(entry, *, verdict, gate, live_ct, caplog):
    """Evaluate the reprieve once and return ``(result, shadow_lines)``."""
    with (
        patch("agent.pid_fence.proc_create_time", return_value=live_ct),
        patch.object(session_health, "subprocess_hang_verdict", return_value=(verdict, gate)),
        caplog.at_level(logging.WARNING, logger="agent.session_health"),
    ):
        result = session_health._tier2_reprieve_signal(None, entry)

    shadow = [r.getMessage() for r in caplog.records if _SHADOW_MARKER in r.getMessage()]
    return result, shadow


class TestProgressingReprieveShadow:
    """Return point 1: ``verdict == "progressing"`` → ``return gate``."""

    def test_recycled_fence_still_grants_the_reprieve_and_logs_the_shadow(self, caplog):
        entry = _entry()
        result, shadow = _run(
            entry,
            verdict="progressing",
            gate="children",
            live_ct=_RECORDED_CT + 5000.0,  # alive pid, different process
            caplog=caplog,
        )

        # PHASE A: behavior is identical to pre-#2518.
        # PHASE B (Task 13): this becomes ``assert result is None``.
        assert result == "children", (
            "Phase A must NOT change behavior — the reprieve is still granted. "
            "Only the log records what Phase B would do."
        )

        # …and the shadow records exactly what Phase B would withdraw.
        # PHASE B (Task 13): delete this block.
        assert len(shadow) == 1, f"expected one shadow line, got {shadow}"
        line = shadow[0]
        assert "test-reprieve-fence" in line, "the shadow must name the session"
        assert "exec_pid=4242" in line, "the shadow must name the recycled pid"
        assert f"recorded_ct={_RECORDED_CT}" in line, "the shadow must record the fence"
        assert f"live_ct={_RECORDED_CT + 5000.0}" in line, (
            "the shadow must record the LIVE create_time too — the pair is the "
            "evidence a human reviews to decide whether the pid was genuinely "
            "recycled"
        )
        assert "granted_gate=children" in line, (
            "the shadow must name the gate that WAS granted, so the reviewer "
            "sees which reprieve Phase B removes"
        )

    def test_matching_fence_grants_the_reprieve_with_no_shadow_line(self, caplog):
        entry = _entry()
        result, shadow = _run(
            entry,
            verdict="progressing",
            gate="cpu",
            live_ct=_RECORDED_CT,
            caplog=caplog,
        )

        assert result == "cpu"
        assert shadow == [], (
            "A fence that still matches is not a withdrawal candidate. Every "
            "line in this log must be a behavior change Phase B would make, or "
            "the canary review is noise."
        )

    def test_dead_pid_is_a_shadow_candidate(self, caplog):
        """``proc_create_time`` is None → unknown → not ours → would withdraw."""
        entry = _entry()
        result, shadow = _run(entry, verdict="progressing", gate="api", live_ct=None, caplog=caplog)

        assert result == "api"  # PHASE A: unchanged
        assert len(shadow) == 1

    def test_legacy_row_with_no_recorded_create_time_is_a_shadow_candidate(self, caplog):
        """A pid with no recorded identity cannot be vouched for."""
        entry = _entry(create_time=None)
        result, shadow = _run(
            entry, verdict="progressing", gate="cpu", live_ct=_RECORDED_CT, caplog=caplog
        )

        assert result == "cpu"  # PHASE A: unchanged
        assert len(shadow) == 1
        assert "recorded_ct=None" in shadow[0]

    def test_no_fence_pid_grants_no_reprieve_and_logs_nothing(self, caplog):
        """No pid at all → the verdict is "unknown" territory, not a fence miss."""
        entry = _entry(pid=None)
        result, shadow = _run(entry, verdict="unknown", gate=None, live_ct=None, caplog=caplog)

        assert result is None
        assert shadow == []


class TestAliveFallThroughShadow:
    """Return point 2: the ``verdict == "unknown"`` fall-through → ``return "alive"``.

    This is the predicate the plan calls out as why the site is HIGH: since
    #2494 stopped clearing the fence, ``pid is not None`` is permanently true
    for any session that ever spawned, so a dead session whose recycled pid
    probes as alive is reprieved every tick, indefinitely.
    """

    def test_recycled_fence_still_returns_alive_and_logs_the_shadow(self, caplog):
        entry = _entry()
        result, shadow = _run(
            entry, verdict="unknown", gate=None, live_ct=_RECORDED_CT + 5000.0, caplog=caplog
        )

        # PHASE A: unchanged.
        # PHASE B (Task 13): this becomes ``assert result is None``.
        assert result == "alive"

        # PHASE B (Task 13): delete this block.
        assert len(shadow) == 1
        assert "granted_gate=alive" in shadow[0]
        assert "exec_pid=4242" in shadow[0]

    def test_matching_fence_returns_alive_with_no_shadow_line(self, caplog):
        entry = _entry()
        result, shadow = _run(
            entry, verdict="unknown", gate=None, live_ct=_RECORDED_CT, caplog=caplog
        )

        assert result == "alive"
        assert shadow == []

    def test_count_guard_still_wins_over_the_fence(self, caplog):
        """The escalation guard fires BEFORE the alive fall-through.

        A never-started session past ``MAX_NO_OUTPUT_REPRIEVES`` is already
        recovered today, so it is not a withdrawal candidate and must not add
        noise to the canary log.
        """
        entry = _entry(
            sdk_ever_output=False,
            reprieve_count=session_health.MAX_NO_OUTPUT_REPRIEVES + 1,
        )
        result, shadow = _run(
            entry, verdict="unknown", gate=None, live_ct=_RECORDED_CT + 5000.0, caplog=caplog
        )

        assert result is None
        assert shadow == [], "an already-recovered session is not a withdrawal candidate"


class TestNonReprieveReturnPointsNeverShadow:
    """Only reprieve-GRANTING return points shadow-log."""

    def test_hung_never_started_returns_none_without_a_shadow(self, caplog):
        entry = _entry(sdk_ever_output=False)
        result, shadow = _run(
            entry, verdict="hung", gate="gone", live_ct=_RECORDED_CT + 5000.0, caplog=caplog
        )

        assert result is None
        assert shadow == []

    def test_compacting_gate_short_circuits_before_the_fence(self, caplog):
        """The compaction reprieve is evaluated first and never reads the fence."""
        import time

        entry = _entry()
        entry.last_compaction_ts = time.time()

        result, shadow = _run(
            entry,
            verdict="progressing",
            gate="children",
            live_ct=_RECORDED_CT + 5000.0,
            caplog=caplog,
        )

        assert result == "compacting"
        assert shadow == []


class TestShadowIsObservationOnly:
    def test_shadow_log_failure_never_breaks_the_decision(self, caplog):
        """An observation must never affect the decision it observes."""
        entry = _entry()

        with (
            patch("agent.pid_fence.proc_create_time", side_effect=[_RECORDED_CT + 5000.0, None]),
            patch.object(
                session_health, "subprocess_hang_verdict", return_value=("progressing", "cpu")
            ),
            patch.object(
                session_health.logger, "warning", side_effect=RuntimeError("logging is down")
            ),
        ):
            # Must not raise, and must return the same value as always.
            assert session_health._tier2_reprieve_signal(None, entry) == "cpu"

    def test_phase_a_block_is_marked_for_deletion(self):
        """The switch is git, not a config flag.

        A flag would rot into a permanent fork in the logic. The marker is what
        Task 13 deletes and what the plan's Verification row greps for.
        """
        import inspect

        src = inspect.getsource(session_health)
        assert "PHASE A" in src and "DELETE IN PHASE B" in src
        # …and no config flag crept in alongside it.
        for forbidden in ("ENFORCE_FENCE", "FENCE_SHADOW", "fence_enabled"):
            assert forbidden not in src, (
                f"{forbidden} would rot into a permanent fork — Phase B must be "
                "a deletion, not a toggle"
            )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
