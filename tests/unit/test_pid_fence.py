"""Unit tests for the ``(pid, create_time)`` execution fence (durability plan #2494).

The fence collapses the former ``claude_pid`` / ``pm_pid`` / ``harness_pid`` trio
into one fenced execution record on ``AgentSession``. It is BEST-EFFORT DETECTION
of PID reuse, not a guarantee — macOS has no pidfd, so an irreducible TOCTOU
window remains (https://lwn.net/Articles/784997/). These tests prove the core
detection property: a recorded fence pid that gets recycled to a *different*
process (different ``create_time``) reads as "not ours", so signals are skipped.
"""

from __future__ import annotations

import logging
import os
import signal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from popoto.exceptions import QueryException
from redis.exceptions import RedisError

from agent.pid_fence import (
    CREATE_TIME_TOLERANCE_S,
    create_times_match,
    fence_is_live,
    proc_create_time,
)


class TestCreateTimesMatch:
    """The tolerance compare, factored out so every consumer shares one rule (#2518)."""

    def test_identical_times_match(self):
        assert create_times_match(1000.0, 1000.0) is True

    def test_sub_tolerance_skew_matches(self):
        assert create_times_match(1000.0, 1000.0 + CREATE_TIME_TOLERANCE_S / 2) is True

    def test_supra_tolerance_skew_does_not_match(self):
        assert create_times_match(1000.0, 1000.0 + CREATE_TIME_TOLERANCE_S * 2) is False

    def test_exact_tolerance_boundary_matches(self):
        # ``<=`` — the boundary itself is inclusive.
        assert create_times_match(1000.0, 1000.0 + CREATE_TIME_TOLERANCE_S) is True

    @pytest.mark.parametrize(
        "recorded,observed",
        [
            (None, 1000.0),  # legacy row: nothing recorded at spawn
            (1000.0, None),  # live process' create_time unreadable now
            (None, None),  # both unknown
        ],
    )
    def test_none_on_either_side_is_unknown_not_a_match(self, recorded, observed):
        """The canonical legacy-row rule: unknown never authorizes a kill.

        ``False`` here must be read by callers as "cannot claim ownership",
        NOT as "this process is dead".
        """
        assert create_times_match(recorded, observed) is False

    @pytest.mark.parametrize(
        "recorded,observed",
        [
            ("not-a-float", 1000.0),
            (1000.0, "not-a-float"),
            ({"pid": 1}, 1000.0),
            (1000.0, object()),
        ],
    )
    def test_never_raises_on_uncoercible_input(self, recorded, observed):
        assert create_times_match(recorded, observed) is False

    def test_nan_never_matches(self):
        """NaN compares False under every ordering — a fence read as NaN is unknown."""
        nan = float("nan")
        assert create_times_match(nan, 1000.0) is False
        assert create_times_match(1000.0, nan) is False
        assert create_times_match(nan, nan) is False


class TestFenceIsLive:
    def test_matching_pid_and_create_time_is_live(self):
        # Our own live process: read its real create_time and match it.
        pid = os.getpid()
        ct = proc_create_time(pid)
        assert ct is not None
        assert fence_is_live(pid, ct) is True

    def test_recycled_pid_reads_as_not_ours(self):
        # SAME live pid, but the recorded create_time is from the process that
        # previously held this pid — the fence must reject it.
        pid = os.getpid()
        real_ct = proc_create_time(pid)
        stale_ct = real_ct - 5000.0  # a process that booted long before us
        assert fence_is_live(pid, stale_ct) is False

    def test_dead_pid_is_not_live(self):
        # A pid that (almost certainly) does not exist.
        assert fence_is_live(2_147_483_646, 123.0) is False

    def test_missing_recorded_fence_is_not_live(self):
        # No recorded create_time → cannot claim ownership → fail safe.
        pid = os.getpid()
        assert fence_is_live(pid, None) is False

    def test_none_pid_is_not_live(self):
        assert fence_is_live(None, 123.0) is False

    def test_tolerance_boundary(self):
        # A sub-tolerance skew still matches; a supra-tolerance one does not.
        fn = lambda _pid: 1000.0  # noqa: E731 — deterministic seam
        assert fence_is_live(42, 1000.0 + CREATE_TIME_TOLERANCE_S / 2, create_time_fn=fn) is True
        assert fence_is_live(42, 1000.0 + CREATE_TIME_TOLERANCE_S * 2, create_time_fn=fn) is False

    def test_never_raises_on_bad_input(self):
        # Garbage recorded create_time coerces to a safe False, never raises.
        assert fence_is_live(42, "not-a-float", create_time_fn=lambda _p: 1.0) is False

    def test_never_raises_on_uncoercible_create_time_types(self):
        """The ``create_time`` type-error path: neither side is a number."""
        assert fence_is_live(42, {"nested": "dict"}, create_time_fn=lambda _p: 1.0) is False
        assert fence_is_live(42, [1, 2, 3], create_time_fn=lambda _p: 1.0) is False
        assert fence_is_live(42, 1.0, create_time_fn=lambda _p: "garbage") is False

    def test_nan_recorded_create_time_is_not_live(self):
        """``fence_is_live(pid, nan)`` reads as "not ours" — NaN is not an identity.

        A NaN can reach the fence from a corrupted hash field. Every NaN
        comparison is False, so the tolerance compare already rejects it; this
        pins that it stays a *silent* False and never an exception or a match.
        """
        pid = os.getpid()
        assert fence_is_live(pid, float("nan")) is False

    def test_proc_create_time_of_dead_pid_is_none(self):
        assert proc_create_time(2_147_483_646) is None

    def test_proc_create_time_of_none_is_none(self):
        assert proc_create_time(None) is None


class TestToleranceConstantPin:
    """Task 7 (#2518): pin ``CREATE_TIME_TOLERANCE_S`` and its error direction.

    The constant is deliberately TIGHTER than psutil's own documented precision
    (0.01s on Linux). That is only safe because of which way the error points,
    so the direction is pinned here alongside the value — a future loosening
    must be a conscious edit to both.

    Two of these use ``create_time_fn``, which is legitimate ONLY because they
    are pure-predicate tolerance-arithmetic tests. Spike-4's rabbit hole applies
    to pid-RECYCLE tests: no production call site threads ``create_time_fn``, so
    driving a recycle scenario through it proves nothing about production. The
    recycle direction is therefore asserted at a real signal site below via
    ``patch("agent.pid_fence.proc_create_time")``, which is the seam production
    actually reads.
    """

    def test_tolerance_value_is_pinned(self):
        assert CREATE_TIME_TOLERANCE_S == 1e-3, (
            "CREATE_TIME_TOLERANCE_S is deliberately tighter than psutil's "
            "documented 0.01s Linux precision because this system is darwin-only. "
            "Changing it is a deliberate decision — see agent/pid_fence.py."
        )

    def test_too_tight_tolerance_yields_not_ours_never_a_false_positive(self):
        """The false-negative direction: a skew beyond tolerance reads "not ours".

        This is the property that makes a tight value safe. A tolerance that is
        too tight can only ever *withhold* an ownership claim; it can never
        assert one for a process that is not ours.
        """
        real_ct = 1000.0
        fn = lambda _pid: real_ct  # noqa: E731 — deterministic seam

        # Our OWN process, misread by a hair beyond the tolerance.
        assert fence_is_live(42, real_ct + CREATE_TIME_TOLERANCE_S * 10, create_time_fn=fn) is False
        # …and the inverse never happens: a genuinely different process' start
        # time is never accepted, no matter how the skew is signed.
        assert fence_is_live(42, real_ct - 5000.0, create_time_fn=fn) is False

    def test_false_negative_skips_the_kill_rather_than_authorizing_one(self):
        """A too-tight verdict at a real signal site withholds the SIGTERM.

        Drives the property through ``_terminate_detached_harness`` — the
        primary SIGTERM site — rather than asserting it about the predicate in
        isolation, so the direction is pinned where it matters.
        """
        from agent import session_health

        kills: list[tuple[int, int]] = []
        entry = SimpleNamespace(
            agent_session_id="dbg-tolerance",
            live_fence={"pid": 4242, "create_time": 200.0 + CREATE_TIME_TOLERANCE_S * 10},
        )
        with (
            patch("agent.pid_fence.proc_create_time", return_value=200.0),
            patch.object(session_health.os, "kill", side_effect=lambda *a: kills.append(a)),
        ):
            session_health._terminate_detached_harness(entry)

        assert kills == [], (
            "A sub-tolerance misread must fail SAFE — withhold the signal, "
            "never redirect it at an unrelated process"
        )


class TestTerminateDetachedHarnessFenceGuard:
    """The primary SIGTERM site (#2148) must skip a recycled pid (Race 3)."""

    def _entry(self, pid, recorded_ct):
        return SimpleNamespace(
            agent_session_id="dbg-fence",
            live_fence={"pid": pid, "create_time": recorded_ct},
        )

    def test_recycled_pid_skips_sigterm(self):
        """Recorded create_time != live create_time → PID recycled → no signal."""
        from agent import session_health

        kills: list[tuple[int, int]] = []
        entry = self._entry(pid=4242, recorded_ct=100.0)

        with (
            # Live process at pid 4242 booted at a DIFFERENT time than recorded.
            patch("agent.pid_fence.proc_create_time", return_value=200.0),
            patch.object(session_health.os, "kill", side_effect=lambda *a: kills.append(a)),
        ):
            session_health._terminate_detached_harness(entry)

        assert kills == [], "A recycled pid must not be SIGTERMd"

    def test_matching_fence_sends_sigterm(self):
        """Live create_time matches the recorded fence → the harness is ours → SIGTERM."""
        from agent import session_health

        kills: list[tuple[int, int]] = []
        entry = self._entry(pid=4242, recorded_ct=200.0)

        with (
            patch("agent.pid_fence.proc_create_time", return_value=200.0),
            patch.object(session_health.os, "kill", side_effect=lambda *a: kills.append(a)),
        ):
            session_health._terminate_detached_harness(entry)

        assert (4242, signal.SIGTERM) in kills

    def test_no_fence_pid_is_noop(self):
        from agent import session_health

        kills: list[tuple[int, int]] = []
        entry = SimpleNamespace(agent_session_id="dbg-fence", live_fence=None)

        with patch.object(session_health.os, "kill", side_effect=lambda *a: kills.append(a)):
            session_health._terminate_detached_harness(entry)

        assert kills == []


class TestStampExecutionSpawn:
    """``stamp_execution_spawn`` appends the fence and updates the live scalars."""

    def _bare_session(self):
        # Construct without touching Redis: bypass __init__ persistence.
        from models.agent_session import AgentSession

        s = AgentSession.__new__(AgentSession)
        s.spawn_history = None
        s.exec_pid = None
        s.pid_create_time = None
        s.exec_cwd = None
        s.exec_harness = None
        return s

    def test_stamp_appends_and_sets_live_fence(self):
        s = self._bare_session()
        saved = {}
        with patch.object(type(s), "save", lambda self, **kw: saved.update(kw)):
            s.stamp_execution_spawn(
                pid=555, create_time=12.5, cwd="/w", harness="claude", generation=1
            )
            s.stamp_execution_spawn(
                pid=556, create_time=13.5, cwd="/w2", harness="claude", generation=2
            )

        assert len(s.spawn_history) == 2, "each spawn appends a history record"
        assert s.exec_pid == 556, "scalar tracks the newest spawn"
        assert s.pid_create_time == 13.5
        # live_fence == newest history entry
        assert s.live_fence["pid"] == 556
        assert s.live_fence["create_time"] == 13.5
        assert s.live_fence["generation"] == 2
        # partial save scoped to the fence fields only
        assert "exec_pid" in saved.get("update_fields", [])

    def test_in_process_subagent_pid_none_carries_agent_id(self):
        s = self._bare_session()
        with patch.object(type(s), "save", lambda self, **kw: None):
            s.stamp_execution_spawn(
                pid=None, create_time=None, cwd=None, harness="claude", agent_id="agent-xyz"
            )
        assert s.exec_pid is None
        assert s.live_fence["agent_id"] == "agent-xyz"

    def test_save_failure_logs_a_warning_not_a_debug_line(self, caplog):
        """A stamping failure is the fence's single point of failure (#2518).

        When this save raises, every downstream consumer silently degrades to
        "no fence recorded" for the session. It used to log at DEBUG, so a
        production failure was invisible. It must be observable at WARNING.
        """
        s = self._bare_session()
        s.id = "sess-stamp-fail"

        def _boom(self, **kwargs):
            raise RuntimeError("redis down")

        with (
            patch.object(type(s), "save", _boom),
            caplog.at_level(logging.WARNING, logger="models.agent_session"),
        ):
            # Fail-silent: persistence must never crash a turn.
            s.stamp_execution_spawn(pid=555, create_time=12.5, cwd="/w", harness="claude")

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "a stamping failure must not be invisible (was DEBUG, now WARNING)"
        msg = warnings[-1].getMessage()
        assert "stamp_execution_spawn save failed" in msg
        assert "555" in msg, "the WARNING must name the pid"
        assert "sess-stamp-fail" in msg, "the WARNING must name the session"
        assert "redis down" in msg
        # The in-memory fence is still updated — only persistence failed.
        assert s.exec_pid == 555

    def test_spawn_history_is_unbounded_by_count(self):
        """Deliberate design pin (models/agent_session.py:335-336, #2518).

        ``spawn_history`` is bounded by the session TTL, NOT by entry count, so
        a long-lived session's full spawn provenance survives for forensics.
        This pins that so introducing a cap is a conscious decision with a
        failing test to update, not a silent truncation of the audit trail.
        """
        s = self._bare_session()
        with patch.object(type(s), "save", lambda self, **kw: None):
            for gen in range(1, 51):
                s.stamp_execution_spawn(
                    pid=1000 + gen,
                    create_time=float(gen),
                    cwd="/w",
                    harness="claude",
                    generation=gen,
                )

        assert len(s.spawn_history) == 50, "spawn_history is unbounded by count by design"
        assert s.spawn_history[0]["generation"] == 1, "the OLDEST entry is retained, not evicted"
        assert s.live_fence["generation"] == 50, "live_fence is always the newest entry"


def _row(pid, ct, *, sid):
    """A hydrated AgentSession stand-in for the forward scan.

    ``find_live_session_by_pid`` routes rows through the real
    ``_filter_hydrated_sessions``, whose canonical check is a string
    ``agent_session_id`` AND a string ``session_id``. A bare
    ``SimpleNamespace(live_fence=...)`` is correctly dropped as a phantom, so
    every stand-in here carries both identity fields.
    """
    return SimpleNamespace(
        agent_session_id=sid,
        session_id=sid,
        id=sid,
        live_fence={"pid": pid, "create_time": ct},
    )


def _fake_query(rows_by_status):
    """Patchable ``AgentSession.query`` returning per-status cohorts.

    A status mapped to an Exception instance raises it, standing in for a
    poisoned/unreadable cohort.
    """

    def fake_filter(**kwargs):
        rows = rows_by_status.get(kwargs.get("status"), [])
        if isinstance(rows, Exception):
            raise rows
        return rows

    return SimpleNamespace(filter=fake_filter)


class TestFindLiveSessionByPid:
    """Reverse lookup resolves ownership via a forward scan over non-terminal statuses.

    The scan itself is exercised against REAL Redis rows in
    ``tests/integration/test_orphan_reap_forward_scan.py``; this class covers
    the match-resolution rules in isolation.
    """

    def test_resolves_owner_by_fence_membership(self):
        from models.agent_session import AgentSession

        owner = _row(777, 1.0, sid="owner")
        other = _row(888, 2.0, sid="other")

        with patch.object(AgentSession, "query", _fake_query({"running": [owner, other]})):
            assert AgentSession.find_live_session_by_pid(777) is owner
            assert AgentSession.find_live_session_by_pid(888) is other
            assert AgentSession.find_live_session_by_pid(999) is None
            assert AgentSession.find_live_session_by_pid(None) is None

    def test_phantom_rows_are_filtered_out(self):
        """Un-hydrated rows never resolve ownership (``_filter_hydrated_sessions``)."""
        from models.agent_session import AgentSession

        phantom = SimpleNamespace(live_fence={"pid": 777, "create_time": 1.0})

        with patch.object(AgentSession, "query", _fake_query({"running": [phantom]})):
            assert AgentSession.find_live_session_by_pid(777, 1.0) is None

    def test_both_sides_record_create_time_and_agree_is_a_fenced_match(self):
        from models.agent_session import AgentSession

        owner = _row(777, 1000.0, sid="owner")
        with patch.object(AgentSession, "query", _fake_query({"running": [owner]})):
            assert AgentSession.find_live_session_by_pid(777, 1000.0) is owner
            # Sub-tolerance skew still agrees.
            assert (
                AgentSession.find_live_session_by_pid(777, 1000.0 + CREATE_TIME_TOLERANCE_S / 2)
                is owner
            )

    def test_both_sides_record_create_time_and_disagree_is_not_a_match(self):
        """A recycled pid: the row's fence is stale, so it does NOT own this process."""
        from models.agent_session import AgentSession

        owner = _row(777, 1000.0, sid="owner")
        with patch.object(AgentSession, "query", _fake_query({"running": [owner]})):
            assert AgentSession.find_live_session_by_pid(777, 5000.0) is None

    def test_caller_observed_none_against_a_row_that_records_one_falls_back_to_pid_only(self):
        """Unknown on the CALLER's side → pid-only match (pre-#2518 behavior)."""
        from models.agent_session import AgentSession

        owner = _row(777, 1000.0, sid="owner")
        with patch.object(AgentSession, "query", _fake_query({"running": [owner]})):
            assert AgentSession.find_live_session_by_pid(777, None) is owner
            assert AgentSession.find_live_session_by_pid(777) is owner

    def test_legacy_row_with_no_recorded_create_time_falls_back_to_pid_only(self):
        """Unknown on the ROW's side (legacy row) → pid-only match, so it still resolves."""
        from models.agent_session import AgentSession

        legacy = _row(777, None, sid="legacy")
        with patch.object(AgentSession, "query", _fake_query({"running": [legacy]})):
            assert AgentSession.find_live_session_by_pid(777, 1000.0) is legacy
            assert AgentSession.find_live_session_by_pid(777, None) is legacy

    def test_uncoercible_caller_create_time_degrades_to_pid_only(self):
        from models.agent_session import AgentSession

        owner = _row(777, 1000.0, sid="owner")
        with patch.object(AgentSession, "query", _fake_query({"running": [owner]})):
            assert AgentSession.find_live_session_by_pid(777, "garbage") is owner

    def test_fenced_match_beats_a_pid_only_match(self):
        """Race 5: a stale dormant row and a live running row both claim pid P.

        The fence — not ``frozenset`` iteration order — decides.
        """
        from models.agent_session import AgentSession

        stale = _row(777, None, sid="stale-dormant")  # legacy row, pid-only candidate
        live = _row(777, 1000.0, sid="live-running")  # fenced candidate

        for cohorts in (
            {"dormant": [stale], "running": [live]},
            {"dormant": [live], "running": [stale]},  # cohorts swapped
        ):
            with patch.object(AgentSession, "query", _fake_query(cohorts)):
                assert AgentSession.find_live_session_by_pid(777, 1000.0) is live

    def test_multiple_matches_log_a_warning(self, caplog):
        """Silent non-deterministic resolution is the sharpest failure mode here."""
        from models.agent_session import AgentSession

        a = _row(777, 1000.0, sid="dup-a")
        b = _row(777, 1000.0, sid="dup-b")

        with (
            patch.object(AgentSession, "query", _fake_query({"running": [a, b]})),
            caplog.at_level(logging.WARNING, logger="models.agent_session"),
        ):
            result = AgentSession.find_live_session_by_pid(777, 1000.0)

        assert result in (a, b)
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("claiming pid=777" in m for m in warnings), (
            f"a duplicate fence pid must log a WARNING; got {warnings}"
        )
        assert any("dup-a" in m and "dup-b" in m for m in warnings), (
            "the WARNING must name every colliding session id"
        )

    def test_single_match_logs_no_multi_match_warning(self, caplog):
        from models.agent_session import AgentSession

        owner = _row(777, 1000.0, sid="owner")
        with (
            patch.object(AgentSession, "query", _fake_query({"running": [owner]})),
            caplog.at_level(logging.WARNING, logger="models.agent_session"),
        ):
            assert AgentSession.find_live_session_by_pid(777, 1000.0) is owner

        assert not [r for r in caplog.records if "claiming pid" in r.getMessage()]

    @pytest.mark.parametrize(
        "exc",
        [
            RedisError("connection reset"),
            QueryException("bad index"),
            AttributeError("no such attr"),
            TypeError("unhashable"),
            ValueError("bad value"),
        ],
    )
    def test_blinded_cohort_warns_and_the_scan_continues(self, caplog, exc):
        """A poisoned cohort must not unprotect live sessions in other cohorts.

        The scan fails toward PROTECTED: it logs a WARNING and continues to the
        remaining statuses, so an owner in a healthy cohort still resolves.
        Returning None here would let a live session's subprocess be treated as
        an unowned orphan.
        """
        from models.agent_session import AgentSession

        owner = _row(777, 1000.0, sid="owner")
        cohorts = {"dormant": exc, "running": [owner]}

        with (
            patch.object(AgentSession, "query", _fake_query(cohorts)),
            caplog.at_level(logging.WARNING, logger="models.agent_session"),
        ):
            result = AgentSession.find_live_session_by_pid(777, 1000.0)

        assert result is owner, "a blinded cohort must not blind the whole scan"
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("scan failed for status=dormant" in m for m in warnings), (
            f"a blinded cohort must log a WARNING; got {warnings}"
        )

    def test_non_integer_fence_pid_is_skipped_not_fatal(self):
        from models.agent_session import AgentSession

        junk = _row("not-a-pid", 1000.0, sid="junk")
        owner = _row(777, 1000.0, sid="owner")
        with patch.object(AgentSession, "query", _fake_query({"running": [junk, owner]})):
            assert AgentSession.find_live_session_by_pid(777, 1000.0) is owner

    def test_signature_accepts_create_time(self):
        """Pins the interface change so a revert fails loudly (Verification row)."""
        import inspect

        from models.agent_session import AgentSession

        params = inspect.signature(AgentSession.find_live_session_by_pid).parameters
        assert "create_time" in params
        assert params["create_time"].default is None, (
            "create_time must be OPTIONAL — omitting it preserves pid-only behavior "
            "for legacy callers"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
