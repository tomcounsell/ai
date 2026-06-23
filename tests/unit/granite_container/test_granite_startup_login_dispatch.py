"""Container-level tests for BYOB ``/login`` recovery dispatch (issue #1750).

These tests use the ``Container._recover_login`` injection seam: a fake
``recover_login`` is set on the instance so NO browser / subprocess is touched.
PTYs are MagicMock(spec=PTYDriver) stand-ins per the existing
``test_container.py`` conventions.

Coverage:
  * Non-blocking thread dispatch — ``_dispatch_login_recovery`` returns fast.
  * Idempotency — a persisting LOGIN_PROMPT spawns recovery EXACTLY once.
  * B1 — plateau early-bail is suppressed while recovery is in flight.
  * B2 — PTY attribution: PM-side vs Dev-side login frame routes ``_login_pty``.
  * C1 — the recovery thread closes the client even on an early loop exit.
  * Failure → degradation: failed recovery falls through to ``startup_unresolved``.
  * Observability — a ``login_recovery`` session_event is recorded on BOTH
    success and failure, with stable field names.
"""

from __future__ import annotations

import threading
import time
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from agent.granite_container import container as container_mod
from agent.granite_container.byob_relogin import ReloginOutcome
from agent.granite_container.container import Container
from agent.granite_container.pty_driver import IdleResult, PTYDriver

LOGIN_FRAME = "Select login method\n❯ 1. Claude account with subscription"


def _idle_result(buffer_text: str, saw_idle: bool) -> IdleResult:
    return IdleResult(
        saw_idle=saw_idle,
        buffer=buffer_text,
        idle_marker="bypass permissions on" if saw_idle else "",
        elapsed_ms=1,
        turn_buffer=buffer_text,
    )


def _mock_pty(buffer_text: str, saw_idle: bool, session_id: str) -> MagicMock:
    m = MagicMock(spec=PTYDriver)
    m.read_until_idle.return_value = _idle_result(buffer_text, saw_idle)
    m.last_resume_uuid.return_value = None
    m.isalive.return_value = True
    m._session_id = session_id
    return m


# ==============================================================================
# B2 — PTY attribution (direct _handle_startup)
# ==============================================================================


class TestLoginPtyAttribution(unittest.TestCase):
    def _container(self) -> Container:
        c = Container(user_message="hello", max_turns=1)
        c._pm_pty = _mock_pty("idle", True, "mock-session-pm")
        c._dev_pty = _mock_pty("idle", True, "mock-session-dev")
        return c

    def test_login_on_pm_routes_to_pm_pty(self) -> None:
        c = self._container()
        c._handle_startup(LOGIN_FRAME, "ordinary dev output")
        self.assertIs(c._login_pty, c._pm_pty)
        self.assertIn("Select login method", c._login_pty_buffer)

    def test_login_on_dev_routes_to_dev_pty(self) -> None:
        c = self._container()
        c._handle_startup("ordinary pm output", LOGIN_FRAME)
        self.assertIs(c._login_pty, c._dev_pty)
        self.assertIn("Select login method", c._login_pty_buffer)

    def test_no_login_leaves_login_pty_unset(self) -> None:
        c = self._container()
        c._handle_startup("normal pm", "normal dev")
        self.assertIsNone(c._login_pty)


# ==============================================================================
# Non-blocking dispatch + idempotency + C1 finally-close (direct dispatch)
# ==============================================================================


class TestDispatchThread(unittest.TestCase):
    def _container_with_login(self) -> Container:
        c = Container(user_message="hello", max_turns=1)
        c._pm_pty = _mock_pty(LOGIN_FRAME, False, "mock-session-pm")
        c._dev_pty = _mock_pty("idle", True, "mock-session-dev")
        c._login_pty = c._pm_pty
        c._login_pty_buffer = LOGIN_FRAME
        return c

    def test_dispatch_is_non_blocking(self) -> None:
        """_dispatch_login_recovery returns fast; recovery runs on a daemon thread."""
        release = threading.Event()
        started = threading.Event()

        def _slow_recover(*args: Any, **kwargs: Any) -> ReloginOutcome:
            started.set()
            release.wait(timeout=5)
            return ReloginOutcome(succeeded=True, flow=1, reason="ok")

        c = self._container_with_login()
        c._recover_login = _slow_recover

        t0 = time.monotonic()
        c._dispatch_login_recovery(deadline=time.monotonic() + 600)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.5, "dispatch must not block on the recovery call")
        self.assertTrue(c._recovery_launched)
        # The worker thread is running and is a daemon.
        self.assertTrue(started.wait(timeout=2))
        threads = [t for t in threading.enumerate() if t.name == "granite-login-recovery"]
        self.assertTrue(threads)
        self.assertTrue(all(t.daemon for t in threads))
        release.set()
        # Outcome publishes and the done event sets.
        self.assertTrue(c._recovery_done.wait(timeout=2))
        self.assertTrue(c._recovery_outcome.succeeded)

    def test_recovery_call_receives_correct_login_pty(self) -> None:
        """The recover call gets the captured _login_pty + its buffer."""
        captured: dict[str, Any] = {}

        def _recover(login_pty: Any, login_pty_buffer: str, **kwargs: Any) -> ReloginOutcome:
            captured["pty"] = login_pty
            captured["buffer"] = login_pty_buffer
            captured["expected_identity"] = kwargs.get("expected_identity")
            return ReloginOutcome(succeeded=True, flow=1, reason="ok")

        c = self._container_with_login()
        c._recover_login = _recover
        c._dispatch_login_recovery(deadline=time.monotonic() + 600)
        self.assertTrue(c._recovery_done.wait(timeout=2))
        self.assertIs(captured["pty"], c._pm_pty)
        self.assertEqual(captured["buffer"], LOGIN_FRAME)


class TestIdempotency(unittest.TestCase):
    def test_persisting_login_spawns_recovery_once(self) -> None:
        """A LOGIN_PROMPT across multiple startup cycles dispatches exactly once."""
        call_count = {"n": 0}

        def _recover(*args: Any, **kwargs: Any) -> ReloginOutcome:
            call_count["n"] += 1
            return ReloginOutcome(succeeded=False, flow=None, reason="failed once")

        c = Container(user_message="hello", max_turns=1)
        c._pm_pty = _mock_pty(LOGIN_FRAME, False, "mock-session-pm")
        c._dev_pty = _mock_pty("idle", True, "mock-session-dev")
        c._recover_login = _recover

        # Drive several startup cycles by hand: detect → dispatch (once).
        for _ in range(5):
            c._handle_startup(LOGIN_FRAME, "idle")
            if c._login_pty is not None and not c._recovery_launched:
                c._dispatch_login_recovery(deadline=time.monotonic() + 600)
        # Let the single thread finish.
        self.assertTrue(c._recovery_done.wait(timeout=2))
        self.assertEqual(call_count["n"], 1, "recovery must be dispatched exactly once")


class TestFinallyClose(unittest.TestCase):
    def test_real_recover_login_closes_client_on_early_exit(self) -> None:
        """C1: recover_login's finally closes the BYOBClient even if the client
        fails to start (the loop may have already returned)."""
        closed = {"n": 0}

        class _RecordingClient:
            def start(self) -> bool:
                return False

            def close(self) -> None:
                closed["n"] += 1

        c = Container(user_message="hello", max_turns=1)
        c._pm_pty = _mock_pty(LOGIN_FRAME, False, "mock-session-pm")
        c._dev_pty = _mock_pty("idle", True, "mock-session-dev")
        c._login_pty = c._pm_pty
        c._login_pty_buffer = LOGIN_FRAME
        # Use the REAL recover_login (default seam) but stub the BYOBClient it
        # constructs so no subprocess spawns; assert finally-close fires.
        with patch.object(container_mod, "recover_login", container_mod.recover_login):
            with patch(
                "agent.granite_container.byob_relogin.BYOBClient",
                lambda: _RecordingClient(),
            ):
                c._recover_login = container_mod.recover_login
                c._dispatch_login_recovery(deadline=time.monotonic() + 600)
                self.assertTrue(c._recovery_done.wait(timeout=3))
        self.assertEqual(closed["n"], 1, "BYOBClient.close must run in finally")
        self.assertFalse(c._recovery_outcome.succeeded)


# ==============================================================================
# Full-loop integration: B1, failure degradation, observability, success
# ==============================================================================


def _build_login_container(recover_fn: Any) -> Container:
    """A container whose PM paints the login frame and whose Dev is busy.

    BOTH PTYs are non-idle so the loop's ``_silent_start`` sentinel (response
    is None AND neither PTY idle) holds — the precondition for the plateau
    detector. Dev's buffer is benign (no startup event), so the chosen startup
    event is the PM's LOGIN_PROMPT.
    """
    c = Container(user_message="hello", max_turns=1)
    c._pm_pty = _mock_pty(LOGIN_FRAME, False, "mock-session-pm")
    c._dev_pty = _mock_pty("dev still working...", False, "mock-session-dev")
    c._recover_login = recover_fn
    return c


def _run_startup_only(c: Container):
    """Run the container, short-circuiting priming and teardown so the test
    exercises only the startup loop. Returns the ContainerResult."""
    with (
        patch.object(c, "_spawn_pair"),
        patch.object(c, "_close_pair"),
        patch.object(c, "_prime_session"),
        patch.object(c, "_run_pkill_fallback"),
    ):
        return c.run()


class TestPlateauSuppressionB1(unittest.TestCase):
    def test_recovery_in_flight_suppresses_plateau_bail(self) -> None:
        """B1: while recovery is in flight (not done), >= STARTUP_PLATEAU_CYCLES
        no-progress cycles must NOT bail to startup_unresolved prematurely."""
        release = threading.Event()
        completed = threading.Event()

        def _blocking_recover(*args: Any, **kwargs: Any) -> ReloginOutcome:
            # Block past the plateau ceiling worth of cycles. If the loop bailed
            # early (B1 broken), run() returns while we are still blocked here.
            release.wait(timeout=10)
            completed.set()
            return ReloginOutcome(succeeded=False, flow=None, reason="released")

        c = _build_login_container(_blocking_recover)

        # Spin many no-progress cycles before releasing recovery. With the
        # plateau ceiling at 3 and the cycle timeout at 0, the loop will have
        # cycled FAR more than 3 times by the time we release — if the bail were
        # not suppressed, it would have returned long before `completed` is set.
        def _releaser() -> None:
            time.sleep(0.6)
            release.set()

        with (
            patch.object(container_mod, "STARTUP_CYCLE_TIMEOUT_S", 0.0),
            patch.object(container_mod, "STARTUP_PLATEAU_CYCLES", 3),
        ):
            threading.Thread(target=_releaser, daemon=True).start()
            result = _run_startup_only(c)

        # The loop must NOT have returned before recovery completed: the run only
        # returns after `release` is set (recovery finishes → plateau no longer
        # suppressed → legitimate bail). If B1 were broken the run would return
        # while recovery is still blocked and `completed` would be False here.
        self.assertTrue(
            completed.is_set(),
            "recovery was reaped mid-flight — B1 plateau suppression is broken",
        )
        self.assertEqual(result.exit_reason, "startup_unresolved")


class TestFailureDegradation(unittest.TestCase):
    def test_failed_recovery_degrades_to_startup_unresolved(self) -> None:
        """A recovery that always fails → loop falls through to the ceiling/alert
        path (startup_unresolved)."""

        def _failing_recover(*args: Any, **kwargs: Any) -> ReloginOutcome:
            return ReloginOutcome(succeeded=False, flow=None, reason="byob unavailable")

        c = _build_login_container(_failing_recover)
        with (
            patch.object(container_mod, "STARTUP_CYCLE_TIMEOUT_S", 0.0),
            patch.object(container_mod, "STARTUP_PLATEAU_CYCLES", 3),
        ):
            result = _run_startup_only(c)
        self.assertEqual(result.exit_reason, "startup_unresolved")


class TestObservability(unittest.TestCase):
    def _drive_and_get_event(self, recover_fn: Any) -> dict[str, Any]:
        c = _build_login_container(recover_fn)
        with (
            patch.object(container_mod, "STARTUP_CYCLE_TIMEOUT_S", 0.0),
            patch.object(container_mod, "STARTUP_PLATEAU_CYCLES", 3),
        ):
            result = _run_startup_only(c)
        events = [e for e in result.startup_events if e.get("event") == "login_recovery"]
        self.assertEqual(len(events), 1, f"expected one login_recovery event, got {events}")
        return events[0]

    def test_failure_event_shape(self) -> None:
        def _fail(*a: Any, **k: Any) -> ReloginOutcome:
            return ReloginOutcome(succeeded=False, flow=None, reason="byob unavailable")

        event = self._drive_and_get_event(_fail)
        self.assertEqual(event["event"], "login_recovery")
        self.assertEqual(event["outcome"], "failed")
        self.assertIsNone(event["flow"])
        self.assertEqual(event["reason"], "byob unavailable")

    def test_success_event_shape(self) -> None:
        # A success outcome that does NOT settle the PTY still records the event;
        # we only assert the event shape (settle is the real claude's job).
        def _succeed(*a: Any, **k: Any) -> ReloginOutcome:
            return ReloginOutcome(succeeded=True, flow=2, reason="paste fallback login recovered")

        event = self._drive_and_get_event(_succeed)
        self.assertEqual(event["event"], "login_recovery")
        self.assertEqual(event["outcome"], "success")
        self.assertEqual(event["flow"], 2)
        self.assertEqual(event["reason"], "paste fallback login recovered")


if __name__ == "__main__":
    unittest.main(verbosity=2)
