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
# so we can import callables without the real bridge package.
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
# TestCircuitHealthGate
# ---------------------------------------------------------------------------


class TestCircuitHealthGate(unittest.TestCase):
    def _run(
        self,
        circuit_state: str,
        was_paused: bool,
        was_hibernating: bool,
        anthropic_present: bool = True,
    ):
        """Run circuit_health_gate with mocked dependencies, return the redis mock."""
        from agent.sustainability import circuit_health_gate

        health_mod, resilience_mod, _cb = _build_health_stubs(circuit_state, anthropic_present)

        r = MagicMock()
        # exists() is called twice: once for pause_key, once for hib_key
        r.exists.side_effect = [int(was_paused), int(was_hibernating)]

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch("agent.sustainability.send_hibernation_notification"),
            patch.dict(
                sys.modules,
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            circuit_health_gate()

        return r

    def test_open_circuit_sets_both_flags(self):
        """OPEN circuit → sets queue_paused (TTL 3600s) AND worker:hibernating (TTL 600s)."""
        r = self._run("open", was_paused=False, was_hibernating=False)
        assert r.set.call_count == 2
        set_calls = r.set.call_args_list
        assert set_calls[0].args == ("testproj:sustainability:queue_paused", "1")
        assert set_calls[0].kwargs == {"ex": 3600}
        assert set_calls[1].args == ("testproj:worker:hibernating", "1")
        assert set_calls[1].kwargs == {"ex": 600}

    def test_half_open_circuit_renews_both_flags(self):
        """HALF_OPEN circuit → renews both flags even when already set."""
        r = self._run("half_open", was_paused=True, was_hibernating=True)
        assert r.set.call_count == 2

    def test_closed_circuit_deletes_both_flags_and_sets_recovery(self):
        """CLOSED circuit after OPEN → deletes both flags, sets both recovery keys."""
        from agent.sustainability import circuit_health_gate

        health_mod, resilience_mod, _cb = _build_health_stubs("closed")

        r = MagicMock()
        r.exists.side_effect = [1, 1]  # was_paused=True, was_hibernating=True

        notif_mock = MagicMock()

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch("agent.sustainability.send_hibernation_notification", notif_mock),
            patch.dict(
                sys.modules,
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            circuit_health_gate()

        delete_calls = [c.args[0] for c in r.delete.call_args_list]
        assert "testproj:sustainability:queue_paused" in delete_calls
        assert "testproj:worker:hibernating" in delete_calls
        set_keys = [c.args[0] for c in r.set.call_args_list]
        assert "testproj:recovery:active" in set_keys
        assert "testproj:worker:recovering" in set_keys
        notif_mock.assert_called_once_with("waking", project_key="testproj")

    def test_closed_circuit_no_recovery_if_neither_flag_was_set(self):
        """CLOSED circuit when neither flag was set → no recovery keys, no notification."""
        from agent.sustainability import circuit_health_gate

        health_mod, resilience_mod, _cb = _build_health_stubs("closed")

        r = MagicMock()
        r.exists.side_effect = [0, 0]  # neither flag was set

        notif_mock = MagicMock()

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch("agent.sustainability.send_hibernation_notification", notif_mock),
            patch.dict(
                sys.modules,
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            circuit_health_gate()

        r.set.assert_not_called()
        notif_mock.assert_not_called()

    def test_unregistered_circuit_returns_without_error(self):
        """get_health() returns nothing for 'anthropic' → silent no-op."""
        r = self._run("closed", was_paused=False, was_hibernating=False, anthropic_present=False)
        r.set.assert_not_called()
        r.delete.assert_not_called()

    def test_exception_does_not_propagate(self):
        """Any unhandled exception is caught; function does not raise."""
        from agent.sustainability import circuit_health_gate

        with patch("agent.sustainability._get_redis", side_effect=RuntimeError("redis down")):
            circuit_health_gate()  # Should not raise

    def test_closed_circuit_only_pause_flag_was_set(self):
        """CLOSED circuit: only queue_paused was set → still triggers recovery and notification."""
        from agent.sustainability import circuit_health_gate

        health_mod, resilience_mod, _cb = _build_health_stubs("closed")

        r = MagicMock()
        r.exists.side_effect = [1, 0]  # was_paused=True, was_hibernating=False

        notif_mock = MagicMock()

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch("agent.sustainability.send_hibernation_notification", notif_mock),
            patch.dict(
                sys.modules,
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            circuit_health_gate()

        set_keys = [c.args[0] for c in r.set.call_args_list]
        assert "testproj:recovery:active" in set_keys
        notif_mock.assert_called_once()


# ---------------------------------------------------------------------------
# TestSessionRecoveryDrip
# ---------------------------------------------------------------------------


class TestSessionRecoveryDrip(unittest.TestCase):
    def test_drip_paused_circuit_session_first(self):
        """paused_circuit session exists alongside paused session → paused_circuit dripped first."""
        import models.agent_session as asm
        import models.session_lifecycle as lm
        from agent.sustainability import session_recovery_drip

        r = MagicMock()
        # recovery:active is set, worker:recovering is not
        r.exists.side_effect = [True, False]

        circuit_session = MagicMock()
        circuit_session.session_id = "sess-circuit"
        circuit_session.agent_session_id = "agent-circuit"
        circuit_session.created_at = 1000.0

        paused_session = MagicMock()
        paused_session.session_id = "sess-paused"
        paused_session.agent_session_id = "agent-paused"
        paused_session.created_at = 900.0  # older, but wrong bucket

        def mock_filter(**kwargs):
            status = kwargs.get("status")
            if status == "paused_circuit":
                return [circuit_session]
            elif status == "paused":
                return [paused_session]
            return []

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
            patch.object(lm, "transition_status") as mock_transition,
        ):
            mock_query.filter.side_effect = lambda **kw: mock_filter(**kw)
            session_recovery_drip()

        mock_transition.assert_called_once_with(
            circuit_session,
            "pending",
            reason="session-recovery-drip: API circuit recovered",
        )

    def test_drip_paused_session_when_circuit_queue_empty(self):
        """No paused_circuit sessions → drip the oldest paused session."""
        import models.agent_session as asm
        import models.session_lifecycle as lm
        from agent.sustainability import session_recovery_drip

        r = MagicMock()
        r.exists.side_effect = [False, True]  # only worker:recovering set

        paused_session = MagicMock()
        paused_session.session_id = "sess-paused"
        paused_session.agent_session_id = "agent-paused"
        paused_session.created_at = 1000.0

        def mock_filter(**kwargs):
            status = kwargs.get("status")
            if status == "paused_circuit":
                return []
            elif status == "paused":
                return [paused_session]
            return []

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
            patch.object(lm, "transition_status") as mock_transition,
        ):
            mock_query.filter.side_effect = lambda **kw: mock_filter(**kw)
            session_recovery_drip()

        mock_transition.assert_called_once_with(
            paused_session,
            "pending",
            reason="session-recovery-drip: worker recovered",
        )

    def test_clears_both_flags_when_both_queues_empty(self):
        """Both queues empty → clears recovery:active AND worker:recovering."""
        import models.agent_session as asm
        from agent.sustainability import session_recovery_drip

        r = MagicMock()
        r.exists.side_effect = [True, True]

        def mock_filter(**kwargs):
            return []

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
        ):
            mock_query.filter.side_effect = lambda **kw: mock_filter(**kw)
            session_recovery_drip()

        delete_calls = [c.args[0] for c in r.delete.call_args_list]
        assert "testproj:recovery:active" in delete_calls
        assert "testproj:worker:recovering" in delete_calls

    def test_no_op_when_neither_flag_set(self):
        """Neither recovery flag set → no sessions modified."""
        import models.session_lifecycle as lm
        from agent.sustainability import session_recovery_drip

        r = MagicMock()
        r.exists.side_effect = [False, False]

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(lm, "transition_status") as mock_transition,
        ):
            session_recovery_drip()

        mock_transition.assert_not_called()

    def test_exception_does_not_propagate(self):
        """Any unhandled exception is caught; function does not raise."""
        from agent.sustainability import session_recovery_drip

        with patch("agent.sustainability._get_redis", side_effect=RuntimeError("redis down")):
            session_recovery_drip()  # Should not raise

    def test_only_one_session_dripped_per_tick(self):
        """Two paused_circuit sessions → only the oldest one is dripped per tick."""
        import models.agent_session as asm
        import models.session_lifecycle as lm
        from agent.sustainability import session_recovery_drip

        r = MagicMock()
        r.exists.side_effect = [True, False]

        session_older = MagicMock()
        session_older.session_id = "sess-old"
        session_older.agent_session_id = "agent-old"
        session_older.created_at = 500.0

        session_newer = MagicMock()
        session_newer.session_id = "sess-new"
        session_newer.agent_session_id = "agent-new"
        session_newer.created_at = 1000.0

        def mock_filter(**kwargs):
            status = kwargs.get("status")
            if status == "paused_circuit":
                return [session_newer, session_older]
            return []

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm.AgentSession, "query", new_callable=MagicMock) as mock_query,
            patch.object(lm, "transition_status") as mock_transition,
        ):
            mock_query.filter.side_effect = lambda **kw: mock_filter(**kw)
            session_recovery_drip()

        assert mock_transition.call_count == 1
        assert mock_transition.call_args[0][0] is session_older


# ---------------------------------------------------------------------------
# TestSessionCountThrottle
# ---------------------------------------------------------------------------


class TestSessionCountThrottle(unittest.TestCase):
    def _make_session(self, started_at_offset: float = -60.0):
        """Return a fake AgentSession with started_at set relative to now."""
        import time as _time

        s = MagicMock()
        # agent_session_id must be a string for _filter_hydrated_sessions to
        # treat this test double as hydrated (issue #1069).
        s.agent_session_id = "agent-test-fake"
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
# TestFailureLoopDetector
# ---------------------------------------------------------------------------


class TestFailureLoopDetector(unittest.TestCase):
    def _make_failed_session(self, error_msg: str, session_id: str):
        s = MagicMock()
        s.session_id = session_id
        # agent_session_id must be a string for _filter_hydrated_sessions to
        # treat this test double as hydrated (issue #1069).
        s.agent_session_id = f"agent-{session_id}"
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


# ---------------------------------------------------------------------------
# TestDigestAnomalyPromptPlainLanguage
# ---------------------------------------------------------------------------


class TestDigestAnomalyPromptPlainLanguage(unittest.TestCase):
    """Verify that sustainability_digest() uses plain-language labels, not raw enum names."""

    def test_digest_anomaly_prompt_uses_plain_language(self):
        """sustainability_digest() must NOT use 'not CLOSED' and MUST include RECOVERING label."""
        import models.agent_session as asm
        from agent.sustainability import sustainability_digest

        # Use an OPEN circuit so circuits_ok=False, which triggers the anomaly path
        health_mod, resilience_mod, _cb = _build_health_stubs("open")

        r = MagicMock()
        r.get.return_value = b"none"  # throttle_level = "none"
        r.exists.return_value = 0  # queue not paused
        r.scard.return_value = 0  # no fingerprint clusters

        captured_command = {}

        # Replace AgentSession on the module so the local `from models.agent_session import
        # AgentSession` inside sustainability_digest() picks up the stub.
        fake_session_cls = MagicMock()

        def capture_enqueue(**kwargs):
            captured_command["command"] = kwargs.get("message_text", "")

        fake_session_cls.create_and_enqueue.side_effect = capture_enqueue
        fake_session_cls.query.filter.return_value = []  # no sessions → failed_24h = 0

        with (
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability._get_project_key", return_value="testproj"),
            patch.object(asm, "AgentSession", fake_session_cls),
            patch.dict(
                sys.modules,
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            sustainability_digest()

        command = captured_command.get("command", "")
        self.assertNotEqual(
            command, "", "create_and_enqueue was not called — anomaly path not reached"
        )

        # (a) The anomaly text must NOT contain the raw "not CLOSED" enum string
        self.assertNotIn(
            "not CLOSED",
            command,
            "anomaly string must not expose raw circuit enum name 'not CLOSED'",
        )

        # (b) The command prompt MUST contain the plain-language label mapping (RECOVERING as proxy)
        self.assertIn(
            "RECOVERING",
            command,
            "command prompt must include plain-language label mapping containing 'RECOVERING'",
        )


# ---------------------------------------------------------------------------
# TestCircuitHealthGateRegistered
# ---------------------------------------------------------------------------


class TestCircuitHealthGateRegistered(unittest.TestCase):
    def test_circuit_health_gate_registered(self):
        """circuit-health-gate must be present in the reflection registry."""
        from agent.reflection_scheduler import load_registry

        registry = load_registry()
        names = [e.name for e in registry]
        assert "circuit-health-gate" in names, f"circuit-health-gate not found in: {names}"

    def test_session_recovery_drip_registered(self):
        """session-recovery-drip must be present in the reflection registry."""
        from agent.reflection_scheduler import load_registry

        registry = load_registry()
        names = [e.name for e in registry]
        assert "session-recovery-drip" in names, f"session-recovery-drip not found in: {names}"


if __name__ == "__main__":
    unittest.main()
