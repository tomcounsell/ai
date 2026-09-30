"""``_tier2_reprieve_signal``'s fence drives the reprieve decision (#2518).

The Tier-2 reprieve gates probe ``exec_pid`` from the session's fenced execution
record. That pid is honored only while the fence still vouches for it, and the
guard is deliberately asymmetric because this site *increases* kills:

  * **positive "not ours"** — a ``create_time`` was recorded and no longer
    verifies (the pid was recycled to an unrelated process, or the live reading
    fails because the process is gone) → the pid is treated as absent, the
    verdict becomes ``"unknown"``, and every psutil-backed reprieve is withdrawn;
  * **unknown** — no ``create_time`` was ever recorded (a pre-fence row) → the
    reprieve stands. Per the canonical legacy-row rule in ``agent/pid_fence.py``,
    unknown must never authorize more force than the site applied before the
    fence existed.

Two reprieve-granting return points depend on this:

  * ``verdict == "progressing"`` → ``return gate``
  * the ``verdict == "unknown"`` fall-through → ``return "alive"``, guarded by
    ``pid is not None``. That predicate is fence-driven, not a "did this session
    ever spawn" test: #2494 stopped clearing the fence on exit, so a raw
    ``exec_pid`` alone discriminates nothing.

Seams. ``agent.pid_fence.proc_create_time`` drives the real fence — the seam
spike-4 established as the one that reaches production. ``subprocess_hang_verdict``
is substituted to pin a verdict deterministically: it probes a real process tree
(CPU deltas, live children, established API sockets), which cannot be steered
from a test without a real hung subprocess, and process-timing tests are
parallel-hostile under ``-n auto --dist=loadfile``. The substitute reproduces the
one behavior this file's contract rests on — ``pid is None`` yields
``("unknown", None)`` (``agent/session_runner/liveness.py``) — so a nulled pid
travels the same path it does in production.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import session_health

_RECORDED_CT = 1000.0


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


def _run(entry, *, verdict, gate, live_ct):
    """Evaluate the reprieve once with a pinned probe verdict.

    The probe substitute honors the real ``subprocess_hang_verdict`` contract for
    a missing pid, so nulling the pid at the fence produces ``"unknown"`` here
    exactly as it does in production.
    """

    def _verdict(pid, _session_key, *, caller=""):
        if pid is None:
            return ("unknown", None)
        return (verdict, gate)

    with (
        patch("agent.pid_fence.proc_create_time", return_value=live_ct),
        patch.object(session_health, "subprocess_hang_verdict", side_effect=_verdict),
    ):
        return session_health._tier2_reprieve_signal(None, entry)


class TestProgressingReprieve:
    """Return point 1: ``verdict == "progressing"`` → ``return gate``."""

    def test_matching_fence_grants_the_reprieve(self):
        assert _run(_entry(), verdict="progressing", gate="cpu", live_ct=_RECORDED_CT) == "cpu"

    def test_recycled_fence_withdraws_the_reprieve(self):
        """Alive pid, different process. The forever-reprieve the fence closes."""
        result = _run(
            _entry(),
            verdict="progressing",
            gate="children",
            live_ct=_RECORDED_CT + 5000.0,
        )
        assert result is None, (
            "a recorded create_time that disagrees with the live one is a PROVEN "
            "recycle — the probe was reading an unrelated process, so its "
            "'progressing' verdict must not reprieve this session"
        )

    def test_unreadable_live_create_time_withdraws_the_reprieve(self):
        """Recorded fence, no live reading: the recorded process is gone.

        ``proc_create_time`` returns ``None`` for a dead pid and for one whose
        identity cannot be read (``AccessDenied``, psutil missing). Both are a
        positive answer that the pid is not the process we recorded, so the
        reprieve is withdrawn. The row that keeps its reprieve is the one that
        never recorded an identity at all — see
        :meth:`TestLegacyRowKeepsItsReprieve`.
        """
        assert _run(_entry(), verdict="progressing", gate="api", live_ct=None) is None

    def test_no_fence_pid_grants_no_reprieve(self):
        """No pid recorded at all → nothing to probe, unchanged by the fence."""
        assert _run(_entry(pid=None), verdict="unknown", gate=None, live_ct=None) is None


class TestLegacyRowKeepsItsReprieve:
    """Unknown never authorizes a kill at a kill-increasing site.

    ``fence_is_live`` answers the same ``False`` for a pre-fence row as for a
    proven recycle, so the legacy fallback is spelled out at the call site. A row
    whose identity was never recorded keeps today's behavior until it ages out
    with session TTL; withdrawing there would be unknown escalating force, which
    the canonical rule in ``agent/pid_fence.py`` forbids.
    """

    def test_progressing_reprieve_survives_an_unfenced_row(self):
        result = _run(
            _entry(create_time=None), verdict="progressing", gate="cpu", live_ct=_RECORDED_CT
        )
        assert result == "cpu"

    def test_alive_fall_through_survives_an_unfenced_row(self):
        result = _run(_entry(create_time=None), verdict="unknown", gate=None, live_ct=None)
        assert result == "alive"


class TestAliveFallThrough:
    """Return point 2: the ``verdict == "unknown"`` fall-through → ``return "alive"``.

    ``pid is not None`` is fence-driven here. Since #2494 stopped clearing the
    fence, a raw ``exec_pid`` stays populated for the life of the row, so on its
    own the predicate is permanently true for any session that ever spawned. What
    makes it discriminate is the fence nulling a pid that is no longer ours.
    """

    def test_matching_fence_returns_alive(self):
        assert _run(_entry(), verdict="unknown", gate=None, live_ct=_RECORDED_CT) == "alive"

    def test_recycled_fence_withdraws_the_alive_reprieve(self):
        result = _run(_entry(), verdict="unknown", gate=None, live_ct=_RECORDED_CT + 5000.0)
        assert result is None, (
            "a dead session whose recycled pid probes as alive was reprieved "
            "every tick, indefinitely — the fence is what ends that"
        )

    def test_count_guard_fires_before_the_alive_fall_through(self):
        """The escalation guard (#1226) still wins on a fence that MATCHES.

        A never-started session past ``MAX_NO_OUTPUT_REPRIEVES`` is recovered on
        the count alone, so the fence never gets to vouch for it.
        """
        entry = _entry(
            sdk_ever_output=False,
            reprieve_count=session_health.MAX_NO_OUTPUT_REPRIEVES + 1,
        )
        assert _run(entry, verdict="unknown", gate=None, live_ct=_RECORDED_CT) is None


class TestGatesUnaffectedByTheFence:
    def test_hung_never_started_returns_none(self):
        entry = _entry(sdk_ever_output=False)
        assert _run(entry, verdict="hung", gate="gone", live_ct=_RECORDED_CT) is None

    def test_compacting_gate_short_circuits_before_the_fence(self):
        """The compaction reprieve is evaluated first and never reads the fence."""
        import time

        entry = _entry()
        entry.last_compaction_ts = time.time()
        reads: list[int | None] = []

        def _counting_read(pid):
            reads.append(pid)
            return _RECORDED_CT + 5000.0  # a recycled fence, were it consulted

        with (
            patch("agent.pid_fence.proc_create_time", side_effect=_counting_read),
            patch.object(
                session_health, "subprocess_hang_verdict", return_value=("progressing", "children")
            ),
        ):
            assert session_health._tier2_reprieve_signal(None, entry) == "compacting"

        assert reads == [], "the compaction reprieve must not depend on the subprocess fence"


class TestFenceContract:
    def test_the_fence_is_read_once_per_evaluation(self):
        """One sample of the process identity, shared by every gate below it."""
        entry = _entry()
        reads: list[int | None] = []

        def _counting_read(pid):
            reads.append(pid)
            return _RECORDED_CT

        with (
            patch("agent.pid_fence.proc_create_time", side_effect=_counting_read),
            patch.object(
                session_health, "subprocess_hang_verdict", return_value=("progressing", "cpu")
            ),
        ):
            assert session_health._tier2_reprieve_signal(None, entry) == "cpu"

        assert reads == [4242], (
            f"the live create_time must be read exactly once per evaluation, got {reads}"
        )

    @pytest.mark.parametrize(
        ("offset", "expected"),
        [
            (0.5, "cpu"),  # within tolerance — the SAME process
            (10.0, None),  # beyond tolerance — a different process
        ],
    )
    def test_tolerance_is_shared_with_the_fence(self, offset, expected):
        """One definition of "same process", not a second one grown here."""
        from agent.pid_fence import CREATE_TIME_TOLERANCE_S

        live_ct = _RECORDED_CT + CREATE_TIME_TOLERANCE_S * offset
        assert _run(_entry(), verdict="progressing", gate="cpu", live_ct=live_ct) == expected

    def test_the_switch_is_git_not_a_config_flag(self):
        """No toggle, and no residue of a log-only rollout, in the live module.

        A flag would rot into a permanent fork in the logic, and a leftover
        shadow branch would leave two answers in the code for one decision.
        """
        import inspect

        src = inspect.getsource(session_health)
        for forbidden in (
            "PHASE A",
            "fence-shadow",
            "ENFORCE_FENCE",
            "FENCE_SHADOW",
            "fence_enabled",
        ):
            assert forbidden not in src, (
                f"{forbidden} survives in agent/session_health.py — the reprieve "
                "fence enforces unconditionally and has no second mode"
            )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
