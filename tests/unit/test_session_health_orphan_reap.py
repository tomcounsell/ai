"""Unit tests for the orphan-subprocess reap pass in ``_agent_session_health_check``.

Issue #1218: a ``claude -p`` SDK subprocess can survive after its owning
``AgentSession`` row has reached a terminal state. The forward scans
(``status="running"`` / ``status="pending"``) cannot detect this — they look at
DB rows and ask whether the worker is alive. The orphan reap pass runs the
inverse scan: iterate ``_active_sessions`` and ask "for each subprocess this
worker is still tracking, is the owning row terminal?".

Coverage:

- TC1: terminal session past grace window → SIGTERM, handle popped, counter ++
- TC2: running session → untouched
- TC3: terminal session within grace window → handle preserved this tick
- TC4: terminal session with ``handle.pid=None`` → handle popped, no SIGTERM
- TC5: handle whose DB row is missing → handle popped, no counter
- TC6: SIGTERM raises ``ProcessLookupError`` → handle popped silently
- TC7: ``_pending_sigkill`` drain — live PID receives SIGKILL, set is cleared
- TC8: ``_pending_sigkill`` drain — dead PID raises ``ProcessLookupError``
       silently, set is cleared

Approach: every test calls ``_agent_session_health_check`` with the forward
``AgentSession.query.filter(...)`` calls patched to return empty iterators
(so the running/pending scans are no-ops), and ``AgentSession.get_by_id``
patched to return a tailored ``SimpleNamespace`` for the orphan-reap pass.
This isolates the new code path from the rest of the health check and the
``DatetimeField(auto_now=True)`` ``updated_at`` semantics on real Popoto
records.

The ``_active_sessions`` registry and ``_pending_sigkill`` set are reset
around each test so cases never leak state.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent.session_health as session_health
from agent.session_health import _agent_session_health_check
from agent.session_state import SessionHandle, _active_sessions


@pytest.fixture
def clean_state():
    """Reset module-level state around each test."""
    saved_active = dict(_active_sessions)
    saved_pending = set(session_health._pending_sigkill)
    _active_sessions.clear()
    session_health._pending_sigkill.clear()

    yield

    _active_sessions.clear()
    _active_sessions.update(saved_active)
    session_health._pending_sigkill.clear()
    session_health._pending_sigkill.update(saved_pending)


def _fake_session(
    *,
    sid: str,
    status: str,
    age_seconds: float,
    project_key: str = "test-orphan-reap",
):
    """Build a stand-in for an AgentSession row."""
    return SimpleNamespace(
        agent_session_id=sid,
        id=sid,
        status=status,
        project_key=project_key,
        updated_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


def _run_health_check_isolated(get_by_id_return) -> dict:
    """Drive ``_agent_session_health_check`` once with forward scans empty.

    ``get_by_id_return`` may be a single SimpleNamespace, a dict
    {sid: SimpleNamespace | None}, or a callable(sid) -> SimpleNamespace | None.

    Returns a dict with the captured ``os.kill`` calls and the post-tick
    ``_active_sessions`` snapshot.
    """
    if callable(get_by_id_return):
        side_effect = get_by_id_return
    elif isinstance(get_by_id_return, dict):

        def side_effect(sid):
            return get_by_id_return.get(sid)
    else:

        def side_effect(_sid):
            return get_by_id_return

    kill_calls: list[tuple[int, int]] = []

    def _record_kill(pid, sig):
        kill_calls.append((pid, sig))

    # Empty iterator for forward scans.
    def _empty_filter(*args, **kwargs):
        return iter([])

    fake_query = SimpleNamespace(filter=_empty_filter, all=lambda: iter([]))

    with (
        patch("agent.session_health.os.kill", side_effect=_record_kill),
        patch("agent.session_health.AgentSession.query", fake_query),
        patch(
            "agent.session_health.AgentSession.get_by_id",
            side_effect=side_effect,
        ),
        patch(
            "agent.session_health._filter_hydrated_sessions",
            side_effect=lambda xs: list(xs),
        ),
    ):
        asyncio.run(_agent_session_health_check())

    return {"kill_calls": kill_calls}


# ---------------------------------------------------------------------------
# TC1: terminal session past grace → SIGTERM, pop, counter increment
# ---------------------------------------------------------------------------


def test_terminal_session_past_grace_is_reaped(clean_state):
    sid = "tc1-terminal-past-grace"
    fake_pid = 999_001
    _active_sessions[sid] = SessionHandle(task=None, pid=fake_pid)

    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0)
    out = _run_health_check_isolated(fake)

    # SIGTERM was sent on the orphan pid.
    assert (fake_pid, signal.SIGTERM) in out["kill_calls"], (
        f"Expected SIGTERM on pid={fake_pid}; got: {out['kill_calls']}"
    )
    # Handle popped from registry.
    assert sid not in _active_sessions
    # PID staged for next-tick SIGKILL escalation.
    assert fake_pid in session_health._pending_sigkill


# ---------------------------------------------------------------------------
# TC2: running session is not touched
# ---------------------------------------------------------------------------


def test_running_session_is_not_reaped(clean_state):
    sid = "tc2-running"
    fake_pid = 999_002
    _active_sessions[sid] = SessionHandle(task=None, pid=fake_pid)

    fake = _fake_session(sid=sid, status="running", age_seconds=600.0)
    out = _run_health_check_isolated(fake)

    # No SIGTERM on our pid — running sessions are out of scope for the reap.
    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"], (
        f"Running session must not be SIGTERMd; got: {out['kill_calls']}"
    )
    # Handle preserved.
    assert sid in _active_sessions
    # PID NOT staged.
    assert fake_pid not in session_health._pending_sigkill


# ---------------------------------------------------------------------------
# TC3: terminal session within grace window → preserved this tick
# ---------------------------------------------------------------------------


def test_terminal_session_within_grace_is_preserved(clean_state):
    sid = "tc3-within-grace"
    fake_pid = 999_003
    _active_sessions[sid] = SessionHandle(task=None, pid=fake_pid)

    # 30s ago is well inside the 60s grace window.
    fake = _fake_session(sid=sid, status="completed", age_seconds=30.0)
    out = _run_health_check_isolated(fake)

    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"], (
        f"Within-grace orphan must not be SIGTERMd this tick; got: {out['kill_calls']}"
    )
    assert sid in _active_sessions
    assert fake_pid not in session_health._pending_sigkill


# ---------------------------------------------------------------------------
# TC4: terminal session, handle.pid is None → handle popped, no SIGTERM
# ---------------------------------------------------------------------------


def test_terminal_session_with_no_pid_is_popped(clean_state):
    sid = "tc4-no-pid"
    _active_sessions[sid] = SessionHandle(task=None, pid=None)

    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0)
    out = _run_health_check_isolated(fake)

    # No os.kill call from the reap (no pid to target).
    assert out["kill_calls"] == [], (
        f"Should not call os.kill when handle.pid is None; got {out['kill_calls']}"
    )
    # Handle popped.
    assert sid not in _active_sessions


# ---------------------------------------------------------------------------
# TC5: handle for missing DB row → handle popped, no counter increment
# ---------------------------------------------------------------------------


def test_handle_with_missing_db_row_is_popped(clean_state):
    sid = "tc5-missing-row"
    fake_pid = 999_005
    _active_sessions[sid] = SessionHandle(task=None, pid=fake_pid)

    out = _run_health_check_isolated(None)  # get_by_id returns None

    # Handle popped.
    assert sid not in _active_sessions
    # No SIGTERM (no terminal status to act on; the handle just had no row).
    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"]
    # PID NOT staged.
    assert fake_pid not in session_health._pending_sigkill


# ---------------------------------------------------------------------------
# TC6: SIGTERM raises ProcessLookupError (already dead) → handle popped silently
# ---------------------------------------------------------------------------


def test_sigterm_process_lookup_error_pops_handle_silently(clean_state):
    sid = "tc6-already-dead"
    fake_pid = 999_006
    _active_sessions[sid] = SessionHandle(task=None, pid=fake_pid)

    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0)

    def _raise_lookup(pid, sig):
        if pid == fake_pid and sig == signal.SIGTERM:
            raise ProcessLookupError("no such process")

    def _empty_filter(*args, **kwargs):
        return iter([])

    fake_query = SimpleNamespace(filter=_empty_filter, all=lambda: iter([]))

    with (
        patch("agent.session_health.os.kill", side_effect=_raise_lookup),
        patch("agent.session_health.AgentSession.query", fake_query),
        patch(
            "agent.session_health.AgentSession.get_by_id",
            side_effect=lambda _sid: fake,
        ),
        patch(
            "agent.session_health._filter_hydrated_sessions",
            side_effect=lambda xs: list(xs),
        ),
    ):
        # Must not raise.
        asyncio.run(_agent_session_health_check())

    # Handle popped despite the lookup error.
    assert sid not in _active_sessions
    # PID NOT staged for SIGKILL — SIGTERM was never successfully delivered.
    assert fake_pid not in session_health._pending_sigkill


# ---------------------------------------------------------------------------
# TC7: _pending_sigkill drain — live PID gets SIGKILL, set is cleared
# ---------------------------------------------------------------------------


def test_pending_sigkill_drain_sends_sigkill_and_clears_set(clean_state):
    fake_pid = 999_007
    session_health._pending_sigkill.add(fake_pid)

    out = _run_health_check_isolated(None)  # No orphan-reap activity needed

    # SIGKILL was sent on our pid.
    assert (fake_pid, signal.SIGKILL) in out["kill_calls"], (
        f"Expected SIGKILL on pid={fake_pid}; got: {out['kill_calls']}"
    )
    # Set is cleared (single-shot drain).
    assert fake_pid not in session_health._pending_sigkill, (
        "Pending SIGKILL set must be cleared after drain — accumulation across "
        "ticks would risk SIGKILLing recycled PIDs."
    )


# ---------------------------------------------------------------------------
# TC8: _pending_sigkill drain — dead PID raises ProcessLookupError silently
# ---------------------------------------------------------------------------


def test_pending_sigkill_drain_handles_already_dead_silently(clean_state):
    dead_pid = 999_008
    session_health._pending_sigkill.add(dead_pid)

    def _raise_lookup(pid, sig):
        if pid == dead_pid and sig == signal.SIGKILL:
            raise ProcessLookupError("no such process")

    def _empty_filter(*args, **kwargs):
        return iter([])

    fake_query = SimpleNamespace(filter=_empty_filter, all=lambda: iter([]))

    with (
        patch("agent.session_health.os.kill", side_effect=_raise_lookup),
        patch("agent.session_health.AgentSession.query", fake_query),
        patch(
            "agent.session_health.AgentSession.get_by_id",
            side_effect=lambda _sid: None,
        ),
        patch(
            "agent.session_health._filter_hydrated_sessions",
            side_effect=lambda xs: list(xs),
        ),
    ):
        # Must not raise.
        asyncio.run(_agent_session_health_check())

    # Set is still cleared even though SIGKILL was a no-op.
    assert dead_pid not in session_health._pending_sigkill


# ---------------------------------------------------------------------------
# Module-level invariants & kill-switch
# ---------------------------------------------------------------------------


def test_pending_sigkill_set_exists_and_is_a_set():
    assert isinstance(session_health._pending_sigkill, set)


def test_orphan_reap_grace_seconds_is_60():
    assert session_health.ORPHAN_REAP_GRACE_SECONDS == 60


def test_disable_orphan_reap_env_flag_short_circuits(clean_state, monkeypatch):
    """DISABLE_ORPHAN_REAP=1 must skip the reap pass entirely."""
    sid = "kill-switch-orphan"
    fake_pid = 999_999
    _active_sessions[sid] = SessionHandle(task=None, pid=fake_pid)

    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0)
    monkeypatch.setenv("DISABLE_ORPHAN_REAP", "1")

    out = _run_health_check_isolated(fake)

    # No SIGTERM, no pop — kill switch fully short-circuits the pass.
    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"]
    assert sid in _active_sessions
