"""Regression tests for #2098: session-liveness-check is single-owner.

The confirmed root cause of the #2091 double-owner incident is that
``_agent_session_health_check`` runs in TWO processes:

1. In the owning worker, in-process via ``_agent_session_health_loop``.
2. Out-of-process as the ``session-liveness-check`` reflection
   (``config/reflections.yaml``), inside ``python -m reflections``.

The health check's actuation branches key off the process-local
``_active_workers`` / ``_active_sessions`` registries. In the reflection
process those are empty relative to the real worker, so every running session
looks ``worker_dead`` (false recovery -> ``running->pending``) and every
pending session looks worker-less (spawns a COMPETING queue worker). These
tests pin the guard that denies actuation in the reflection process while
leaving worker-process and direct-call (unit-test) actuation untouched.

The guard sits before the first side-effecting call (``_reap_slot_leases``),
so we use that call as the tripwire: if the guard returns early it never fires;
if actuation proceeds it does.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import agent.session_health as session_health


def _stub_health_body(monkeypatch) -> MagicMock:
    """Patch the first post-guard side effect and the DB scans to no-ops.

    Returns the ``_reap_slot_leases`` sentinel. When actuation proceeds it is
    called exactly once; when the guard returns early it is never called.
    """
    sentinel = MagicMock(name="_reap_slot_leases")
    monkeypatch.setattr(session_health, "_reap_slot_leases", sentinel)

    # Keep the body hermetic for the "proceeds" cases: empty running/pending
    # scans, so nothing touches Redis or real recovery paths.
    fake_agent_session = MagicMock(name="AgentSession")
    fake_agent_session.query.filter.return_value = []
    monkeypatch.setattr(session_health, "AgentSession", fake_agent_session)
    return sentinel


@pytest.mark.asyncio
async def test_reflection_process_skips_actuation(monkeypatch):
    """Reflection worker (env marker, never marked owner) does NOT actuate."""
    monkeypatch.setenv("VALOR_REFLECTION_WORKER", "1")
    monkeypatch.setattr(session_health, "_OWNS_SESSION_HEALTH_ACTUATION", False)
    sentinel = _stub_health_body(monkeypatch)

    await session_health._agent_session_health_check()

    assert sentinel.call_count == 0, (
        "reflection process must return before any actuation (the #2091 "
        "double-owner race is caused by it acting on an empty registry)"
    )


@pytest.mark.asyncio
async def test_owning_worker_actuates_even_with_env_marker(monkeypatch):
    """The worker (marked owner) actuates even if it inherited the env marker."""
    monkeypatch.setenv("VALOR_REFLECTION_WORKER", "1")
    monkeypatch.setattr(session_health, "_OWNS_SESSION_HEALTH_ACTUATION", True)
    sentinel = _stub_health_body(monkeypatch)

    await session_health._agent_session_health_check()

    assert sentinel.call_count == 1, (
        "the owning worker must never be gated — the owner flag overrides the reflection env marker"
    )


@pytest.mark.asyncio
async def test_direct_call_without_markers_actuates(monkeypatch):
    """Direct callers (unit tests, worker default) still actuate — no regression."""
    monkeypatch.delenv("VALOR_REFLECTION_WORKER", raising=False)
    monkeypatch.setattr(session_health, "_OWNS_SESSION_HEALTH_ACTUATION", False)
    sentinel = _stub_health_body(monkeypatch)

    await session_health._agent_session_health_check()

    assert sentinel.call_count == 1, (
        "a process with neither marker (the existing direct-call tests) must "
        "actuate exactly as before"
    )


def test_mark_owning_worker_process_sets_flag(monkeypatch):
    """`mark_owning_worker_process` flips the module owner flag to True."""
    monkeypatch.setattr(session_health, "_OWNS_SESSION_HEALTH_ACTUATION", False)
    session_health.mark_owning_worker_process()
    assert session_health._OWNS_SESSION_HEALTH_ACTUATION is True


def test_reflection_entrypoint_sets_env_marker(monkeypatch):
    """`reflections.__main__.main` tags the process before scheduling.

    Guards against a regression where the env marker stops being set, which
    would silently re-enable the double-owner actuation. We stop execution
    right after the marker is set by making the arg parser exit.
    """
    import reflections.__main__ as reflection_main

    # Pre-register the key with monkeypatch (value "0") so its teardown restores
    # the original absent state — main() overwrites it via os.environ directly,
    # which monkeypatch would otherwise not clean up (test-isolation hazard).
    monkeypatch.setenv("VALOR_REFLECTION_WORKER", "0")
    # Make _configure_logging a no-op and force argparse to exit immediately
    # after the env marker line so we never enter the asyncio scheduler loop.
    monkeypatch.setattr(reflection_main, "_configure_logging", lambda: None)
    monkeypatch.setattr("sys.argv", ["reflections", "--this-flag-does-not-exist"])

    with pytest.raises(SystemExit):
        reflection_main.main()

    assert reflection_main.os.environ.get("VALOR_REFLECTION_WORKER") == "1"
