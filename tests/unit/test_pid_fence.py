"""Unit tests for the ``(pid, create_time)`` execution fence (durability plan #2494).

The fence collapses the former ``claude_pid`` / ``pm_pid`` / ``harness_pid`` trio
into one fenced execution record on ``AgentSession``. It is BEST-EFFORT DETECTION
of PID reuse, not a guarantee — macOS has no pidfd, so an irreducible TOCTOU
window remains (https://lwn.net/Articles/784997/). These tests prove the core
detection property: a recorded fence pid that gets recycled to a *different*
process (different ``create_time``) reads as "not ours", so signals are skipped.
"""

from __future__ import annotations

import os
import signal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.pid_fence import CREATE_TIME_TOLERANCE_S, fence_is_live, proc_create_time


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


class TestFindLiveSessionByPid:
    """Reverse lookup resolves ownership via a forward scan over non-terminal statuses."""

    def test_resolves_owner_by_fence_membership(self):
        from models.agent_session import AgentSession
        from models.session_lifecycle import NON_TERMINAL_STATUSES

        owner = SimpleNamespace(live_fence={"pid": 777, "create_time": 1.0})
        other = SimpleNamespace(live_fence={"pid": 888, "create_time": 2.0})
        a_status = next(iter(NON_TERMINAL_STATUSES))

        def fake_filter(**kwargs):
            return [owner, other] if kwargs.get("status") == a_status else []

        with patch.object(AgentSession, "query", SimpleNamespace(filter=fake_filter)):
            assert AgentSession.find_live_session_by_pid(777) is owner
            assert AgentSession.find_live_session_by_pid(888) is other
            assert AgentSession.find_live_session_by_pid(999) is None
            assert AgentSession.find_live_session_by_pid(None) is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
