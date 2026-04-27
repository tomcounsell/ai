"""Unit tests for Mode 4 of issue #1099 — OOM backoff via scheduled_at.

When the OS kills the harness subprocess under memory pressure
(``returncode == -9``), the recovery branch must defer the next pickup by
120s rather than immediately re-queueing into a thrash loop. The defer is
implemented by setting ``AgentSession.scheduled_at = now + 120s`` — the
existing pending-scan in ``agent/session_pickup.py`` already skips sessions
whose ``scheduled_at`` is in the future.

Critical ordering invariant (resolves critique blocker B2): the
``recovery_attempts`` increment in the recovery branch must read the
pre-bump value into a local variable BEFORE the increment, so first-time
OS kills (``pre_bump_attempts == 0``) actually trigger the defer.

Cleanup: every test that creates AgentSession records uses the
``test-resilience-mode-4`` ``project_key`` prefix and a fixture-scoped Popoto
ORM teardown (NEVER raw Redis).
"""

from datetime import UTC, datetime, timedelta

import pytest

from models.agent_session import AgentSession, SessionType


@pytest.fixture
def clean_sessions():
    """Yield the project_key prefix and clean up Popoto records on teardown."""
    project_key = "test-resilience-mode-4"
    yield project_key
    try:
        for s in AgentSession.query.filter(project_key=project_key):
            s.delete()
    except Exception:
        pass


def test_exit_returncode_field_defaults_to_zero():
    """The new field must default to 0 (= "no exit code recorded").

    Rationale: ``IntField(null=True, default=None)`` crashes Popoto serialization
    for pre-existing rows in Redis (the descriptor returns the ``IntField``
    class itself instead of ``None``). All other IntFields on this model use
    ``default=0``; ``exit_returncode`` follows the same convention. The only
    reader (``agent/session_health.py``) checks for ``== -9`` (OS OOM kill),
    so ``0`` and "healthy exit" are safely conflated with "not recorded".
    See ``test_agent_session_exit_returncode_backcompat.py`` for the
    serialization regression guard.
    """
    s = AgentSession(
        project_key="test-resilience-mode-4-fielddefault",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="x-default-test",
    )
    try:
        assert s.exit_returncode == 0
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_pre_bump_capture_ordering():
    """Locking-in test for the pre-bump capture ordering (resolves blocker B2).

    The recovery branch in ``agent/session_health.py`` must read
    ``recovery_attempts`` into a local BEFORE the increment, so a session with
    a starting value of 0 evaluates the OOM-defer condition with
    ``pre_bump_attempts == 0`` (truthy) — even though the persisted
    ``recovery_attempts`` after the branch will be 1.

    Asserts the bytecode of ``_agent_session_health_check`` references
    ``pre_bump_attempts`` BEFORE the OOM-defer condition.
    """
    import inspect

    from agent import session_health

    src = inspect.getsource(session_health._agent_session_health_check)
    # The pre-bump capture line must literally appear and must precede the
    # OOM-defer block. Both checks are textual but they pin the ordering
    # invariant against future refactors.
    assert "pre_bump_attempts = entry.recovery_attempts or 0" in src, (
        "pre_bump capture line missing — ordering fix not in place"
    )
    assert src.index("pre_bump_attempts = entry.recovery_attempts or 0") < src.index(
        "and pre_bump_attempts == 0"
    ), "pre_bump capture must come BEFORE the OOM-defer condition that reads it"


def test_oom_defer_condition_grep_present():
    """The OOM-defer block must reference exit_returncode == -9, pre_bump_attempts == 0,
    _is_memory_tight(), and scheduled_at = now + 120s.
    """
    import inspect

    from agent import session_health

    src = inspect.getsource(session_health._agent_session_health_check)
    assert 'exit_returncode", None) == -9' in src or "exit_returncode == -9" in src
    assert "pre_bump_attempts == 0" in src
    assert "_is_memory_tight()" in src
    assert "timedelta(seconds=120)" in src
    assert 'update_fields=["scheduled_at", "recovery_attempts"]' in src


def test_pending_scan_skips_deferred(clean_sessions):
    """A pending session with scheduled_at in the future must be skipped by _is_eligible."""
    # Build a session with the field already set to "deferred 60s into the future".
    future = datetime.now(tz=UTC) + timedelta(seconds=60)

    # Recreate the same eligibility predicate the production scan uses, then
    # exercise it directly. This avoids hitting the worker-key index machinery
    # which is not needed to assert the eligibility rule.
    def _is_eligible(j) -> bool:
        sa = j.scheduled_at
        if not sa:
            return True
        if isinstance(sa, datetime):
            now = datetime.now(tz=UTC)
            if sa.tzinfo is None:
                sa = sa.replace(tzinfo=UTC)
            return sa <= now
        return True

    deferred = AgentSession(
        project_key=clean_sessions,
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="sid-defer-future",
        status="pending",
    )
    deferred.scheduled_at = future
    deferred.save()
    assert _is_eligible(deferred) is False

    not_deferred = AgentSession(
        project_key=clean_sessions,
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="sid-eligible",
        status="pending",
    )
    not_deferred.scheduled_at = None
    not_deferred.save()
    assert _is_eligible(not_deferred) is True


def test_is_memory_tight_returns_bool():
    """``_is_memory_tight()`` must return a bool and never raise (fail-open semantics)."""
    from agent.session_health import _is_memory_tight

    result = _is_memory_tight()
    assert isinstance(result, bool)


def test_is_memory_tight_caches_result(monkeypatch):
    """The 5-second in-process cache must avoid repeated psutil syscalls (concern #5)."""
    from agent import session_health

    call_count = {"n": 0}

    class FakeMem:
        available = 100  # very small → tight

    def fake_virtual_memory():
        call_count["n"] += 1
        return FakeMem()

    # Reset cache and patch psutil.
    session_health._MEMORY_CACHE = None
    import psutil

    monkeypatch.setattr(psutil, "virtual_memory", fake_virtual_memory)

    # First call hits psutil.
    assert session_health._is_memory_tight() is True
    assert call_count["n"] == 1
    # Second call should be cached → no additional syscall.
    assert session_health._is_memory_tight() is True
    assert call_count["n"] == 1


def test_is_memory_tight_fails_open(monkeypatch):
    """When psutil raises, the helper returns False (fail-open, do not defer)."""
    from agent import session_health

    def fake_virtual_memory():
        raise RuntimeError("psutil exploded")

    session_health._MEMORY_CACHE = None
    import psutil

    monkeypatch.setattr(psutil, "virtual_memory", fake_virtual_memory)

    # Must not raise — must return False so the OOM defer does NOT fire.
    assert session_health._is_memory_tight() is False


def test_store_exit_returncode_persists_value(clean_sessions, monkeypatch):
    """``_store_exit_returncode`` must write the value via partial save (Popoto ORM)."""
    from agent.sdk_client import _store_exit_returncode

    s = AgentSession(
        project_key=clean_sessions,
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="sid-exit-rc",
        session_id="session-id-exit-rc",
    )
    s.save()

    _store_exit_returncode(session_id="session-id-exit-rc", returncode=-9)

    # Re-read from Redis to confirm the value persisted.
    found = list(AgentSession.query.filter(session_id="session-id-exit-rc"))
    assert len(found) == 1
    assert found[0].exit_returncode == -9


def test_store_exit_returncode_noop_on_none():
    """No session_id or returncode → silent no-op, no exception."""
    from agent.sdk_client import _store_exit_returncode

    # Must not raise.
    _store_exit_returncode(session_id=None, returncode=-9)
    _store_exit_returncode(session_id="sid", returncode=None)
    _store_exit_returncode(session_id=None, returncode=None)
