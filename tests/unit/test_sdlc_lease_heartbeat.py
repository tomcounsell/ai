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

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
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

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
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

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
            calls.append((run_id, peek, renew_only))
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
        # Each iteration: one peek then one mutating renew (run_id="mine").
        peeks = [c for c in calls if c[1] is True]
        renews = [c for c in calls if c[1] is False]
        assert len(peeks) >= 1
        assert len(renews) >= 1
        assert all(run_id == "mine" for run_id, _peek, _renew_only in renews)
        # Issue #2714 L0: the extend is renew-only -- it can never mint.
        assert all(renew_only is True for _run_id, _peek, renew_only in renews)

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
            mock_redis.eval.return_value = 1  # CAS renewal wins
            rc = run_heartbeat(
                issue_number=2537,
                run_id="mine",
                interval=1,
                max_lifetime=2,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        lock_key = "session:issuelock:2537"
        # The mutating renew fired despite the dead payload pid. Under
        # renew_only (issue #2714 L0) the renewal is a compare-and-set Lua
        # EVAL carrying the full self-healed payload and a fresh TTL -- never
        # a SET (which could mint).
        lock_sets = [c for c in mock_redis.set.call_args_list if c.args[0] == lock_key]
        assert lock_sets == []  # only the supervised-run signal used SET
        assert mock_redis.eval.call_count >= 1
        eval_args = mock_redis.eval.call_args.args
        assert eval_args[1] == 1
        assert eval_args[2] == lock_key
        assert eval_args[3] == stored  # ARGV[1]: the exact raw value just read
        renewed = json.loads(eval_args[4])  # ARGV[2]: the self-healed payload
        assert renewed["run_id"] == "mine"
        assert int(eval_args[5]) > 0  # ARGV[3]: the TTL

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

        def flaky_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
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

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
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

        The mutation that matters: writing the signal before (or regardless
        of) the ownership peek would republish a non-owner's ``run_id`` over a
        live successor's, which DENIES the successor's forks their inheritance
        signal and re-creates the #2659 wedge. It cannot hand them the wrong
        ``run_id`` -- ``supervised_run_status`` reports live only when the
        signal's ``run_id`` matches the lock's owner, so a mis-owned signal
        reads not-live. That liveness anchor is what bounds this to a
        stand-down rather than a takeover.
        """
        from tools.sdlc_lease_heartbeat import run_heartbeat

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
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

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
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

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
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


class TestReleaseRaceIsClosed:
    """Race 1 (issue #2714): the supervisor releases the lease in the window
    between the heartbeat's peek and its extend.

    Before the L0 renew-only mode, the extend's ``SET NX`` succeeded on the
    now-absent key and the heartbeat RE-MINTED the lease it had just lost --
    then renewed it to the 4h ceiling with no supervisor behind it, which is
    #2714's exact bug. Runs the REAL ``touch_issue_lock`` against the
    per-worker test Redis db: only the release is injected.
    """

    ISSUE = 271401
    KEY = f"session:issuelock:{ISSUE}"

    def teardown_method(self):
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.delete(self.KEY)

    def test_release_between_peek_and_extend_leaves_key_absent(self):
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        from models.session_lifecycle import touch_issue_lock
        from tools.sdlc_lease_heartbeat import run_heartbeat

        _R.set(
            self.KEY,
            json.dumps(
                {
                    "run_id": "mine",
                    "session_id": "sdlc-local-2714",
                    "pid": 1,
                    "hostname": "h",
                }
            ),
            ex=1800,
        )

        calls = {"n": 0}

        def touch_then_release(*args, **kwargs):
            result = touch_issue_lock(*args, **kwargs)
            if kwargs.get("peek"):
                calls["n"] += 1
                if calls["n"] == 1:
                    # The supervisor releases the lease right after our peek
                    # reported self-ownership, before the extend lands.
                    _R.delete(self.KEY)
            return result

        # Deterministic clock with a generous budget: the loop is free to run
        # many ticks, so ending after exactly two proves it took the
        # lease-lost exit rather than the deadline.
        clock = {"n": 0}

        def monotonic():
            clock["n"] += 1
            return 0.0 if clock["n"] <= 20 else 999.0

        signals = []
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=touch_then_release),
            patch(
                "agent.supervised_run.write_supervised_run_signal",
                side_effect=lambda *a, **kw: signals.append(a),
            ),
        ):
            rc = run_heartbeat(
                issue_number=self.ISSUE,
                run_id="mine",
                session_id="sdlc-local-2714",
                interval=1,
                max_lifetime=100,
                _sleep=lambda s: None,
                _monotonic=monotonic,
            )

        assert rc == 0
        assert calls["n"] == 2  # tick 1 renewed nothing; tick 2 saw it gone
        # The extend did NOT recreate the released key.
        assert _R.get(self.KEY) is None
