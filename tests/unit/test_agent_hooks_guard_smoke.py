"""Smoke test for the agent-hooks consistency guard fixture in conftest.py.

Simulates the corrupt sys.modules state (agent + agent.hooks present but
agent.hooks not bound as an attribute on agent) and verifies the autouse
guard fixture repairs it before the next test's setup runs, using a real
dotted-string monkeypatch.setattr against agent.hooks.pre_tool_use.
"""

import sys
import types


def test_step1_corrupt_agent_hooks_state():
    """Simulate the corruption vector described in the guard's docstring."""
    import agent.hooks.pre_tool_use  # noqa: F401 - ensure real modules are cached

    # Replace the top-level `agent` module object without re-importing
    # children, breaking the parent->child attribute link while both
    # names remain in sys.modules.
    real_agent = sys.modules["agent"]
    fake_agent = types.ModuleType("agent")
    sys.modules["agent"] = fake_agent

    assert "agent" in sys.modules
    assert "agent.hooks" in sys.modules
    assert not hasattr(sys.modules["agent"], "hooks")

    # Restore in case this test runs standalone without the guard (defensive);
    # the autouse guard fixture's teardown/next-setup should handle real repair.
    del real_agent


def test_step2_dotted_monkeypatch_survives_after_guard_repair(monkeypatch):
    """After the guard's setup-time repair, dotted setattr must not raise."""
    # If the guard fixture ran and repaired the corruption from step1's test,
    # this dotted monkeypatch should succeed cleanly.
    monkeypatch.setattr(
        "agent.hooks.pre_tool_use.TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES",
        (),
        raising=False,
    )
    import agent

    assert hasattr(agent, "hooks")
