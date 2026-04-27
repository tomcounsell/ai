"""Integration test: PM/Teammate session terminal transition after stop.py sidecar-first change.

Issue #1157 — Phantom PM Twin Dedupe.

After the stop.py change to prefer ``AgentSession.get_by_id(sidecar_agent_session_id)``
over the legacy ``query.filter(session_id=f"local-{session_id}")`` reconstruction,
we must assert that a worker-spawned PM/Teammate session still transitions to a
terminal status when the Stop hook fires.

This is the "better coverage can't hurt" test requested by open question 2 in the
plan: it prevents a future refactor from silently breaking PM session finalization
by exercising the full ``_complete_agent_session`` call path against a real
(non-mocked) AgentSession record in the test Redis db.

The test creates a live ``AgentSession`` with ``session_type="pm"``, a sidecar that
points at the record via its ``agent_session_id`` (NOT a ``local-*`` session_id),
invokes ``_complete_agent_session``, and asserts the record's ``status`` transitioned
to ``completed``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the hook importable (it lives outside the normal package path)
_HOOK_DIR = str(Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks")
if _HOOK_DIR not in sys.path:
    sys.path.insert(0, _HOOK_DIR)


@pytest.fixture
def pm_session(redis_test_db, tmp_path, monkeypatch):
    """Create a live non-terminal PM AgentSession in the test Redis db."""
    from models.agent_session import AgentSession

    s = AgentSession.create(
        session_id="0_pm_terminal_test_1",  # bridge-style session_id, NOT local-*
        project_key="test-phantom-dedupe",
        status="running",
        session_type="pm",
        message_text="Integration test: PM terminal transition",
    )
    yield s
    try:
        s.delete()
    except Exception:
        pass


def _write_sidecar(tmp_path: Path, claude_session_id: str, agent_session_id: str) -> Path:
    """Write an AgentSession sidecar pointing at the given agent_session_id.

    The memory_bridge sidecar path is derived from the session_id, inside the
    session log directory. For this test we monkeypatch the path resolution to
    use tmp_path so we don't pollute the real logs dir.
    """
    sidecar_dir = tmp_path / "sidecar"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sidecar_dir / f"{claude_session_id}.json"
    import json

    sidecar_path.write_text(json.dumps({"agent_session_id": agent_session_id}))
    return sidecar_path


def test_pm_session_terminal_transition_fires_after_stop_hook_change(pm_session, monkeypatch):
    """_complete_agent_session transitions a PM session to completed via get_by_id path.

    Primary assertion: after calling ``_complete_agent_session`` with a sidecar
    pointing at the PM session's ``agent_session_id``, the session's ``status``
    is a terminal value (``completed``).

    Secondary assertion: the legacy ``query.filter(session_id=f"local-...")``
    fallback is NOT consulted, because the primary ``get_by_id`` lookup succeeds.
    """
    from models.agent_session import AgentSession

    assert pm_session.status == "running", "fixture must start non-terminal"

    # Stub the sidecar loader to return a sidecar pointing at the real PM session.
    sidecar = {"agent_session_id": pm_session.agent_session_id}

    def fake_load_sidecar(_session_id: str) -> dict:
        return sidecar

    import hook_utils.memory_bridge

    monkeypatch.setattr(hook_utils.memory_bridge, "load_agent_session_sidecar", fake_load_sidecar)

    # Track whether the legacy fallback filter was consulted. The primary
    # get_by_id path should succeed, so filter should NOT be called with
    # local-* reconstruction.
    filter_calls: list[dict] = []
    original_filter = AgentSession.query.filter

    def tracked_filter(*args, **kwargs):
        filter_calls.append(dict(kwargs))
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(AgentSession.query, "filter", tracked_filter)

    from stop import _complete_agent_session

    _complete_agent_session(
        "claude-uuid-pm-test",
        {"stop_reason": "end_turn", "session_id": "claude-uuid-pm-test"},
    )

    # Re-load the session from Redis to confirm the terminal transition persisted
    refreshed = AgentSession.get_by_id(pm_session.agent_session_id)
    assert refreshed is not None, "session should still exist after finalize"
    assert refreshed.status == "completed", (
        f"expected terminal status 'completed' after stop hook, got {refreshed.status!r}"
    )

    # Legacy fallback should NOT have been invoked with local-* reconstruction,
    # because the primary get_by_id path resolved successfully.
    local_reconstruction_filters = [
        f
        for f in filter_calls
        if any(isinstance(v, str) and v.startswith("local-") for v in f.values())
    ]
    assert not local_reconstruction_filters, (
        f"expected primary get_by_id path to succeed, but legacy local-* fallback "
        f"was consulted with filters: {local_reconstruction_filters}"
    )
