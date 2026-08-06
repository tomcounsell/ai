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
        assert "recycled" in line, (
            "a recorded create_time that disagrees with the live one is a "
            "PROVEN recycle, and must be labelled as such — this is the case "
            "that argues FOR Phase B"
        )
        assert "unfenced-legacy" not in line, (
            "a fenced row must never be labelled as an unfenced legacy row"
        )
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
        assert "dead-or-unreadable" in shadow[0], (
            "a recorded fence whose live create_time cannot be read is neither "
            "a proven recycle nor an unfenced row — label it for what it is"
        )

    def test_legacy_row_with_no_recorded_create_time_is_a_shadow_candidate(self, caplog):
        """A pid with no recorded identity cannot be vouched for.

        The label is the point (#2518 review nit 1). ``fence_is_live`` returns
        the same ``False`` here as for a proven recycle, but the two argue in
        OPPOSITE directions for the human authorizing Phase B: withdrawing a
        reprieve on a recycled pid is the fix, withdrawing one on a row whose
        identity was never recorded is unknown authorizing a kill at a
        kill-increasing site. The reader must not have to infer that from
        ``recorded_ct=None``.
        """
        entry = _entry(create_time=None)
        result, shadow = _run(
            entry, verdict="progressing", gate="cpu", live_ct=_RECORDED_CT, caplog=caplog
        )

        assert result == "cpu"  # PHASE A: unchanged
        assert len(shadow) == 1
        line = shadow[0]
        assert "recorded_ct=None" in line
        assert "unfenced-legacy" in line, (
            "an unfenced legacy row must be labelled distinctly — calling it "
            "'recycled' would tell the Phase B reviewer the opposite of the truth"
        )
        assert "exec_pid=4242 recycled" not in line, (
            "the pre-#2518 wording labelled every mismatch 'recycled'; a legacy "
            "row must never carry that label again"
        )

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
            patch("agent.pid_fence.proc_create_time", return_value=_RECORDED_CT + 5000.0),
            patch.object(
                session_health, "subprocess_hang_verdict", return_value=("progressing", "cpu")
            ),
            patch.object(
                session_health.logger, "warning", side_effect=RuntimeError("logging is down")
            ),
        ):
            # Must not raise, and must return the same value as always.
            assert session_health._tier2_reprieve_signal(None, entry) == "cpu"

    def test_the_decision_and_the_label_share_one_create_time_read(self, caplog):
        """One sample of the process, not two (#2518 review nit 1).

        The mismatch that decides whether to log at all and the ``{reason}``
        that labels the line must come from the same reading. Two reads,
        separated by ``subprocess_hang_verdict`` (which samples CPU, so the
        window is not negligible), let the label describe a LATER state than
        the decision acted on — and the mislabelling is one-directional: a
        decision-time ``None`` followed by a live reading of a reassigned pid
        prints ``recycled``, the log's strongest pro-enforcement label, for a
        fence that was merely unreadable.
        """
        entry = _entry()
        reads: list[int | None] = []

        def _counting_read(pid):
            reads.append(pid)
            return None  # decision-time: unreadable

        with (
            patch("agent.pid_fence.proc_create_time", side_effect=_counting_read),
            patch.object(
                session_health, "subprocess_hang_verdict", return_value=("progressing", "cpu")
            ),
            caplog.at_level(logging.WARNING, logger="agent.session_health"),
        ):
            assert session_health._tier2_reprieve_signal(None, entry) == "cpu"

        assert reads == [4242], (
            f"the live create_time must be read exactly once per evaluation, got {reads}"
        )
        shadow = [r.getMessage() for r in caplog.records if _SHADOW_MARKER in r.getMessage()]
        assert len(shadow) == 1
        assert "dead-or-unreadable" in shadow[0], (
            "the label must report what the DECISION saw (an unreadable live "
            "create_time), never a second, later reading"
        )
        assert "recycled" not in shadow[0]

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


class TestShadowMismatchReason:
    """The label is evidence, not decoration (#2518 review nit 1).

    The fence answers ``False`` for a recycled pid, a dead pid, and an unfenced
    legacy row alike. The Phase B authorization decision needs those separated:
    a proven recycle is the forever-reprieve the fence exists to withdraw,
    while an unfenced row is unknown, and unknown must not authorize a kill at
    a kill-increasing site.

    Three labels, and the caller's precondition is part of the contract: the
    reason is derived from the SAME ``live_ct`` that produced the mismatch, so
    a pair reaching ``recycled`` has already failed ``create_times_match`` on
    those exact values.
    """

    def test_no_recorded_create_time_is_unfenced_legacy(self):
        assert session_health._shadow_mismatch_reason(None, _RECORDED_CT) == "unfenced-legacy"

    def test_unfenced_row_with_no_live_reading_is_still_unfenced_legacy(self):
        """Absent recorded identity dominates: nothing about this pid is known."""
        assert session_health._shadow_mismatch_reason(None, None) == "unfenced-legacy"

    def test_unreadable_live_create_time_is_dead_or_unreadable(self):
        assert session_health._shadow_mismatch_reason(_RECORDED_CT, None) == "dead-or-unreadable"

    def test_differing_create_times_are_a_proven_recycle(self):
        assert session_health._shadow_mismatch_reason(_RECORDED_CT, _RECORDED_CT + 5000.0) == (
            "recycled"
        )

    def test_an_agreeing_reading_is_unreachable_not_a_label(self):
        """There is no fourth "matched on re-read" label, by construction.

        It only existed because the log re-read ``create_time`` after the
        decision had already frozen the mismatch. Such a line was never noise
        to discount: ``create_time`` is immutable per process, so agreement on
        a second read means the pid IS the recorded process and IS alive, i.e.
        the gating predicate returned a false negative and Phase B would kill a
        live, owned, actively-progressing session. The single read removes the
        way to observe it, so the label is gone rather than reworded.
        """
        import inspect

        src = inspect.getsource(session_health)
        assert "matched-on-reread" not in src, (
            "one create_time read means an agreeing reading cannot occur — the "
            "label must not come back, in code or in prose"
        )
        assert "proc_create_time" not in inspect.getsource(
            session_health._log_shadow_reprieve_withdrawal
        ), "the logger must never sample the process itself; live_ct is passed in"

    def test_tolerance_is_shared_with_the_fence(self, caplog):
        """One definition of "same process", not a second one grown here.

        Asserted end to end, because the tolerance compare now lives at the
        decision site (``create_times_match``) where it also decides whether a
        line is emitted at all.
        """
        from agent.pid_fence import CREATE_TIME_TOLERANCE_S

        within = _RECORDED_CT + CREATE_TIME_TOLERANCE_S / 2
        beyond = _RECORDED_CT + CREATE_TIME_TOLERANCE_S * 10

        _, shadow_within = _run(
            _entry(), verdict="progressing", gate="cpu", live_ct=within, caplog=caplog
        )
        assert shadow_within == [], "within tolerance is the SAME process — not a withdrawal"

        caplog.clear()
        _, shadow_beyond = _run(
            _entry(), verdict="progressing", gate="cpu", live_ct=beyond, caplog=caplog
        )
        assert len(shadow_beyond) == 1
        assert "recycled" in shadow_beyond[0]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
