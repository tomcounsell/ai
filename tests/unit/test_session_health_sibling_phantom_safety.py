"""Sibling phantom-safety tests (issue #1069).

Five sibling functions iterate ``AgentSession.query.*`` and could crash (or
worse, mutate a phantom) if orphan ``$IndexF`` members are present:

1. ``_recover_interrupted_agent_sessions_startup`` — destructive (session_health.py)
2. ``_agent_session_health_check`` — destructive (session_health.py)
3. ``session_recovery_drip`` — read-ish (sustainability.py)
4. ``session_count_throttle`` — read-only (sustainability.py)
5. ``failure_loop_detector`` — read-only (sustainability.py)

Each test seeds a phantom in the relevant status/project index bucket, adds
one live record so the function has something real to see, invokes the
function, and asserts no exception propagates and the live record is
unaffected.

These tests complement ``test_session_health_phantom_guard.py``, which covers
the cleanup path specifically.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_phantom(project_key: str = "default", status: str = "pending") -> str:
    """Seed a phantom hash and register it in the class + status + project indexes.

    The fake id is 32 chars (matches AutoKeyField uuid4 expected length). We
    also register the hash in the ``$KeyF:AgentSession:project_key:*`` set so
    filter queries with ``project_key=...`` pick up the phantom, matching the
    real production failure mode where all indexes diverge together.
    """
    import hashlib

    from popoto.redis_db import POPOTO_REDIS_DB

    # 32-char id unique per (project_key, status) so multiple seeds don't collide.
    digest = hashlib.md5(f"{project_key}:{status}".encode()).hexdigest()
    fake_id = f"ghost{digest}"[:32].ljust(32, "0")
    hash_key = f"AgentSession:None:{fake_id}:None:{project_key}:None"
    POPOTO_REDIS_DB.hset(hash_key, "placeholder", "")
    POPOTO_REDIS_DB.sadd("$Class:AgentSession", hash_key)
    POPOTO_REDIS_DB.sadd(f"$IndexF:AgentSession:status:{status}", hash_key)
    POPOTO_REDIS_DB.sadd(f"$KeyF:AgentSession:project_key:{project_key}", hash_key)
    return fake_id


@pytest.fixture(autouse=True)
def _force_project_key(monkeypatch):
    """Make sure sustainability's default project_key matches what we seed."""
    monkeypatch.setenv("VALOR_PROJECT_KEY", "default")
    yield


# ---------------------------------------------------------------------------
# session_health.py sibling guards
# ---------------------------------------------------------------------------


class TestRecoverInterruptedStartup:
    def test_survives_phantom_in_running_index(self):
        """_recover_interrupted_agent_sessions_startup must not crash on a phantom."""
        from agent.session_health import _recover_interrupted_agent_sessions_startup
        from models.agent_session import AgentSession

        live = AgentSession(session_id="live-1", project_key="default", status="running")
        live.save()
        live_id = live.agent_session_id

        _seed_phantom(project_key="default", status="running")

        # Pre-assertion: seeding produced a phantom.
        raw = list(AgentSession.query.filter(status="running"))
        assert any(not isinstance(getattr(s, "agent_session_id", None), str) for s in raw)

        # Must not raise.
        _recover_interrupted_agent_sessions_startup()

        # Live record should still be queryable.
        after = AgentSession.get_by_id(live_id)
        assert after is not None


class TestAgentSessionHealthCheck:
    def test_survives_phantom_in_running_index(self):
        """_agent_session_health_check must filter phantoms before the terminal-status guard.

        Without the filter, ``getattr(entry, "status", None)`` on a phantom
        returns a Field descriptor which is NOT in _TERMINAL_STATUSES, so the
        phantom would fall through to the destructive recovery path.
        """
        import asyncio

        from agent.session_health import _agent_session_health_check
        from models.agent_session import AgentSession

        live = AgentSession(session_id="live-2", project_key="default", status="running")
        live.save()
        live_id = live.agent_session_id

        _seed_phantom(project_key="default", status="running")

        raw = list(AgentSession.query.filter(status="running"))
        assert any(not isinstance(getattr(s, "agent_session_id", None), str) for s in raw)

        # _agent_session_health_check is async.
        asyncio.run(_agent_session_health_check())

        after = AgentSession.get_by_id(live_id)
        assert after is not None


# ---------------------------------------------------------------------------
# sustainability.py sibling guards
# ---------------------------------------------------------------------------


class TestSessionRecoveryDrip:
    def test_survives_phantom_in_paused_circuit(self):
        """session_recovery_drip filters phantoms in paused_circuit / paused queries."""
        from popoto.redis_db import POPOTO_REDIS_DB

        from agent.sustainability import session_recovery_drip
        from models.agent_session import AgentSession

        # Set recovery flag so the function does work rather than early-returning.
        POPOTO_REDIS_DB.set("default:recovery:active", "1", ex=60)

        live = AgentSession(session_id="live-3", project_key="default", status="paused_circuit")
        live.save()
        live_id = live.agent_session_id

        _seed_phantom(project_key="default", status="paused_circuit")

        raw = list(AgentSession.query.filter(project_key="default", status="paused_circuit"))
        assert any(not isinstance(getattr(s, "agent_session_id", None), str) for s in raw)

        # Must not raise.
        session_recovery_drip()

        # Live record either remains or transitioned to pending — both are fine,
        # the key check is no crash and no phantom processing.
        # Query by id to verify survival.
        after = AgentSession.get_by_id(live_id)
        assert after is not None


class TestSessionCountThrottle:
    def test_survives_phantom_in_project(self):
        """session_count_throttle filters phantoms when counting session starts."""
        from agent.sustainability import session_count_throttle
        from models.agent_session import AgentSession

        live = AgentSession(session_id="live-4", project_key="default", status="pending")
        live.save()

        _seed_phantom(project_key="default", status="pending")

        raw = list(AgentSession.query.filter(project_key="default"))
        assert any(not isinstance(getattr(s, "agent_session_id", None), str) for s in raw)

        # Must not raise.
        session_count_throttle()


class TestFailureLoopDetector:
    def test_survives_phantom_in_project(self):
        """failure_loop_detector filters phantoms when scanning for failed sessions."""
        from agent.sustainability import failure_loop_detector
        from models.agent_session import AgentSession

        live = AgentSession(session_id="live-5", project_key="default", status="failed")
        live.save()

        _seed_phantom(project_key="default", status="failed")

        raw = list(AgentSession.query.filter(project_key="default"))
        assert any(not isinstance(getattr(s, "agent_session_id", None), str) for s in raw)

        # Must not raise.
        failure_loop_detector()


# ---------------------------------------------------------------------------
# Reflections config invariant
# ---------------------------------------------------------------------------


def test_reflections_config_has_agent_session_cleanup_enabled():
    """The fix re-enables (keeps enabled) agent-session-cleanup."""
    import pathlib

    import yaml

    here = pathlib.Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    cfg_path = repo_root / "config" / "reflections.yaml"
    data = yaml.safe_load(cfg_path.read_text())

    reflections = data.get("reflections", [])
    target = next((r for r in reflections if r.get("name") == "agent-session-cleanup"), None)
    assert target is not None, "agent-session-cleanup reflection missing from config"
    assert target.get("enabled") is True, (
        f"agent-session-cleanup must be enabled: true, got {target.get('enabled')}"
    )
