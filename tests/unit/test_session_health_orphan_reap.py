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
- TC4: terminal session with no fenced pid → handle popped, no SIGTERM
- TC5: handle whose DB row is missing → handle popped, no counter
- TC6: SIGTERM raises ``ProcessLookupError`` → handle popped silently
- TC7: ``_pending_sigkill`` drain — fence-matching PID receives SIGKILL, set is
       cleared
- TC8: ``_pending_sigkill`` drain — dead PID raises ``ProcessLookupError``
       silently, set is cleared
- D2 (#2518): the staged SIGKILL is FENCED — a staged ``(pid, create_time)``
  whose identity no longer matches at drain time is dropped unsignalled
- Legacy-row policy (#2518): a row carrying a pid but no recorded
  ``create_time`` gets SIGTERM on a plain liveness probe (recoverable, and what
  the site already did pre-fence) and NO SIGKILL escalation, because unknown
  never authorizes an irreversible kill

Approach: every test calls ``_agent_session_health_check`` with the forward
``AgentSession.query.filter(...)`` calls patched to return empty iterators
(so the running/pending scans are no-ops), and ``AgentSession.get_by_id``
patched to return a tailored ``SimpleNamespace`` for the orphan-reap pass.
This isolates the new code path from the rest of the health check and the
``DatetimeField(auto_now=True)`` ``updated_at`` semantics on real Popoto
records.

Fence seam (#2518): these tests previously stubbed
``agent.pid_fence.fence_is_live`` to ``lambda pid, ct: pid is not None``, so the
real predicate was never exercised and every fence branch collapsed to a pid
null-check. They now run the REAL ``fence_is_live`` with
``agent.pid_fence.proc_create_time`` patched — the seam spike-4 established as
the one that reaches production (late-bound, already proven through
``session_health``'s lazy import). Fake ``create_time`` values drive the
dead / recycled / matching branches without chasing real pid recycling, which
cannot be forced on demand and is parallel-hostile under ``-n auto``.

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


#: The ``create_time`` every ``_fake_session`` records in its fence by default.
#: ``_run_health_check_isolated`` patches ``proc_create_time`` to return this
#: same value, so the REAL fence reads "alive and ours" unless a test says
#: otherwise.
_FENCE_CT = 111.0


def _fake_session(
    *,
    sid: str,
    status: str,
    age_seconds: float,
    project_key: str = "test-orphan-reap",
    pid: int | None = None,
    create_time: float | None = _FENCE_CT,
):
    """Build a stand-in for an AgentSession row.

    Durability plan #2494: the in-process orphan reaper reads the fenced
    execution record (``live_fence``) off the session row, not ``SessionHandle.pid``
    (deleted). ``pid`` seeds that fence here. ``create_time=None`` produces a
    LEGACY row — a pid with no recorded identity (#2518).
    """
    return SimpleNamespace(
        agent_session_id=sid,
        id=sid,
        status=status,
        project_key=project_key,
        updated_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        live_fence={"pid": pid, "create_time": create_time} if pid is not None else None,
    )


def _staged_pids() -> set[int]:
    """The pids currently staged in ``_pending_sigkill``.

    ``_pending_sigkill`` holds ``(pid, create_time)`` tuples since #2518, so a
    bare ``pid not in _pending_sigkill`` assertion passes vacuously against
    ANY set of tuples — including a correctly-staged one. Every membership
    assertion below goes through this projection instead.
    """
    return {p for p, _ct in session_health._pending_sigkill}


def _run_health_check_isolated(get_by_id_return, *, live_create_time=_FENCE_CT) -> dict:
    """Drive ``_agent_session_health_check`` once with forward scans empty.

    ``get_by_id_return`` may be a single SimpleNamespace, a dict
    {sid: SimpleNamespace | None}, or a callable(sid) -> SimpleNamespace | None.

    ``live_create_time`` is what ``agent.pid_fence.proc_create_time`` reports
    for EVERY pid this tick — the seam that drives the real fence (#2518). It
    may be a scalar or a callable(pid) -> float | None:

    * ``_FENCE_CT``  → the fence matches ``_fake_session``'s default → "ours"
    * a different float → the pid is alive but RECYCLED → "not ours"
    * ``None``       → the pid is dead / unreadable → "not ours"

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

    ct_fn = live_create_time if callable(live_create_time) else (lambda _pid: live_create_time)

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
        # The REAL fence runs; only its psutil read is faked (#2518). Fake pids
        # do not exist on this machine, so without this patch every fence read
        # would be "dead" and no branch but the pop-the-handle one is reachable.
        patch("agent.pid_fence.proc_create_time", side_effect=ct_fn),
    ):
        asyncio.run(_agent_session_health_check())

    return {"kill_calls": kill_calls}


# ---------------------------------------------------------------------------
# TC1: terminal session past grace → SIGTERM, pop, counter increment
# ---------------------------------------------------------------------------


def test_terminal_session_past_grace_is_reaped(clean_state):
    sid = "tc1-terminal-past-grace"
    fake_pid = 999_001
    _active_sessions[sid] = SessionHandle(task=None)

    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0, pid=fake_pid)
    out = _run_health_check_isolated(fake)

    # SIGTERM was sent on the orphan pid.
    assert (fake_pid, signal.SIGTERM) in out["kill_calls"], (
        f"Expected SIGTERM on pid={fake_pid}; got: {out['kill_calls']}"
    )
    # Handle popped from registry.
    assert sid not in _active_sessions
    # Staged for next-tick SIGKILL escalation as a ``(pid, create_time)`` FENCE
    # TUPLE, not a bare int (#2518): the drain 300s later re-verifies identity
    # rather than trusting the pid, because the tick interval is the same order
    # as the macOS pid-recycle window.
    assert (fake_pid, _FENCE_CT) in session_health._pending_sigkill


# ---------------------------------------------------------------------------
# TC2: running session is not touched
# ---------------------------------------------------------------------------


def test_running_session_is_not_reaped(clean_state):
    sid = "tc2-running"
    fake_pid = 999_002
    _active_sessions[sid] = SessionHandle(task=None)

    fake = _fake_session(sid=sid, status="running", age_seconds=600.0, pid=fake_pid)
    out = _run_health_check_isolated(fake)

    # No SIGTERM on our pid — running sessions are out of scope for the reap.
    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"], (
        f"Running session must not be SIGTERMd; got: {out['kill_calls']}"
    )
    # Handle preserved.
    assert sid in _active_sessions
    # PID NOT staged.
    assert fake_pid not in _staged_pids()


# ---------------------------------------------------------------------------
# TC3: terminal session within grace window → preserved this tick
# ---------------------------------------------------------------------------


def test_terminal_session_within_grace_is_preserved(clean_state):
    sid = "tc3-within-grace"
    fake_pid = 999_003
    _active_sessions[sid] = SessionHandle(task=None)

    # 30s ago is well inside the 60s grace window.
    fake = _fake_session(sid=sid, status="completed", age_seconds=30.0, pid=fake_pid)
    out = _run_health_check_isolated(fake)

    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"], (
        f"Within-grace orphan must not be SIGTERMd this tick; got: {out['kill_calls']}"
    )
    assert sid in _active_sessions
    assert fake_pid not in _staged_pids()


# ---------------------------------------------------------------------------
# TC4: terminal session, no fenced pid → handle popped, no SIGTERM
# ---------------------------------------------------------------------------


def test_terminal_session_with_no_pid_is_popped(clean_state):
    sid = "tc4-no-pid"
    _active_sessions[sid] = SessionHandle(task=None)

    # No fenced pid on the row → nothing to signal.
    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0, pid=None)
    out = _run_health_check_isolated(fake)

    # No os.kill call from the reap (no fenced pid to target).
    assert out["kill_calls"] == [], (
        f"Should not call os.kill when the fence has no pid; got {out['kill_calls']}"
    )
    # Handle popped.
    assert sid not in _active_sessions


# ---------------------------------------------------------------------------
# TC5: handle for missing DB row → handle popped, no counter increment
# ---------------------------------------------------------------------------


def test_handle_with_missing_db_row_is_popped(clean_state):
    sid = "tc5-missing-row"
    fake_pid = 999_005
    _active_sessions[sid] = SessionHandle(task=None)

    out = _run_health_check_isolated(None)  # get_by_id returns None

    # Handle popped.
    assert sid not in _active_sessions
    # No SIGTERM (no terminal status to act on; the handle just had no row).
    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"]
    # PID NOT staged.
    assert fake_pid not in _staged_pids()


# ---------------------------------------------------------------------------
# TC6: SIGTERM raises ProcessLookupError (already dead) → handle popped silently
# ---------------------------------------------------------------------------


def test_sigterm_process_lookup_error_pops_handle_silently(clean_state):
    sid = "tc6-already-dead"
    fake_pid = 999_006
    _active_sessions[sid] = SessionHandle(task=None)

    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0, pid=fake_pid)

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
        patch("agent.pid_fence.proc_create_time", return_value=_FENCE_CT),
    ):
        # Must not raise.
        asyncio.run(_agent_session_health_check())

    # Handle popped despite the lookup error.
    assert sid not in _active_sessions
    # PID NOT staged for SIGKILL — SIGTERM was never successfully delivered.
    assert fake_pid not in _staged_pids()


# ---------------------------------------------------------------------------
# TC7: _pending_sigkill drain — fence-matching PID gets SIGKILL, set is cleared
# ---------------------------------------------------------------------------


def test_pending_sigkill_drain_sends_sigkill_and_clears_set(clean_state):
    fake_pid = 999_007
    session_health._pending_sigkill.add((fake_pid, _FENCE_CT))

    # The staged identity still holds at drain time → the escalation fires.
    out = _run_health_check_isolated(None)  # No orphan-reap activity needed

    # SIGKILL was sent on our pid.
    assert (fake_pid, signal.SIGKILL) in out["kill_calls"], (
        f"Expected SIGKILL on pid={fake_pid}; got: {out['kill_calls']}"
    )
    # Set is cleared (single-shot drain).
    assert fake_pid not in _staged_pids(), (
        "Pending SIGKILL set must be cleared after drain — accumulation across "
        "ticks would risk SIGKILLing recycled PIDs."
    )


# ---------------------------------------------------------------------------
# TC8: _pending_sigkill drain — dead PID raises ProcessLookupError silently
# ---------------------------------------------------------------------------


def test_pending_sigkill_drain_handles_already_dead_silently(clean_state):
    dead_pid = 999_008
    session_health._pending_sigkill.add((dead_pid, _FENCE_CT))

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
        # Fence matches, so the drain genuinely attempts the SIGKILL and the
        # ProcessLookupError path is the thing under test — not a fence skip.
        patch("agent.pid_fence.proc_create_time", return_value=_FENCE_CT),
    ):
        # Must not raise.
        asyncio.run(_agent_session_health_check())

    # Set is still cleared even though SIGKILL was a no-op.
    assert dead_pid not in _staged_pids()


# ---------------------------------------------------------------------------
# D2 (#2518): the staged SIGKILL is FENCED at drain time
#
# The stage→drain gap is one health tick (300s,
# AGENT_SESSION_HEALTH_CHECK_INTERVAL) — the same order as the macOS pid-recycle
# window, so "one tick is short enough" was never a defence. The identity
# compare is.
# ---------------------------------------------------------------------------


def test_staged_sigkill_is_skipped_when_the_fence_no_longer_matches(clean_state):
    """A staged pid recycled to an unrelated process is dropped unsignalled."""
    fake_pid = 999_020
    session_health._pending_sigkill.add((fake_pid, _FENCE_CT))

    # Alive, but a DIFFERENT process now holds the pid.
    out = _run_health_check_isolated(None, live_create_time=_FENCE_CT + 5000.0)

    assert (fake_pid, signal.SIGKILL) not in out["kill_calls"], (
        "A recycled pid must never be SIGKILLed — that signals an unrelated "
        f"process. Got: {out['kill_calls']}"
    )
    # Still a one-shot drain: the entry is discarded either way, no retry.
    assert fake_pid not in _staged_pids()


def test_staged_sigkill_is_skipped_when_the_pid_is_dead(clean_state):
    """A staged pid that has since exited reads as unknown → no signal."""
    fake_pid = 999_021
    session_health._pending_sigkill.add((fake_pid, _FENCE_CT))

    out = _run_health_check_isolated(None, live_create_time=None)

    assert (fake_pid, signal.SIGKILL) not in out["kill_calls"]
    assert fake_pid not in _staged_pids()


def test_staged_sigkill_is_skipped_when_no_create_time_was_recorded(clean_state):
    """Legacy staging (``create_time is None``) is unknown → never a SIGKILL.

    Unknown never authorizes an irreversible kill (the canonical rule in
    ``agent/pid_fence.py``), even though the pid probes alive. SIGKILL is the
    irreversible half; the recoverable SIGTERM this session already received is
    the half unknown does permit.
    """
    fake_pid = 999_022
    session_health._pending_sigkill.add((fake_pid, None))

    out = _run_health_check_isolated(None, live_create_time=_FENCE_CT)

    assert (fake_pid, signal.SIGKILL) not in out["kill_calls"]
    assert fake_pid not in _staged_pids()


def test_staged_sigkill_drain_is_per_entry_not_all_or_nothing(clean_state):
    """One recycled entry must not suppress a legitimate sibling escalation."""
    ours, recycled = 999_023, 999_024
    session_health._pending_sigkill.add((ours, _FENCE_CT))
    session_health._pending_sigkill.add((recycled, _FENCE_CT))

    def _ct(pid):
        return _FENCE_CT if pid == ours else _FENCE_CT + 5000.0

    out = _run_health_check_isolated(None, live_create_time=_ct)

    assert (ours, signal.SIGKILL) in out["kill_calls"]
    assert (recycled, signal.SIGKILL) not in out["kill_calls"]
    assert session_health._pending_sigkill == set()


# ---------------------------------------------------------------------------
# Legacy-row policy (#2518): a pid with NO recorded create_time
#
# Three behaviors existed for this one condition before #2518. The rule is now:
# unknown never authorizes an IRREVERSIBLE kill and never authorizes MORE force
# than the site already applied, but it may authorize an action the site was
# already taking. In the orphan reap that means SIGTERM on a plain liveness
# probe — recoverable, and the site's own pre-fence behavior — and no SIGKILL
# escalation, because identity is unverifiable.
# ---------------------------------------------------------------------------


def test_legacy_row_gets_sigterm_but_no_sigkill_staging(clean_state):
    sid = "legacy-row-sigterm-only"
    fake_pid = 999_030
    _active_sessions[sid] = SessionHandle(task=None)

    # A pid with no recorded create_time — a row written before the fence existed.
    fake = _fake_session(
        sid=sid, status="completed", age_seconds=120.0, pid=fake_pid, create_time=None
    )
    # os.kill is patched to succeed, so the plain ``_pid_is_alive`` probe
    # (os.kill(pid, 0)) reports the pid alive.
    out = _run_health_check_isolated(fake, live_create_time=None)

    assert (fake_pid, signal.SIGTERM) in out["kill_calls"], (
        "A legacy row whose pid probes alive must still be SIGTERMd — refusing "
        f"to signal at all leaks the orphan forever. Got: {out['kill_calls']}"
    )
    assert fake_pid not in _staged_pids(), (
        "A legacy row must NOT earn the SIGKILL escalation: without a recorded "
        "create_time we cannot prove the pid is ours, and unknown never "
        "authorizes a kill. Only a fence MATCH earns escalation."
    )
    assert sid not in _active_sessions


def test_legacy_row_with_a_dead_pid_is_only_popped(clean_state):
    sid = "legacy-row-dead-pid"
    fake_pid = 999_031
    _active_sessions[sid] = SessionHandle(task=None)

    fake = _fake_session(
        sid=sid, status="completed", age_seconds=120.0, pid=fake_pid, create_time=None
    )

    def _dead_kill(pid, sig):
        raise ProcessLookupError("no such process")

    def _empty_filter(*args, **kwargs):
        return iter([])

    with (
        patch("agent.session_health.os.kill", side_effect=_dead_kill),
        patch(
            "agent.session_health.AgentSession.query",
            SimpleNamespace(filter=_empty_filter, all=lambda: iter([])),
        ),
        patch("agent.session_health.AgentSession.get_by_id", side_effect=lambda _sid: fake),
        patch("agent.session_health._filter_hydrated_sessions", side_effect=lambda xs: list(xs)),
        patch("agent.pid_fence.proc_create_time", return_value=None),
    ):
        asyncio.run(_agent_session_health_check())

    assert sid not in _active_sessions
    assert fake_pid not in _staged_pids()


def test_recycled_fence_pid_is_popped_without_any_signal(clean_state):
    """A terminal session whose exec_pid was recycled: pop the handle, signal nothing."""
    sid = "recycled-fence-pid"
    fake_pid = 999_032
    _active_sessions[sid] = SessionHandle(task=None)

    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0, pid=fake_pid)
    # Alive pid, mismatched identity — and the row DID record a create_time, so
    # the legacy liveness fallback must not fire either.
    out = _run_health_check_isolated(fake, live_create_time=_FENCE_CT + 5000.0)

    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"], (
        f"A recycled pid must never be SIGTERMd; got {out['kill_calls']}"
    )
    assert fake_pid not in _staged_pids()
    assert sid not in _active_sessions


# ---------------------------------------------------------------------------
# Module-level invariants & kill-switch
# ---------------------------------------------------------------------------


def test_pending_sigkill_set_exists_and_is_a_set():
    assert isinstance(session_health._pending_sigkill, set)


def test_pending_sigkill_is_annotated_as_a_fence_tuple_set():
    """D2 anti-regression: bare ints must not creep back in.

    ``set[int]`` type-checks fine and the drain would still run, so only the
    annotation records the intent. The Verification row greps for the old
    ``set[int]`` shape; this asserts the new one from the runtime side.
    """
    import inspect

    src = inspect.getsource(session_health)
    assert "_pending_sigkill: set[tuple[int, float | None]] = set()" in src
    assert "_pending_sigkill: set[int]" not in src


def test_orphan_reap_grace_seconds_is_60():
    assert session_health.ORPHAN_REAP_GRACE_SECONDS == 60


def test_disable_orphan_reap_env_flag_short_circuits(clean_state, monkeypatch):
    """DISABLE_ORPHAN_REAP=1 must skip the reap pass entirely."""
    sid = "kill-switch-orphan"
    fake_pid = 999_999
    _active_sessions[sid] = SessionHandle(task=None)

    fake = _fake_session(sid=sid, status="completed", age_seconds=120.0, pid=fake_pid)
    monkeypatch.setenv("DISABLE_ORPHAN_REAP", "1")

    out = _run_health_check_isolated(fake)

    # No SIGTERM, no pop — kill switch fully short-circuits the pass.
    assert (fake_pid, signal.SIGTERM) not in out["kill_calls"]
    assert sid in _active_sessions
