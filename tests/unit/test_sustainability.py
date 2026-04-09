"""Unit tests for agent/sustainability.py.

Uses unittest.mock to avoid real Redis or circuit breaker connections.
All tests are synchronous and fast.

NOTE: sustainability.py imports are deferred (inside each function body), so we
can safely patch sys.modules for the heavy dependencies that are not available
in a unit-test environment.  We do NOT pre-install stub packages at module level
because that collides with the real `bridge` package imported by agent/__init__.py.
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_circuit(state_value: str):
    """Return a fake circuit-breaker object with .state.value = state_value."""
    cb = MagicMock()
    cb.state = MagicMock()
    cb.state.value = state_value
    # Equality with a plain string (used in sustainability.py comparisons)
    cb.state.__eq__ = lambda self, other: self.value == other
    return cb


def _circuit_state_closed():
    cs = MagicMock()
    cs.value = "closed"
    cs.CLOSED = cs  # CircuitState.CLOSED == cs
    cs.__eq__ = lambda self, other: self is other
    return cs


# ---------------------------------------------------------------------------
# Shared patch context: minimal stubs for bridge.health / bridge.resilience
# so we can import api_health_gate without the real bridge package.
# ---------------------------------------------------------------------------


def _build_health_stubs(circuit_state_value: str = "closed", anthropic_present: bool = True):
    """Return a dict of sys.modules patches for bridge.health + bridge.resilience."""

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
# TestApiHealthGate
# ---------------------------------------------------------------------------


class TestApiHealthGate(unittest.TestCase):
    def _run(self, circuit_state: str, was_paused: bool, anthropic_present: bool = True):
        """Run api_health_gate with mocked dependencies, return the redis mock."""
        from agent.sustainability import api_health_gate

        health_mod, resilience_mod, _cb = _build_health_stubs(circuit_state, anthropic_present)

        r = MagicMock()
        r.exists.return_value = int(was_paused)

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.dict(
                sys.modules,
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            api_health_gate()

        return r

    def test_open_circuit_sets_queue_paused(self):
        """OPEN circuit, not previously paused → queue_paused key written."""
        r = self._run("open", was_paused=False)
        r.set.assert_called_once_with("testproj:sustainability:queue_paused", "1", ex=3600)

    def test_closed_circuit_clears_pause_and_sets_recovery(self):
        """CLOSED circuit after OPEN → deletes pause key, sets recovery:active."""
        r = self._run("closed", was_paused=True)
        r.delete.assert_called_once_with("testproj:sustainability:queue_paused")
        r.set.assert_called_once_with("testproj:recovery:active", "1", ex=3600)

    def test_closed_circuit_no_recovery_if_was_not_paused(self):
        """CLOSED circuit when not previously paused → no recovery:active set."""
        r = self._run("closed", was_paused=False)
        r.delete.assert_called_once_with("testproj:sustainability:queue_paused")
        r.set.assert_not_called()

    def test_unregistered_circuit_returns_without_error(self):
        """get_health() returns nothing for 'anthropic' → silent no-op."""
        r = self._run("closed", was_paused=False, anthropic_present=False)
        r.set.assert_not_called()
        r.delete.assert_not_called()


# ---------------------------------------------------------------------------
# TestSessionCountThrottle
# ---------------------------------------------------------------------------


class TestSessionCountThrottle(unittest.TestCase):
    def _make_session(self, started_at_offset: float = -60.0):
        """Return a fake AgentSession with started_at set relative to now."""
        import time as _time

        s = MagicMock()
        s.started_at = _time.time() + started_at_offset
        return s

    def _run_throttle(self, session_count: int, moderate: int = 20, suspended: int = 40):
        """Run session_count_throttle with `session_count` recent sessions."""
        import models.agent_session as asm
        from agent.sustainability import session_count_throttle

        r = MagicMock()
        sessions = [self._make_session() for _ in range(session_count)]

        env_patch = {
            "SUSTAINABILITY_THROTTLE_MODERATE": str(moderate),
            "SUSTAINABILITY_THROTTLE_SUSPENDED": str(suspended),
            "VALOR_PROJECT_KEY": "testproj",
        }

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
            patch.dict(os.environ, env_patch),
        ):
            mock_query.filter.return_value = sessions
            session_count_throttle()

        return r

    def test_below_threshold_does_not_set_throttle_flag(self):
        """Session count below moderate threshold → throttle_level written as 'none'."""
        r = self._run_throttle(session_count=5, moderate=20, suspended=40)
        r.set.assert_called_once_with("testproj:sustainability:throttle_level", "none", ex=7200)

    def test_at_moderate_threshold_sets_moderate_flag(self):
        """Session count == moderate threshold → throttle_level written as 'moderate'."""
        r = self._run_throttle(session_count=20, moderate=20, suspended=40)
        r.set.assert_called_once_with("testproj:sustainability:throttle_level", "moderate", ex=7200)

    def test_at_suspended_threshold_sets_suspended_flag(self):
        """Session count == suspended threshold → throttle_level written as 'suspended'."""
        r = self._run_throttle(session_count=40, moderate=20, suspended=40)
        r.set.assert_called_once_with(
            "testproj:sustainability:throttle_level", "suspended", ex=7200
        )


# ---------------------------------------------------------------------------
# TestPopAgentSessionGuard
# ---------------------------------------------------------------------------


class TestPopAgentSessionGuard(unittest.TestCase):
    """Tests that _pop_agent_session returns None when queue_paused is set."""

    def test_queue_paused_returns_none(self):
        """When queue_paused Redis key exists, _pop_agent_session returns None."""
        import asyncio

        import popoto.redis_db as prd

        from agent.agent_session_queue import _pop_agent_session

        fake_redis = MagicMock()

        # queue_paused key → truthy; throttle key → None
        def fake_get(key):
            if "queue_paused" in key:
                return b"1"
            return None

        fake_redis.get.side_effect = fake_get

        # _pop_agent_session does `from popoto.redis_db import POPOTO_REDIS_DB as _r`
        # inside the function body — patch the module attribute directly.
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


# ---------------------------------------------------------------------------
# TestRecoveryDrip
# ---------------------------------------------------------------------------


class TestRecoveryDrip(unittest.TestCase):
    def test_drip_transitions_oldest_paused_session(self):
        """recovery:active set + one paused_circuit session → transition to pending."""
        import models.agent_session as asm
        import models.session_lifecycle as lm
        from agent.sustainability import recovery_drip

        r = MagicMock()
        r.exists.return_value = True

        mock_session = MagicMock()
        mock_session.session_id = "sess-001"
        mock_session.created_at = 1000.0

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
            patch.object(lm, "transition_status") as mock_transition,
        ):
            mock_query.filter.return_value = [mock_session]
            recovery_drip()

        mock_transition.assert_called_once_with(
            mock_session,
            "pending",
            reason="recovery-drip: API circuit recovered",
        )

    def test_no_op_when_recovery_flag_absent(self):
        """recovery:active flag not set → no sessions modified."""
        import models.session_lifecycle as lm
        from agent.sustainability import recovery_drip

        r = MagicMock()
        r.exists.return_value = False

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(lm, "transition_status") as mock_transition,
        ):
            recovery_drip()

        mock_transition.assert_not_called()

    def test_clears_recovery_flag_when_queue_empty(self):
        """recovery:active set but no paused sessions → clears the flag."""
        import models.agent_session as asm
        from agent.sustainability import recovery_drip

        r = MagicMock()
        r.exists.return_value = True

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
        ):
            mock_query.filter.return_value = []
            recovery_drip()

        r.delete.assert_called_once_with("testproj:recovery:active")


# ---------------------------------------------------------------------------
# TestFailureLoopDetector
# ---------------------------------------------------------------------------


class TestFailureLoopDetector(unittest.TestCase):
    def _make_failed_session(self, error_msg: str, session_id: str):
        s = MagicMock()
        s.session_id = session_id
        s.status = "failed"
        s.created_at = 1000.0
        # completed_at as a float timestamp (recent — within 4h window)
        import time as _time

        s.completed_at = _time.time() - 60  # 1 minute ago
        s.extra_context = {"error_message": error_msg}
        s.failed_reason = error_msg
        return s

    @patch("agent.sustainability.subprocess.run")
    def test_three_same_fingerprint_failures_files_issue(self, mock_subprocess):
        """Three sessions with same fingerprint → gh issue filed, fingerprint added to Redis."""
        import models.agent_session as asm
        from agent.sustainability import failure_loop_detector

        r = MagicMock()
        r.exists.return_value = False  # queue not paused
        r.sadd.return_value = 1  # fingerprint is new (not previously seen)
        r.ttl.return_value = 86400  # TTL already set

        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="https://github.com/issue/1", stderr=""
        )

        sessions = [self._make_failed_session("conn_timeout", f"s{i}") for i in range(3)]

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
        ):
            mock_query.filter.return_value = sessions
            failure_loop_detector()

        # sadd should have been called with the seen_fingerprints key
        r.sadd.assert_called_once()
        sadd_args = r.sadd.call_args[0]
        self.assertIn("testproj:sustainability:seen_fingerprints", sadd_args)

        # gh issue create should have been called
        mock_subprocess.assert_called_once()
        args = mock_subprocess.call_args[0][0]
        self.assertIn("gh", args)
        self.assertIn("issue", args)
        self.assertIn("create", args)

    @patch("agent.sustainability.subprocess.run")
    def test_already_seen_fingerprint_does_not_file_duplicate(self, mock_subprocess):
        """Fingerprint already in Redis seen set → no duplicate issue filed."""
        import models.agent_session as asm
        from agent.sustainability import failure_loop_detector

        r = MagicMock()
        r.exists.return_value = False  # queue not paused
        r.sadd.return_value = 0  # fingerprint already in set (sadd returns 0 = not added)

        sessions = [self._make_failed_session("conn_timeout", f"s{i}") for i in range(3)]

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
        ):
            mock_query.filter.return_value = sessions
            failure_loop_detector()

        mock_subprocess.assert_not_called()

    @patch("agent.sustainability.subprocess.run")
    def test_fewer_than_three_failures_no_issue(self, mock_subprocess):
        """Only two sessions with same fingerprint → no issue filed."""
        import models.agent_session as asm
        from agent.sustainability import failure_loop_detector

        r = MagicMock()
        r.exists.return_value = False
        r.sismember.return_value = False

        sessions = [self._make_failed_session("conn_timeout", f"s{i}") for i in range(2)]

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
        ):
            mock_query.filter.return_value = sessions
            failure_loop_detector()

        mock_subprocess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
