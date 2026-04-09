"""Unit tests for agent/hibernation.py.

Uses unittest.mock to avoid real Redis or circuit breaker connections.
All tests are synchronous and fast.

NOTE: hibernation.py imports are deferred (inside each function body), so we
can safely patch sys.modules for the heavy dependencies not available in a
unit-test environment.
"""

import asyncio
import os
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers (mirrored from test_sustainability.py pattern)
# ---------------------------------------------------------------------------


def _build_health_stubs(circuit_state_value: str = "closed", anthropic_present: bool = True):
    """Return stubbed sys.modules patches for bridge.health + bridge.resilience."""

    class _CircuitState:
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half_open"

        def __init__(self, v):
            self.value = v

        def __eq__(self, other):
            if isinstance(other, str):
                return self.value == other
            return NotImplemented

    cb = MagicMock()
    cb.state = _CircuitState(circuit_state_value)

    health_mod = types.ModuleType("bridge.health")
    if anthropic_present:
        health_mod.get_health = MagicMock(return_value={"anthropic": cb})
    else:
        health_mod.get_health = MagicMock(return_value={})

    resilience_mod = types.ModuleType("bridge.resilience")
    resilience_mod.CircuitState = _CircuitState

    return health_mod, resilience_mod, cb


# ---------------------------------------------------------------------------
# TestWorkerHealthGate
# ---------------------------------------------------------------------------


class TestWorkerHealthGate(unittest.TestCase):
    def test_circuit_closed_clears_hibernating_writes_recovering(self):
        """Circuit CLOSED + hibernating flag set → delete hibernating, write recovering."""
        import agent.hibernation as hib_mod

        health_mod, resilience_mod, cb = _build_health_stubs("closed")
        r = MagicMock()
        r.exists.return_value = True  # was_hibernating = True

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch("agent.hibernation.send_hibernation_notification") as mock_notif,
            patch.dict(
                "sys.modules",
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            hib_mod.worker_health_gate()

        r.delete.assert_called_once_with("testproj:worker:hibernating")
        r.set.assert_called_once_with("testproj:worker:recovering", "1", ex=3600)
        mock_notif.assert_called_once_with("waking", project_key="testproj")

    def test_circuit_closed_no_previous_hibernation_no_notification(self):
        """Circuit CLOSED + hibernating flag absent → no recovering write, no notification."""
        import agent.hibernation as hib_mod

        health_mod, resilience_mod, cb = _build_health_stubs("closed")
        r = MagicMock()
        r.exists.return_value = False  # was_hibernating = False

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch("agent.hibernation.send_hibernation_notification") as mock_notif,
            patch.dict(
                "sys.modules",
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            hib_mod.worker_health_gate()

        r.delete.assert_called_once_with("testproj:worker:hibernating")
        r.set.assert_not_called()
        mock_notif.assert_not_called()

    def test_circuit_open_writes_hibernating_flag(self):
        """Circuit OPEN → renew worker:hibernating flag (TTL 600s)."""
        import agent.hibernation as hib_mod

        health_mod, resilience_mod, cb = _build_health_stubs("open")
        r = MagicMock()
        r.exists.return_value = False  # first time

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch.dict(
                "sys.modules",
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            hib_mod.worker_health_gate()

        r.set.assert_called_once_with("testproj:worker:hibernating", "1", ex=600)

    def test_circuit_half_open_renews_hibernating_flag(self):
        """Circuit HALF_OPEN → renew worker:hibernating flag (still not recovered)."""
        import agent.hibernation as hib_mod

        health_mod, resilience_mod, cb = _build_health_stubs("half_open")
        r = MagicMock()
        r.exists.return_value = True  # already hibernating

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch.dict(
                "sys.modules",
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            hib_mod.worker_health_gate()

        r.set.assert_called_once_with("testproj:worker:hibernating", "1", ex=600)

    def test_no_anthropic_circuit_is_no_op(self):
        """Anthropic circuit not registered → function returns early, no Redis writes."""
        import agent.hibernation as hib_mod

        health_mod, resilience_mod, cb = _build_health_stubs(anthropic_present=False)
        r = MagicMock()

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch.dict(
                "sys.modules",
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            hib_mod.worker_health_gate()

        r.get.assert_not_called()
        r.set.assert_not_called()
        r.delete.assert_not_called()

    def test_exception_does_not_propagate(self):
        """Any unhandled exception is caught; function does not raise."""
        import agent.hibernation as hib_mod

        health_mod, resilience_mod, cb = _build_health_stubs("closed")

        with (
            patch("agent.hibernation._get_redis", side_effect=RuntimeError("redis down")),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch.dict(
                "sys.modules",
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            # Should not raise
            hib_mod.worker_health_gate()


# ---------------------------------------------------------------------------
# TestSessionResumeDrip
# ---------------------------------------------------------------------------


class TestSessionResumeDrip(unittest.TestCase):
    def test_drip_transitions_oldest_paused_session(self):
        """recovering flag set + one paused session → transition to pending."""
        import agent.hibernation as hib_mod
        import models.agent_session as asm
        import models.session_lifecycle as lm

        r = MagicMock()
        r.exists.return_value = True

        mock_session = MagicMock()
        mock_session.session_id = "sess-001"
        mock_session.created_at = 1000.0

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
            patch.object(lm, "transition_status") as mock_transition,
        ):
            mock_query.filter.return_value = [mock_session]
            hib_mod.session_resume_drip()

        mock_transition.assert_called_once_with(
            mock_session,
            "pending",
            reason="session-resume-drip: worker recovered",
        )
        r.delete.assert_not_called()

    def test_empty_paused_queue_clears_recovering_flag(self):
        """recovering flag set + no paused sessions → delete recovering flag."""
        import agent.hibernation as hib_mod
        import models.agent_session as asm

        r = MagicMock()
        r.exists.return_value = True

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
        ):
            mock_query.filter.return_value = []
            hib_mod.session_resume_drip()

        r.delete.assert_called_once_with("testproj:worker:recovering")

    def test_no_op_when_recovering_flag_absent(self):
        """worker:recovering flag not set → no sessions modified."""
        import agent.hibernation as hib_mod
        import models.session_lifecycle as lm

        r = MagicMock()
        r.exists.return_value = False

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch.object(lm, "transition_status") as mock_transition,
        ):
            hib_mod.session_resume_drip()

        mock_transition.assert_not_called()

    def test_only_one_session_per_tick(self):
        """recovering flag set + two paused sessions → only one transitioned per tick."""
        import agent.hibernation as hib_mod
        import models.agent_session as asm
        import models.session_lifecycle as lm

        r = MagicMock()
        r.exists.return_value = True

        session_older = MagicMock()
        session_older.session_id = "sess-old"
        session_older.created_at = 500.0

        session_newer = MagicMock()
        session_newer.session_id = "sess-new"
        session_newer.created_at = 1000.0

        with (
            patch("agent.hibernation._get_redis", return_value=r),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
            patch.object(lm, "transition_status") as mock_transition,
        ):
            mock_query.filter.return_value = [session_newer, session_older]
            hib_mod.session_resume_drip()

        # Should drip the oldest (session_older), not both
        assert mock_transition.call_count == 1
        assert mock_transition.call_args[0][0] is session_older

    def test_exception_does_not_propagate(self):
        """Any unhandled exception is caught; function does not raise."""
        import agent.hibernation as hib_mod

        with (
            patch("agent.hibernation._get_redis", side_effect=RuntimeError("redis down")),
            patch("agent.hibernation._get_project_key", return_value="testproj"),
        ):
            # Should not raise
            hib_mod.session_resume_drip()


# ---------------------------------------------------------------------------
# TestHibernationFlagBlocksPop (integration between flag and _pop_agent_session)
# ---------------------------------------------------------------------------


class TestHibernationFlagBlocksPop(unittest.TestCase):
    def test_hibernating_flag_blocks_pop(self):
        """worker:hibernating flag set → _pop_agent_session returns None."""
        import popoto.redis_db as prd

        from agent.agent_session_queue import _pop_agent_session

        fake_redis = MagicMock()

        def fake_get(key):
            if "hibernating" in key:
                return b"1"
            return None

        fake_redis.get.side_effect = fake_get

        with (
            patch.object(prd, "POPOTO_REDIS_DB", fake_redis),
            patch.dict(os.environ, {"VALOR_PROJECT_KEY": "testproj"}),
        ):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    _pop_agent_session("testproj:pending_sessions", True)
                )
            finally:
                loop.close()

        self.assertIsNone(result)

    def test_no_hibernating_flag_allows_pop(self):
        """worker:hibernating flag absent → _pop_agent_session proceeds past guard."""
        import popoto.redis_db as prd

        from agent.agent_session_queue import _pop_agent_session

        fake_redis = MagicMock()

        # No flags set — all gets return None
        fake_redis.get.return_value = None

        with (
            patch.object(prd, "POPOTO_REDIS_DB", fake_redis),
            patch.dict(os.environ, {"VALOR_PROJECT_KEY": "testproj"}),
        ):
            # The pop will proceed past guard but fail at lock/query stage — that's OK.
            # We just verify it doesn't short-circuit at the hibernation guard.
            loop = asyncio.new_event_loop()
            try:
                # Will return None from lock acquisition or empty queue — not from guard
                loop.run_until_complete(_pop_agent_session("testproj:pending_sessions", True))
            except Exception:
                pass  # Any downstream error is fine — guard was not the blocker
            finally:
                loop.close()

        # Verify the guard keys were checked (get was called at all)
        fake_redis.get.assert_called()


# ---------------------------------------------------------------------------
# TestPausedStatusInLifecycle
# ---------------------------------------------------------------------------


class TestPausedStatusInLifecycle(unittest.TestCase):
    def test_paused_in_non_terminal_statuses(self):
        """'paused' must be in NON_TERMINAL_STATUSES."""
        from models.session_lifecycle import NON_TERMINAL_STATUSES

        assert "paused" in NON_TERMINAL_STATUSES

    def test_paused_not_in_terminal_statuses(self):
        """'paused' must not be in TERMINAL_STATUSES."""
        from models.session_lifecycle import TERMINAL_STATUSES

        assert "paused" not in TERMINAL_STATUSES

    def test_transition_status_accepts_paused(self):
        """transition_status(session, 'paused') must not raise ValueError."""
        from models.session_lifecycle import transition_status

        mock_session = MagicMock()
        mock_session.status = "running"
        mock_session._saved_field_values = {"status": "running"}

        # Should not raise
        transition_status(mock_session, "paused", reason="test")

        mock_session.save.assert_called()


# ---------------------------------------------------------------------------
# TestReflectionsRegistered
# ---------------------------------------------------------------------------


class TestReflectionsRegistered(unittest.TestCase):
    def test_worker_health_gate_registered(self):
        """worker-health-gate must be present in the reflection registry."""
        from agent.reflection_scheduler import load_registry

        registry = load_registry()
        names = [e.name for e in registry]
        assert "worker-health-gate" in names, f"worker-health-gate not found in: {names}"

    def test_session_resume_drip_registered(self):
        """session-resume-drip must be present in the reflection registry."""
        from agent.reflection_scheduler import load_registry

        registry = load_registry()
        names = [e.name for e in registry]
        assert "session-resume-drip" in names, f"session-resume-drip not found in: {names}"


if __name__ == "__main__":
    unittest.main()
