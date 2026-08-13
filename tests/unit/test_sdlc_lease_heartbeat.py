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

    def test_no_supervisor_args_is_the_unchanged_pre_2714_behavior(self):
        """Default-off proof (#2714 Test Impact): every branch this issue added
        is inert unless an identity was explicitly recorded.

        Called exactly as the pre-#2714 callers call it -- no supervisor args --
        the loop peeks, renews, and exits at the caller's own bound: no process
        is probed, no lease is released, no signal is cleared, and the exit
        reason is the plain ``max_lifetime`` one, not the new unsupervised
        ceiling. A regression here means the supervisor watch acquired a
        default-on foothold.
        """
        import logging

        from tools.sdlc_lease_heartbeat import run_heartbeat

        calls = []

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
            calls.append((run_id, peek, renew_only))
            return _peek_result("mine") if peek else MagicMock()

        ticks = iter([0.0, 1.0, 2.0, 999.0])
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch),
            patch("agent.supervised_run.write_supervised_run_signal") as write_signal,
            patch("agent.session_health._psutil_process_for_pid") as probe,
            patch("models.session_lifecycle.release_issue_lock") as release,
            patch("agent.supervised_run.clear_supervised_run_signal") as clear,
            patch.object(logging.getLogger("tools.sdlc_lease_heartbeat"), "info") as info,
        ):
            rc = run_heartbeat(
                issue_number=2446,
                run_id="mine",
                interval=1,
                max_lifetime=5,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        assert probe.call_count == 0  # the supervisor watch never engaged
        release.assert_not_called()
        clear.assert_not_called()
        renews = [c for c in calls if c[1] is False]
        assert len(renews) >= 1
        assert write_signal.call_count >= 1
        logged = " ".join(str(c.args) for c in info.call_args_list)
        assert "max_lifetime" in logged
        assert "unsupervised_max_lifetime" not in logged

    def test_dead_payload_pid_with_a_live_supervisor_still_renews(self):
        """#2714 must never confuse the PAYLOAD pid with the SUPERVISOR pid.

        Two different pids are in play on every local run: the lease payload's,
        stamped by the ephemeral ``session-ensure`` CLI and dead by tick one,
        and the supervising ``claude`` process's, handed over on the argv. Only
        the latter may end the loop. Here the payload pid is dead and the
        supervisor is alive: the renew must still fire (#2446/#2451) and the
        lease must not be released.

        Runs the REAL ``touch_issue_lock`` against the per-worker test Redis so
        the payload-pid path is genuinely exercised, and keys the psutil double
        on the pid so a mixed-up lookup shows up as an assertion failure rather
        than a coincidence.
        """
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        from tools.sdlc_lease_heartbeat import run_heartbeat

        issue = 271402
        key = f"session:issuelock:{issue}"
        payload_pid, supervisor_pid = 4242, 777
        _R.delete(key)
        _R.set(
            key,
            json.dumps(
                {
                    "run_id": "mine",
                    "session_id": "sdlc-local-2714",
                    "pid": payload_pid,  # the session-ensure CLI -- long dead
                    "machine_id": "hw-uuid-1",
                    "hostname": "this-host",
                    "create_time": 1.0,
                }
            ),
            ex=1800,
        )

        probed = []

        def probe(pid):
            probed.append(pid)
            return None if pid == payload_pid else _alive_proc(99.0)

        ticks = iter([0.0, 1.0, 2.0, 999.0])
        try:
            with (
                patch("models.session_lifecycle._local_machine_id", return_value="hw-uuid-1"),
                patch("agent.session_health._psutil_process_for_pid", side_effect=probe),
                patch("agent.supervised_run.write_supervised_run_signal"),
                patch("models.session_lifecycle.release_issue_lock") as release,
            ):
                rc = run_heartbeat(
                    issue_number=issue,
                    run_id="mine",
                    session_id="sdlc-local-2714",
                    interval=1,
                    max_lifetime=5,
                    supervisor_pid=supervisor_pid,
                    supervisor_create_time=99.0,
                    supervisor_check_interval=1,
                    _sleep=lambda s: None,
                    _monotonic=lambda: next(ticks),
                )

            assert rc == 0
            # The dead payload pid never became a death signal ...
            release.assert_not_called()
            # ... and the supervisor pid is the one the watch actually probed.
            assert supervisor_pid in probed
            # The lease survived, renewed under our own identity.
            stored = _R.get(key)
            assert stored is not None
            renewed = json.loads(stored)
            assert renewed["run_id"] == "mine"
            assert renewed["renewed_at"] > 0
        finally:
            _R.delete(key)

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

    def test_signal_is_cleared_on_the_supervisor_dead_exit(self):
        """#2714: a dead supervisor's signal is dropped, not left to expire.

        #2659 coupled the signal's lifetime to the lease's, which is exactly
        why a zombie heartbeat kept BOTH keys fresh. The coupling has to hold
        in the other direction too: when the watch releases the lease it must
        clear the companion signal under the same identity in the same breath,
        or a stale ``session:supervisedrun:{N}`` outlives the lock it was
        supposed to shadow for up to a full TTL and a successor's forks read a
        dead run's ``run_id``.
        """
        from tools.sdlc_lease_heartbeat import run_heartbeat

        ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0])
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=_self_owned_touch()),
            patch("agent.supervised_run.write_supervised_run_signal"),
            patch("agent.session_health._psutil_process_for_pid", return_value=None),
            patch("models.session_lifecycle.release_issue_lock") as release,
            patch("agent.supervised_run.clear_supervised_run_signal") as clear,
        ):
            rc = run_heartbeat(
                issue_number=2659,
                run_id="mine",
                session_id="sdlc-local-2659",
                interval=1,
                max_lifetime=100,
                supervisor_pid=4242,
                supervisor_create_time=99.0,
                supervisor_check_interval=1,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        # Compare-and-delete keyed on OUR run_id: a successor that already took
        # over is never harmed by this clear.
        clear.assert_called_once_with(2659, "mine")
        release.assert_called_once_with(2659, "mine")


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
        # The extend's compare-and-set loses on the released key, and the
        # heartbeat consumes that result: it exits on THIS tick rather than
        # falling through to the signal write and discovering the loss on a
        # later peek. One peek total.
        assert calls["n"] == 1
        # The extend did NOT recreate the released key.
        assert _R.get(self.KEY) is None
        # And it never republished the companion supervised-run signal with a
        # fresh TTL for a lease it no longer holds. Without this assertion the
        # fail-closed renewal result could be discarded and the suite would
        # still pass.
        assert signals == []

    def test_fail_closed_renewal_also_stops_the_signal_write(self):
        """The other way the extend reports not-acquired: `renew_only`'s
        fail-CLOSED exception handler. It must reach the same exit, or a
        transient Redis blip would keep republishing the supervised-run
        signal for a lease whose ownership could not be verified.
        """
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_lease_heartbeat import run_heartbeat

        def peek_ok_extend_fails_closed(*args, **kwargs):
            if kwargs.get("peek"):
                return IssueLockResult(
                    acquired=False, owner_session_id="sdlc-local-2714", owner_run_id="mine"
                )
            # What touch_issue_lock returns for renew_only=True on any error.
            return IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)

        # Bounded clock: the deadline is reachable, so a regression that stops
        # consuming the fail-closed result fails this test on `signals` rather
        # than spinning until the suite-wide timeout.
        clock = {"n": 0}

        def monotonic():
            clock["n"] += 1
            return 0.0 if clock["n"] <= 20 else 999.0

        signals = []
        with (
            patch(
                "models.session_lifecycle.touch_issue_lock",
                side_effect=peek_ok_extend_fails_closed,
            ),
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
        assert signals == []


class TestSupervisorIdentityResolution:
    """Issue #2714 L2: three-tier supervisor identity (env -> ancestry -> none).

    spike-1 measured ``CLAUDE_PID`` exported into every Claude Code Bash tool
    call, matching the ``claude`` ancestor exactly -- even from a subagent.
    spike-2 proved ``ppid``-based inference is unsound (a HEALTHY heartbeat is
    reparented to pid 1 within seconds of spawn), so the resolver must never
    consult it.
    """

    def test_env_var_resolves_pid_and_create_time(self, monkeypatch):
        from tools import sdlc_supervisor_identity as si

        monkeypatch.setenv("CLAUDE_PID", "32886")
        proc = MagicMock()
        proc.create_time.return_value = 111.5

        with patch("agent.session_health._psutil_process_for_pid", return_value=proc):
            detailed = si.resolve_supervisor_identity_detailed()
            # The 2-tuple form is the same resolution minus the source label.
            assert si.resolve_supervisor_identity() == (32886, 111.5)

        assert detailed == (si.SOURCE_ENV, 32886, 111.5)

    def test_env_var_that_does_not_resolve_falls_through_to_ancestry(self, monkeypatch):
        """A stale/garbage CLAUDE_PID must degrade to the walk, not to nothing."""
        from tools import sdlc_supervisor_identity as si

        monkeypatch.setenv("CLAUDE_PID", "not-a-number")

        ancestor = MagicMock()
        ancestor.pid = 999
        ancestor.exe.return_value = "/opt/homebrew/bin/claude"
        ancestor.create_time.return_value = 222.5
        me = MagicMock()
        me.parents.return_value = [MagicMock(pid=1, **{"exe.return_value": "/bin/zsh"}), ancestor]

        with patch.object(si, "_self_process", return_value=me):
            assert si.resolve_supervisor_identity_detailed() == (si.SOURCE_ANCESTRY, 999, 222.5)

    def test_ancestry_matches_node_process_whose_cmdline_mentions_claude(self, monkeypatch):
        from tools import sdlc_supervisor_identity as si

        monkeypatch.delenv("CLAUDE_PID", raising=False)
        ancestor = MagicMock()
        ancestor.pid = 777
        ancestor.exe.return_value = "/usr/local/bin/node"
        ancestor.cmdline.return_value = ["node", "/x/@anthropic-ai/claude-code/cli.js"]
        ancestor.create_time.return_value = 333.0
        me = MagicMock()
        me.parents.return_value = [ancestor]

        with patch.object(si, "_self_process", return_value=me):
            assert si.resolve_supervisor_identity_detailed() == (si.SOURCE_ANCESTRY, 777, 333.0)

    def test_no_env_and_no_claude_ancestor_is_unresolved(self, monkeypatch):
        from tools import sdlc_supervisor_identity as si

        monkeypatch.delenv("CLAUDE_PID", raising=False)
        other = MagicMock()
        other.pid = 5
        other.exe.return_value = "/bin/zsh"
        other.cmdline.return_value = ["zsh"]
        me = MagicMock()
        me.parents.return_value = [other]

        with patch.object(si, "_self_process", return_value=me):
            assert si.resolve_supervisor_identity_detailed() == (si.SOURCE_UNRESOLVED, None, None)
            assert si.resolve_supervisor_identity() == (None, None)

    def test_any_exception_resolves_to_none_never_raises(self, monkeypatch):
        from tools import sdlc_supervisor_identity as si

        monkeypatch.delenv("CLAUDE_PID", raising=False)
        with patch.object(si, "_self_process", side_effect=RuntimeError("psutil exploded")):
            assert si.resolve_supervisor_identity_detailed() == (si.SOURCE_UNRESOLVED, None, None)
            # The best-effort contract asserted through its OBSERVABLE fallback,
            # not through a `pass`: the caller gets (None, None) and no
            # exception crosses the boundary into the ensure that spawns the
            # heartbeat.
            assert si.resolve_supervisor_identity() == (None, None)

    def test_env_tier_raising_degrades_to_the_ancestry_tier(self, monkeypatch):
        """A failing tier degrades to the next one, never to unresolved."""
        from tools import sdlc_supervisor_identity as si

        monkeypatch.setenv("CLAUDE_PID", "32886")
        ancestor = MagicMock()
        ancestor.pid = 999
        ancestor.exe.return_value = "/opt/homebrew/bin/claude"
        ancestor.create_time.return_value = 222.5
        me = MagicMock()
        me.parents.return_value = [ancestor]

        with (
            patch(
                "agent.session_health._psutil_process_for_pid",
                side_effect=RuntimeError("psutil exploded"),
            ),
            patch.object(si, "_self_process", return_value=me),
        ):
            assert si.resolve_supervisor_identity_detailed() == (si.SOURCE_ANCESTRY, 999, 222.5)

    def test_module_never_consults_the_parent_pid_syscall(self):
        """spike-2 anti-criterion: ``ppid``/``getppid`` is not a death signal."""
        import inspect

        from tools import sdlc_supervisor_identity as si

        src = inspect.getsource(si)
        assert "getppid" not in src


def _alive_proc(create_time):
    p = MagicMock()
    p.create_time.return_value = create_time
    return p


def _self_owned_touch():
    """A touch_issue_lock double that always reports self-ownership."""

    def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
        if peek:
            return _peek_result(run_id)
        return MagicMock()

    return fake_touch


class TestSupervisorLiveness:
    """Issue #2714 L2: a positively-dead supervisor drops the lease.

    The evidence bar is deliberately high (Risk 5 + #2446): a pid that psutil
    cannot find, or one whose ``create_time`` no longer matches, counts as
    dead -- but only after ``SDLC_SUPERVISOR_DEATH_CONFIRMATIONS`` consecutive
    such observations, and ANY exception counts as NOT dead so a psutil flake
    can never lapse a live run's lease.
    """

    def test_dead_supervisor_releases_the_lease_and_exits(self, caplog):
        import logging

        from tools.sdlc_lease_heartbeat import run_heartbeat

        caplog.set_level(logging.INFO, logger="tools.sdlc_lease_heartbeat")
        ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0])

        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=_self_owned_touch()),
            patch("agent.supervised_run.write_supervised_run_signal"),
            patch("agent.session_health._psutil_process_for_pid", return_value=None),
            patch("models.session_lifecycle.release_issue_lock") as release,
            patch("agent.supervised_run.clear_supervised_run_signal") as clear,
        ):
            rc = run_heartbeat(
                issue_number=2714,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                supervisor_pid=4242,
                supervisor_create_time=99.0,
                supervisor_check_interval=1,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        release.assert_called_once()
        clear.assert_called_once()
        assert any("supervisor_dead" in r.getMessage() for r in caplog.records if r.levelno >= 20)

    def test_live_supervisor_keeps_renewing(self):
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
            patch("agent.supervised_run.write_supervised_run_signal"),
            patch("agent.session_health._psutil_process_for_pid", return_value=_alive_proc(99.0)),
            patch("models.session_lifecycle.release_issue_lock") as release,
        ):
            rc = run_heartbeat(
                issue_number=2714,
                run_id="mine",
                interval=1,
                max_lifetime=10,
                supervisor_pid=4242,
                supervisor_create_time=99.0,
                supervisor_check_interval=1,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        assert len(renews) >= 2
        release.assert_not_called()

    def test_unresolved_supervisor_never_consults_psutil(self):
        """No recorded identity -> the watch is inert; nothing is probed."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        ticks = iter([0.0, 1.0, 2.0, 999.0])
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=_self_owned_touch()),
            patch("agent.supervised_run.write_supervised_run_signal"),
            patch("agent.session_health._psutil_process_for_pid") as probe,
            patch("models.session_lifecycle.release_issue_lock") as release,
        ):
            rc = run_heartbeat(
                issue_number=2714,
                run_id="mine",
                interval=1,
                max_lifetime=10,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        assert probe.call_count == 0
        release.assert_not_called()

    def test_partial_or_invalid_identity_is_unresolved_never_dead(self):
        """pid 0/negative/non-numeric, or one half missing -> unresolved."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        for pid, create_time in [
            (0, 99.0),
            (-5, 99.0),
            ("not-a-pid", 99.0),
            (4242, None),
            (None, 99.0),
            (4242, "not-a-time"),
        ]:
            ticks = iter([0.0, 1.0, 999.0])
            with (
                patch("models.session_lifecycle.touch_issue_lock", side_effect=_self_owned_touch()),
                patch("agent.supervised_run.write_supervised_run_signal"),
                patch("agent.session_health._psutil_process_for_pid") as probe,
                patch("models.session_lifecycle.release_issue_lock") as release,
            ):
                rc = run_heartbeat(
                    issue_number=2714,
                    run_id="mine",
                    interval=1,
                    max_lifetime=10,
                    supervisor_pid=pid,
                    supervisor_create_time=create_time,
                    supervisor_check_interval=1,
                    _sleep=lambda s: None,
                    _monotonic=lambda: next(ticks),
                )
            assert rc == 0, (pid, create_time)
            assert probe.call_count == 0, (pid, create_time)
            release.assert_not_called()

    def test_create_time_mismatch_counts_as_dead(self, caplog):
        """Risk 5: the OS recycled the pid -- alive, but not our supervisor."""
        import logging

        from tools.sdlc_lease_heartbeat import run_heartbeat

        caplog.set_level(logging.INFO, logger="tools.sdlc_lease_heartbeat")
        ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0])
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=_self_owned_touch()),
            patch("agent.supervised_run.write_supervised_run_signal"),
            patch(
                "agent.session_health._psutil_process_for_pid",
                return_value=_alive_proc(123.456),  # recorded was 99.0
            ),
            patch("models.session_lifecycle.release_issue_lock") as release,
            patch("agent.supervised_run.clear_supervised_run_signal"),
        ):
            rc = run_heartbeat(
                issue_number=2714,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                supervisor_pid=4242,
                supervisor_create_time=99.0,
                supervisor_check_interval=1,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        release.assert_called_once()
        assert any("supervisor_dead" in r.getMessage() for r in caplog.records)

    def test_single_flake_does_not_trip_the_confirmation_gate(self):
        """One dead observation followed by a live one must NOT release."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        seq = [None, _alive_proc(99.0), _alive_proc(99.0), _alive_proc(99.0)]

        def probe(_pid):
            return seq.pop(0) if seq else _alive_proc(99.0)

        ticks = iter([0.0, 1.0, 2.0, 3.0, 999.0])
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=_self_owned_touch()),
            patch("agent.supervised_run.write_supervised_run_signal"),
            patch("agent.session_health._psutil_process_for_pid", side_effect=probe),
            patch("models.session_lifecycle.release_issue_lock") as release,
        ):
            rc = run_heartbeat(
                issue_number=2714,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                supervisor_pid=4242,
                supervisor_create_time=99.0,
                supervisor_check_interval=1,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        release.assert_not_called()

    def test_psutil_exception_keeps_renewing_never_releases(self):
        """#2446: an unverifiable probe fails TOWARD holding the lease, and the
        tick's renew still fires -- the watch must never break the renewer."""
        from tools.sdlc_lease_heartbeat import run_heartbeat

        renews = []

        def fake_touch(issue, run_id, session_id="", ttl=None, peek=False, renew_only=False):
            if peek:
                return _peek_result("mine")
            renews.append(run_id)
            return MagicMock()

        ticks = iter([0.0, 1.0, 2.0, 3.0, 999.0])
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=fake_touch),
            patch("agent.supervised_run.write_supervised_run_signal"),
            patch(
                "agent.session_health._psutil_process_for_pid",
                side_effect=RuntimeError("psutil exploded"),
            ),
            patch("models.session_lifecycle.release_issue_lock") as release,
        ):
            rc = run_heartbeat(
                issue_number=2714,
                run_id="mine",
                interval=1,
                max_lifetime=100,
                supervisor_pid=4242,
                supervisor_create_time=99.0,
                supervisor_check_interval=1,
                _sleep=lambda s: None,
                _monotonic=lambda: next(ticks),
            )

        assert rc == 0
        release.assert_not_called()
        assert len(renews) >= 3  # the raising probe never stopped the renewer

    def test_malformed_supervisor_argv_never_aborts_the_cli(self):
        """The CLI boundary half of "malformed means unresolved, not dead".

        ``--supervisor-pid`` / ``--supervisor-create-time`` are deliberately
        parsed as strings. With ``type=int`` / ``type=float`` a garbage value
        would abort argparse with exit code 2 and leave the lease with NO
        renewer at all -- strictly worse than the unresolved fallback. The
        values must reach ``run_heartbeat`` verbatim, where
        ``_resolved_supervisor`` classifies them as unresolved.
        """
        import sys

        from tools import sdlc_lease_heartbeat as hb

        for pid, create_time in [("not-a-pid", "99.0"), ("0", "99.0"), ("-5", "not-a-time")]:
            argv = [
                "sdlc_lease_heartbeat",
                "--issue-number",
                "2714",
                "--run-id",
                "mine",
                "--supervisor-pid",
                pid,
                "--supervisor-create-time",
                create_time,
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(hb, "run_heartbeat", return_value=0) as run,
                patch.object(hb.logging, "basicConfig"),
                patch.object(hb.sys, "exit"),
            ):
                hb.main()  # no SystemExit(2) from argparse

            assert run.call_args.kwargs["supervisor_pid"] == pid
            assert hb._resolved_supervisor(pid, create_time) == (None, None)


class TestUnsupervisedCeiling:
    """Issue #2714 L3: an unresolvable supervisor gets a 90-minute bound.

    Risk 1: the shortened bound applies ONLY on the unresolvable path, and it
    only STOPS RENEWING -- it never releases, because failing to resolve a
    supervisor is not positive proof the run is dead. The lease's own 1800s
    TTL is the correct disposition there.
    """

    def _run(self, ticks, **kwargs):
        from tools.sdlc_lease_heartbeat import run_heartbeat

        it = iter(ticks)
        with (
            patch("models.session_lifecycle.touch_issue_lock", side_effect=_self_owned_touch()),
            patch("agent.supervised_run.write_supervised_run_signal"),
            patch("agent.session_health._psutil_process_for_pid", return_value=_alive_proc(99.0)),
            patch("models.session_lifecycle.release_issue_lock") as release,
            patch("agent.supervised_run.clear_supervised_run_signal") as clear,
        ):
            rc = run_heartbeat(
                issue_number=2714,
                run_id="mine",
                interval=1,
                _sleep=lambda s: None,
                _monotonic=lambda: next(it),
                **kwargs,
            )
        return rc, release, clear

    def test_unresolved_supervisor_selects_the_ninety_minute_bound(self, caplog):
        import logging

        from tools.sdlc_lease_heartbeat import UNSUPERVISED_MAX_LIFETIME_SECONDS

        assert UNSUPERVISED_MAX_LIFETIME_SECONDS == 90 * 60

        caplog.set_level(logging.INFO, logger="tools.sdlc_lease_heartbeat")
        # Inside the 5400s bound, then past it.
        rc, release, clear = self._run([0.0, 5399.0, 5401.0])

        assert rc == 0
        assert any("unsupervised_max_lifetime" in r.getMessage() for r in caplog.records)
        # Risk 1: stop renewing, but NEVER release -- the TTL is the backstop.
        release.assert_not_called()
        clear.assert_not_called()

    def test_resolvable_supervisor_keeps_the_unchanged_four_hour_bound(self, caplog):
        """The invariant the round-1 plan asserted and never tested.

        A tick at 5401s -- past the unsupervised ceiling -- must still renew,
        proving the 4h bound is the one selected whenever a supervisor pid is
        present. Lowering MAX_LIFETIME_SECONDS uniformly would lapse a live
        supervisor's long BUILD stage and reintroduce #2446.
        """
        import logging

        from tools.sdlc_lease_heartbeat import MAX_LIFETIME_SECONDS

        assert MAX_LIFETIME_SECONDS == 4 * 60 * 60

        caplog.set_level(logging.INFO, logger="tools.sdlc_lease_heartbeat")
        rc, release, _clear = self._run(
            [0.0, 5401.0, 14401.0],
            supervisor_pid=4242,
            supervisor_create_time=99.0,
            supervisor_check_interval=1,
        )

        assert rc == 0
        assert not any("unsupervised_max_lifetime" in r.getMessage() for r in caplog.records)
        assert any("max_lifetime" in r.getMessage() for r in caplog.records)
        release.assert_not_called()

    def test_explicit_max_lifetime_overrides_both_defaults(self):
        """The sentinel: an explicitly-passed bound is never silently replaced."""
        rc, _release, _clear = self._run([0.0, 3.0], max_lifetime=2)
        assert rc == 0  # exited at the caller's bound, not at 5400

    def test_invalid_env_override_falls_back_to_the_default(self, monkeypatch):
        import importlib

        for bad in ("not-a-number", "-1", "0", ""):
            monkeypatch.setenv("SDLC_HEARTBEAT_UNSUPERVISED_MAX_SECONDS", bad)
            import tools.sdlc_lease_heartbeat as hb

            hb = importlib.reload(hb)
            assert hb.UNSUPERVISED_MAX_LIFETIME_SECONDS == 90 * 60, bad

        monkeypatch.delenv("SDLC_HEARTBEAT_UNSUPERVISED_MAX_SECONDS", raising=False)
        import tools.sdlc_lease_heartbeat as hb

        importlib.reload(hb)


class TestHeartbeatObservability:
    """Issue #2714 L4 / spike-4: the log file has been 0 bytes since 2026-08-04.

    ``main()`` only configured logging under ``--verbose`` and every call site
    was ``logger.debug``, so the six observed zombie heartbeats produced zero
    diagnostics. Every decision now emits an assertable INFO record.
    """

    def test_startup_line_names_the_supervisor_source_and_intervals(self, caplog):
        import logging

        from tools.sdlc_lease_heartbeat import run_heartbeat

        caplog.set_level(logging.INFO, logger="tools.sdlc_lease_heartbeat")
        with patch(
            "models.session_lifecycle.touch_issue_lock",
            side_effect=lambda *a, **k: _peek_result(None),
        ):
            run_heartbeat(
                issue_number=2714,
                run_id="mine",
                interval=1,
                max_lifetime=10,
                supervisor_source="env",
                supervisor_pid=4242,
                supervisor_create_time=99.0,
                _sleep=lambda s: None,
            )

        startup = [r.getMessage() for r in caplog.records if "starting" in r.getMessage()]
        assert startup, [r.getMessage() for r in caplog.records]
        assert "env" in startup[0]
        assert "4242" in startup[0]

    def test_lease_lost_and_foreign_owner_exits_are_named(self, caplog):
        import logging

        from tools.sdlc_lease_heartbeat import run_heartbeat

        for owner, reason in [(None, "lease_lost"), ("successor", "foreign_owner")]:
            caplog.clear()
            caplog.set_level(logging.INFO, logger="tools.sdlc_lease_heartbeat")
            with patch(
                "models.session_lifecycle.touch_issue_lock",
                side_effect=lambda *a, _o=owner, **k: _peek_result(_o),
            ):
                rc = run_heartbeat(
                    issue_number=2714,
                    run_id="mine",
                    interval=1,
                    max_lifetime=10,
                    _sleep=lambda s: None,
                )
            assert rc == 0
            assert any(
                reason in r.getMessage() for r in caplog.records if r.levelno >= logging.INFO
            ), reason

    def test_main_configures_info_logging_without_verbose(self):
        """spike-4's root cause: basicConfig ran only under --verbose."""
        import sys

        from tools import sdlc_lease_heartbeat as hb

        argv = ["sdlc_lease_heartbeat", "--issue-number", "2714", "--run-id", "mine"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(hb, "run_heartbeat", return_value=0),
            patch.object(hb.logging, "basicConfig") as basic_config,
            patch.object(hb.sys, "exit"),
        ):
            hb.main()

        basic_config.assert_called_once()
        assert basic_config.call_args.kwargs["level"] == hb.logging.INFO

    def test_cli_passes_none_max_lifetime_so_the_sentinel_is_live(self):
        """Round-2 CONCERN: --max-lifetime must default to None at BOTH ends."""
        import sys

        from tools import sdlc_lease_heartbeat as hb

        argv = ["sdlc_lease_heartbeat", "--issue-number", "2714", "--run-id", "mine"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(hb, "run_heartbeat", return_value=0) as run,
            patch.object(hb.logging, "basicConfig"),
            patch.object(hb.sys, "exit"),
        ):
            hb.main()

        assert run.call_args.kwargs["max_lifetime"] is None

    def test_docstring_no_longer_claims_max_lifetime_is_the_death_backstop(self):
        from tools import sdlc_lease_heartbeat as hb

        assert "max-lifetime is the death backstop" not in (hb.__doc__ or "")
