"""Regression tests for project_key namespace alignment between sustainability
reflections and the AgentSession writers (issue #1171).

These tests assert that with ``VALOR_PROJECT_KEY=valor`` set:
  - ``circuit_health_gate`` writes its flags under ``valor:sustainability:*``
    and ``valor:worker:*``.
  - ``session_recovery_drip`` queries ``AgentSession.query.filter(project_key="valor")``.

Catches the failure mode where one half of the system writes/reads ``default:*``
while AgentSession records carry ``project_key="valor"``, leaving paused
sessions stranded.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers (lifted from tests/unit/test_sustainability.py to avoid coupling)
# ---------------------------------------------------------------------------


def _build_health_stubs(circuit_state_value: str = "open", anthropic_present: bool = True):
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

    return health_mod, resilience_mod


# ---------------------------------------------------------------------------
# circuit_health_gate writes to {valor}:* when VALOR_PROJECT_KEY=valor
# ---------------------------------------------------------------------------


class TestCircuitHealthGateValorNamespace(unittest.TestCase):
    """Assert circuit_health_gate writes under ``valor:*`` keys when env is set."""

    def test_open_circuit_writes_valor_flags(self):
        """OPEN circuit + VALOR_PROJECT_KEY=valor → flags land under valor:*."""
        from agent.sustainability import circuit_health_gate

        health_mod, resilience_mod = _build_health_stubs("open")
        r = MagicMock()
        r.exists.side_effect = [0, 0]  # neither flag was set

        with (
            patch.dict("os.environ", {"VALOR_PROJECT_KEY": "valor"}, clear=False),
            patch("agent.sustainability._get_redis", return_value=r),
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

        set_keys = [c.args[0] for c in r.set.call_args_list]
        assert "valor:sustainability:queue_paused" in set_keys, set_keys
        assert "valor:worker:hibernating" in set_keys, set_keys
        # And explicitly NOT default:*
        assert all(not k.startswith("default:") for k in set_keys), set_keys

    def test_closed_circuit_recovery_keys_use_valor_namespace(self):
        """CLOSED-after-OPEN + VALOR_PROJECT_KEY=valor → recovery keys land under valor:*."""
        from agent.sustainability import circuit_health_gate

        health_mod, resilience_mod = _build_health_stubs("closed")
        r = MagicMock()
        r.exists.side_effect = [1, 1]  # both flags were set, recovery should fire

        with (
            patch.dict("os.environ", {"VALOR_PROJECT_KEY": "valor"}, clear=False),
            patch("agent.sustainability._get_redis", return_value=r),
            patch("agent.sustainability.send_hibernation_notification") as notif_mock,
            patch.dict(
                sys.modules,
                {
                    "bridge.health": health_mod,
                    "bridge.resilience": resilience_mod,
                },
            ),
        ):
            circuit_health_gate()

        delete_keys = [c.args[0] for c in r.delete.call_args_list]
        assert "valor:sustainability:queue_paused" in delete_keys
        assert "valor:worker:hibernating" in delete_keys

        set_keys = [c.args[0] for c in r.set.call_args_list]
        assert "valor:recovery:active" in set_keys
        assert "valor:worker:recovering" in set_keys
        notif_mock.assert_called_once_with("waking", project_key="valor")


# ---------------------------------------------------------------------------
# session_recovery_drip filters AgentSessions by project_key="valor"
# ---------------------------------------------------------------------------


class TestSessionRecoveryDripValorNamespace(unittest.TestCase):
    """Assert session_recovery_drip queries AgentSession.query.filter(project_key="valor")."""

    def test_filter_uses_valor_project_key(self):
        """With VALOR_PROJECT_KEY=valor, recovery_drip queries project_key="valor"."""
        # Patch AgentSession.query.filter so we can capture the call args.
        from agent import sustainability as sust_mod
        from models import agent_session as agent_session_mod

        captured_filter_kwargs = []

        class _FakeQuery:
            def filter(self, **kwargs):
                captured_filter_kwargs.append(kwargs)
                # Return an empty list to short-circuit the rest of the function.
                return []

        class _FakeAgentSession:
            query = _FakeQuery()

        r = MagicMock()
        r.exists.return_value = 1  # recovery active flag is set

        with (
            patch.dict("os.environ", {"VALOR_PROJECT_KEY": "valor"}, clear=False),
            patch.object(sust_mod, "_get_redis", return_value=r),
            patch.object(agent_session_mod, "AgentSession", _FakeAgentSession),
        ):
            sust_mod.session_recovery_drip()

        # We expect at least one filter call to have been made with
        # project_key="valor". The function may make multiple filter calls
        # (e.g. for paused_circuit AND paused statuses); all must pass valor.
        assert captured_filter_kwargs, "expected at least one AgentSession.query.filter() call"
        for kwargs in captured_filter_kwargs:
            assert kwargs.get("project_key") == "valor", (
                f"expected project_key='valor', got {kwargs!r}"
            )


if __name__ == "__main__":
    unittest.main()
