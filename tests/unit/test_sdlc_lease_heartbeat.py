"""Unit tests for tools.sdlc_lease_heartbeat (issue #2446/#2451).

The heartbeat is PEEK-FIRST, RENEW-ONLY (BLOCKER 1): it must NEVER call the
mutating ``touch_issue_lock`` unless a peek first confirms the lease is owned by
its own run_id. This closes the lease-theft hole (Risk 2) where a zombie
heartbeat re-acquires a lapsed lease under a stale id and blocks a successor.

Tests cover:
- terminate-on-foreign-owner (a successor owns the lease -> exit, no renew)
- exit-immediately-on-no-ownership (lease absent/lapsed -> exit, never mints)
- renew-on-self-owner (self owns -> mutating renew fires, then bounded exit)
- ownership-not-pid-liveness (#2537 review): a self-owned lease whose payload
  pid is dead (the ephemeral session-ensure CLI stamped it) still renews
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


def _peek_result(owner_run_id):
    r = MagicMock()
    r.owner_run_id = owner_run_id
    r.acquired = owner_run_id is None
    return r


class TestPeekFirstRenewOnly:
    def test_exits_immediately_when_lease_absent_never_mints(self):
        """peek.owner_run_id is None (lease absent/lapsed) -> exit 0 without
        ever calling the mutating (run_id-bearing) form -- no lease-theft."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        calls = []

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False):
            calls.append((run_id, peek))
            # Peek on an absent key reports no owner.
            return _peek_result(None)

        slept = []
        with patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch):
            rc = run_heartbeat(
                issue_number=2446,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                _sleep=lambda s: slept.append(s),
            )

        assert rc == 0
        # Exactly one call -- the peek (peek=True, non-mutating). No mutating call.
        assert calls == [("mine", True)]
        assert not any(peek is False for _run_id, peek in calls)
        assert slept == []  # exited before sleeping

    def test_terminates_on_foreign_owner_no_renew(self):
        """A successor owns the lease (peek.owner_run_id != run_id) -> exit 0,
        no mutating renew."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        calls = []

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False):
            calls.append((run_id, peek))
            return _peek_result("successor-run")

        with patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch):
            rc = run_heartbeat(
                issue_number=2446,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                _sleep=lambda s: None,
            )

        assert rc == 0
        assert calls == [("mine", True)]  # peek only, then exit
        assert all(peek is True for _run_id, peek in calls)

    def test_renews_on_self_owner_then_exits_at_deadline(self):
        """Self owns the lease -> the mutating renew (run_id-bearing) fires each
        tick; the loop self-terminates once max-lifetime is reached."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        calls = []

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False):
            calls.append((run_id, peek))
            if peek:
                return _peek_result("mine")  # self still owns
            return MagicMock()  # renew result (unused)

        # Deterministic clock: advance past the deadline after two ticks.
        ticks = iter([0.0, 1.0, 2.0, 999.0])

        with patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch):
            rc = run_heartbeat(
                issue_number=2446,
                run_id="mine",
                interval=1,
                max_lifetime=5,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        # Each iteration: one peek (run_id=None) then one mutating renew (run_id="mine").
        peeks = [c for c in calls if c[1] is True]
        renews = [c for c in calls if c[1] is False]
        assert len(peeks) >= 1
        assert len(renews) >= 1
        assert all(run_id == "mine" for run_id, peek in renews)  # renew under self only

    def test_renews_when_self_owned_payload_pid_is_dead(self):
        """#2537 review regression (PR #2615): the lease payload's pid is
        stamped by the short-lived `sdlc-tool session-ensure` CLI and is dead
        by the heartbeat's first tick. The heartbeat must judge OWNERSHIP
        (run_id match), never pid-liveness -- a pid-keyed guard would exit on
        tick one for every local run and lapse the lease mid-stage (the exact
        #2446/#2451 failure the heartbeat prevents).

        Exercises the REAL touch_issue_lock peek path (only Redis and psutil
        are stubbed): same-machine payload, dead pid, matching run_id -> the
        mutating renew still fires."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        stored = json.dumps(
            {
                "run_id": "mine",
                "session_id": "sdlc-local-2537",
                "pid": 4242,  # the session-ensure CLI's pid -- long dead
                "machine_id": "hw-uuid-1",
                "hostname": "this-host",
                "create_time": 1.0,
            }
        )

        # Deterministic clock: one full tick, then past the deadline.
        ticks = iter([0.0, 1.0, 999.0])
        with (
            patch("models.session_lifecycle._local_machine_id", return_value="hw-uuid-1"),
            # pid 4242 is dead -- any pid-liveness inference reads orphaned.
            patch("agent.session_health._psutil_process_for_pid", return_value=None),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
        ):
            mock_redis.get.return_value = stored
            mock_redis.set.return_value = False  # renew path: NX fails, re-SET follows
            rc = run_heartbeat(
                issue_number=2537,
                run_id="mine",
                interval=1,
                max_lifetime=2,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        # The mutating renew fired despite the dead payload pid: the renewal
        # branch re-SETs the full payload with a fresh TTL.
        assert mock_redis.set.call_count >= 1
        _args, kwargs = mock_redis.set.call_args
        assert kwargs.get("ex") is not None  # TTL-bearing renewal re-SET
        renewed = json.loads(_args[1])
        assert renewed["run_id"] == "mine"

    def test_missing_identifiers_exit_clean_never_touch(self):
        from tools.sdlc_lease_heartbeat import run_heartbeat

        with patch("models.session_lifecycle.touch_issue_lock") as touch:
            assert run_heartbeat(issue_number=0, run_id="mine") == 0
            assert run_heartbeat(issue_number=2446, run_id="") == 0
            touch.assert_not_called()

    def test_tick_exception_is_swallowed_and_loop_continues(self):
        """A Redis hiccup on one tick must not crash the loop -- best-effort."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        state = {"n": 0}

        def flaky_touch(issue, run_id, session_id="", ttl=None, peek=False):
            state["n"] += 1
            if state["n"] == 1 and peek:
                raise RuntimeError("redis down")
            # Second tick: lease is foreign -> clean exit.
            return _peek_result("successor")

        ticks = iter([0.0, 1.0, 2.0, 999.0])
        with patch("models.session_lifecycle.touch_issue_lock", side_effect=flaky_touch):
            rc = run_heartbeat(
                issue_number=2446,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )
        assert rc == 0
        assert state["n"] >= 2  # survived the raising tick, ran again


class TestSupervisedRunSignalRenewal:
    """Issue #2659: the signal must renew with the lease, not just at acquire.

    ``write_supervised_run_signal`` had a single call site -- the acquire path
    in ``tools/sdlc_session_ensure.py`` -- while this heartbeat renewed the
    lease forever. So 1800s (the shared TTL) into every pipeline the signal
    expired under a live lease, and from that moment each stage fork the
    supervisor dispatched read a bare ``ISSUE_LOCKED`` from its own
    supervisor's lock and correctly stood down. The pipeline wedged with a live
    lock, a live heartbeat, and no error anywhere.
    """

    def test_self_owned_renew_also_refreshes_the_signal(self):
        """The renewing tick refreshes the signal under the same identity."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False):
            if peek:
                return _peek_result("mine")  # self still owns
            return MagicMock()

        ticks = iter([0.0, 1.0, 999.0])
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch),
            patch("agent.supervised_run.write_supervised_run_signal") as write_signal,
        ):
            rc = run_heartbeat(
                issue_number=2659,
                run_id="mine",
                session_id="sdlc-local-2659",
                interval=1,
                max_lifetime=2,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        assert write_signal.call_count >= 1
        args, kwargs = write_signal.call_args
        assert args[0] == 2659
        assert args[1] == "mine"
        assert kwargs.get("session_id") == "sdlc-local-2659"
        # The worktree file carrier has no TTL, so the renewal deliberately
        # refreshes only the Redis carrier.
        assert kwargs.get("working_dir") is None

    def test_foreign_owner_never_writes_the_signal(self):
        """A successor owns the lease -> exit without resurrecting our signal.

        The mutation that matters: writing the signal before (or regardless of)
        the ownership peek would republish a dead run's inheritance token over a
        live successor's, handing the successor's forks the wrong ``run_id``.
        """
        from tools.sdlc_lease_heartbeat import run_heartbeat

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False):
            return _peek_result("successor-run")

        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch),
            patch("agent.supervised_run.write_supervised_run_signal") as write_signal,
        ):
            rc = run_heartbeat(
                issue_number=2659,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                _sleep=lambda s: None,
            )

        assert rc == 0
        write_signal.assert_not_called()

    def test_absent_lease_never_writes_the_signal(self):
        """Lease absent/lapsed -> exit 0 without publishing a signal for a
        lease we no longer hold (the signal must never outlive the lock)."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False):
            return _peek_result(None)

        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch),
            patch("agent.supervised_run.write_supervised_run_signal") as write_signal,
        ):
            rc = run_heartbeat(
                issue_number=2659,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                _sleep=lambda s: None,
            )

        assert rc == 0
        write_signal.assert_not_called()

    def test_signal_write_failure_does_not_break_lease_renewal(self):
        """The signal is best-effort: a failing write must not stop the loop
        or the lease renewal that keeps the pipeline alive."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        renews = []

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False):
            if peek:
                return _peek_result("mine")
            renews.append(run_id)
            return MagicMock()

        ticks = iter([0.0, 1.0, 2.0, 999.0])
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch),
            patch(
                "agent.supervised_run.write_supervised_run_signal",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            rc = run_heartbeat(
                issue_number=2659,
                run_id="mine",
                interval=1,
                max_lifetime=5,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        assert len(renews) >= 2  # the raising write did not end the loop
