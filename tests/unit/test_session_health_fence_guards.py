"""Fence guards that ship ENFORCING, unshadowed (#2518, Task 2).

Two sites null a recycled fenced pid before probing it, and both are strictly
kill-REDUCING — the opposite direction from ``_tier2_reprieve_signal``, which is
why neither gets a Phase A shadow and neither can ever emit a shadow-log line:

* ``_has_progress`` (``agent/session_health.py``)
* ``_owned_task_hang_check`` (``agent/agent_session_queue.py``)

**The ``_has_progress`` direction is the load-bearing thing this file pins.**
An earlier reading of the defect had it backwards — it claimed fencing this site
closes a "false progressing blocks recovery indefinitely" gap. It cannot.
``if _verdict != "hung":`` treats ``"unknown"`` and ``"progressing"``
IDENTICALLY: both honor the sticky fields and return True. So nulling a recycled
pid moves the verdict from whatever it was to ``"unknown"``, which can only ever
WITHHOLD a hang verdict, never produce one.

The gap it actually closes is the reverse: an unrelated process occupying a
recycled ``exec_pid`` and probing as ``"hung"`` bypasses the sticky-field honor
and prematurely releases a session with REAL progress to Tier-2 recovery.

Both directions are asserted below so a later refactor cannot quietly restore
the premature release, and cannot quietly widen the change into a kill-increasing
one either.

Seams. ``agent.pid_fence.proc_create_time`` drives the real fence.
``subprocess_hang_verdict`` is substituted to pin a verdict: it probes a real
process tree (CPU deltas, live children, established API sockets), which cannot
be steered from a test without a genuinely hung subprocess, and process-timing
tests are parallel-hostile under ``-n auto --dist=loadfile``. The substitute
RECORDS the pid it was handed, which is the assertion that actually proves the
fence nulled it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import session_health

_RECORDED_CT = 1000.0


def _entry(*, pid=4242, create_time=_RECORDED_CT, turn_count=3, log_path="", uuid=None):
    """A session on the own-progress leg of ``_has_progress``.

    Reaching that leg needs: ``sdk_ever_output`` False (no ``last_*_at``
    fields), a heartbeat OLDER than ``HEARTBEAT_FRESHNESS_WINDOW`` (90s) so
    sub-check B's fast path does not short-circuit, but YOUNGER than
    ``NO_OUTPUT_BUDGET_SECONDS`` (1800s) so the own-progress fields are honored
    at all.
    """
    return SimpleNamespace(
        agent_session_id="test-has-progress-fence",
        id="test-has-progress-fence",
        session_id="test-has-progress-fence",
        project_key="test-has-progress-fence",
        last_tool_use_at=None,
        last_turn_at=None,
        last_stdout_at=None,
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=600),
        started_at=datetime.now(UTC) - timedelta(seconds=600),
        created_at=datetime.now(UTC) - timedelta(seconds=600),
        turn_count=turn_count,
        log_path=log_path,
        claude_session_uuid=uuid,
        live_fence=({"pid": pid, "create_time": create_time} if pid is not None else None),
        get_children=lambda: [],
    )


def _run_has_progress(entry, *, live_ct, verdict):
    """Evaluate ``_has_progress`` once; return ``(result, pid_the_prober_saw)``."""
    seen: dict = {}

    def _verdict(pid, session_key, *, caller=""):
        seen["pid"] = pid
        seen["caller"] = caller
        # An unrelated occupant of a recycled pid probes as whatever the caller
        # scripted; ``None`` is what the real prober returns for a null pid.
        return ("unknown", None) if pid is None else (verdict, "dead")

    with (
        patch("agent.pid_fence.proc_create_time", return_value=live_ct),
        patch.object(session_health, "subprocess_hang_verdict", side_effect=_verdict),
    ):
        result = session_health._has_progress(entry)

    return result, seen


class TestHasProgressFenceDirection:
    """The critique-BLOCKER regression: fencing here is strictly kill-REDUCING."""

    def test_recycled_pid_probing_as_hung_still_honors_real_progress(self):
        """The defect this closes: a premature Tier-2 release.

        The session has REAL progress (``turn_count > 0``). Its recorded
        ``exec_pid`` has been recycled onto an unrelated process which, probed,
        would report ``"hung"``. Before the fence, that verdict bypassed the
        sticky-field honor and released a progressing session to recovery.
        """
        entry = _entry(turn_count=3)

        result, seen = _run_has_progress(
            entry,
            live_ct=_RECORDED_CT + 5000.0,  # alive pid, DIFFERENT process
            verdict="hung",
        )

        assert seen["pid"] is None, (
            "the recycled pid must be nulled BEFORE the probe — this is the "
            "assertion that proves the guard ran, not just that the outcome "
            "happened to be True"
        )
        assert result is True, (
            "a session with real progress must not be released to Tier-2 "
            "recovery because an unrelated process occupying its old pid looks "
            "hung"
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"turn_count": 3, "log_path": "", "uuid": None},
            {"turn_count": 0, "log_path": "/tmp/session.log", "uuid": None},
            {"turn_count": 0, "log_path": "", "uuid": "claude-uuid-1"},
        ],
        ids=["turn_count", "log_path", "claude_session_uuid"],
    )
    def test_every_sticky_field_survives_a_recycled_hung_probe(self, kwargs):
        entry = _entry(**kwargs)
        result, seen = _run_has_progress(entry, live_ct=_RECORDED_CT + 5000.0, verdict="hung")
        assert seen["pid"] is None
        assert result is True

    def test_unrecycled_pid_probing_as_hung_still_bypasses_the_sticky_fields(self):
        """The converse: the guard must not become a blanket suppression.

        A session whose OWN subprocess is genuinely hung is still released to
        Tier-2 recovery. Fencing narrows the hang verdict to processes we can
        prove are ours; it does not withdraw the verdict itself.
        """
        entry = _entry(turn_count=3)

        result, seen = _run_has_progress(
            entry,
            live_ct=_RECORDED_CT,  # fence MATCHES — this really is our process
            verdict="hung",
        )

        assert seen["pid"] == 4242, "a fence-verified pid must reach the prober"
        assert result is False, (
            "a confirmed-hung OWN subprocess must still bypass the sticky "
            "fields and fall through to recovery"
        )

    def test_matching_fence_probing_as_progressing_honors_the_sticky_fields(self):
        entry = _entry(turn_count=3)
        result, seen = _run_has_progress(entry, live_ct=_RECORDED_CT, verdict="progressing")
        assert seen["pid"] == 4242
        assert result is True

    def test_unknown_and_progressing_are_the_same_outcome_at_this_branch(self):
        """Why this site needs no Phase A shadow, asserted rather than asserted-in-prose.

        ``if _verdict != "hung":`` cannot distinguish them, so nulling a pid can
        only ever move an outcome TOWARD honoring progress — never away.
        """
        progressing, _ = _run_has_progress(
            _entry(turn_count=3), live_ct=_RECORDED_CT, verdict="progressing"
        )
        unknown, _ = _run_has_progress(
            _entry(turn_count=3), live_ct=_RECORDED_CT, verdict="unknown"
        )
        assert progressing is unknown is True

    def test_dead_pid_is_nulled_and_progress_is_honored(self):
        entry = _entry(turn_count=3)
        result, seen = _run_has_progress(entry, live_ct=None, verdict="hung")
        assert seen["pid"] is None
        assert result is True

    def test_legacy_row_with_no_recorded_create_time_is_nulled(self):
        """Unknown identity never authorizes a hang verdict either."""
        entry = _entry(create_time=None)
        result, seen = _run_has_progress(entry, live_ct=_RECORDED_CT, verdict="hung")
        assert seen["pid"] is None
        assert result is True

    def test_no_sticky_progress_still_falls_through_to_the_child_check(self):
        """The guard must not manufacture progress that was never there."""
        entry = _entry(turn_count=0, log_path="", uuid=None)
        result, seen = _run_has_progress(entry, live_ct=_RECORDED_CT + 5000.0, verdict="hung")
        assert seen["pid"] is None
        assert result is False, "no sticky evidence and no live children → no progress"

    def test_probe_uses_the_has_progress_caller_key(self):
        """The CPU flat-count stays independent of the Tier-2 prober's."""
        _, seen = _run_has_progress(_entry(), live_ct=_RECORDED_CT, verdict="progressing")
        assert seen["caller"] == "has_progress"


class TestOwnedTaskHangCheckFence:
    """``_owned_task_hang_check`` nulls a non-matching pid before the probe.

    Same direction as ``_has_progress``: routing to ``("unknown", None)`` yields
    ``(False, None)`` — no hang — so the change is strictly kill-reducing.
    """

    def _entry(self, *, pid=4242, create_time=_RECORDED_CT):
        return SimpleNamespace(
            agent_session_id="test-owned-hang",
            id="test-owned-hang",
            session_id="test-owned-hang",
            last_tool_use_at=None,
            last_turn_at=None,
            last_stdout_at=None,
            live_fence=({"pid": pid, "create_time": create_time} if pid is not None else None),
        )

    def _run(self, entry, *, live_ct, verdict):
        from agent import agent_session_queue

        seen: dict = {}

        def _verdict(pid, session_key, *, caller=""):
            seen["pid"] = pid
            return ("unknown", None) if pid is None else (verdict, "dead")

        with (
            patch("agent.pid_fence.proc_create_time", return_value=live_ct),
            patch.object(agent_session_queue, "subprocess_hang_verdict", side_effect=_verdict),
        ):
            result = agent_session_queue._owned_task_hang_check(
                entry, {}, "test-owned-hang", caller="test"
            )
        return result, seen

    def test_recycled_pid_never_drives_a_hang_decision(self):
        result, _ = self._run(self._entry(), live_ct=_RECORDED_CT + 5000.0, verdict="hung")
        assert result == (False, None), (
            "an unrelated process occupying a recycled exec_pid must not drive "
            "a hang decision for a session it has nothing to do with"
        )

    def test_recycled_pid_is_nulled_before_the_probe(self):
        _, seen = self._run(self._entry(), live_ct=_RECORDED_CT + 5000.0, verdict="hung")
        assert seen["pid"] is None

    def test_matching_fence_still_reports_a_hang(self):
        result, seen = self._run(self._entry(), live_ct=_RECORDED_CT, verdict="hung")
        assert seen["pid"] == 4242
        assert result == (True, "dead")

    def test_dead_pid_is_nulled(self):
        result, seen = self._run(self._entry(), live_ct=None, verdict="hung")
        assert seen["pid"] is None
        assert result == (False, None)

    def test_legacy_row_is_nulled(self):
        result, seen = self._run(
            self._entry(create_time=None), live_ct=_RECORDED_CT, verdict="hung"
        )
        assert seen["pid"] is None
        assert result == (False, None)

    def test_sdk_output_short_circuits_before_the_fence(self):
        """The evidence-only PRE-first-output probe does not apply post-output."""
        from agent import agent_session_queue

        entry = self._entry()
        entry.last_turn_at = "2026-08-04T00:00:00Z"

        with patch.object(agent_session_queue, "subprocess_hang_verdict") as probe:
            result = agent_session_queue._owned_task_hang_check(
                entry, {}, "test-owned-hang", caller="test"
            )

        assert result == (False, None)
        probe.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
