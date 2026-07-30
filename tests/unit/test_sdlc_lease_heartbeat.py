"""Unit tests for tools.sdlc_lease_heartbeat (issue #2446/#2451).

The heartbeat is PEEK-FIRST, RENEW-ONLY (BLOCKER 1): it must NEVER call the
mutating ``touch_issue_lock`` unless a peek first confirms the lease is owned by
its own run_id. This closes the lease-theft hole (Risk 2) where a zombie
heartbeat re-acquires a lapsed lease under a stale id and blocks a successor.

Tests cover:
- terminate-on-foreign-owner (a successor owns the lease -> exit, no renew)
- exit-immediately-on-no-ownership (lease absent/lapsed -> exit, never mints)
- renew-on-self-owner (self owns -> mutating renew fires, then bounded exit)
"""

from __future__ import annotations

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
            # peek with run_id=None returns the current (absent) owner.
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
        # Exactly one call -- the peek (run_id=None, peek=True). No mutating call.
        assert calls == [(None, True)]
        assert not any(run_id is not None for run_id, _ in calls)
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
        assert calls == [(None, True)]  # peek only, then exit
        assert all(run_id is None for run_id, _ in calls)

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
