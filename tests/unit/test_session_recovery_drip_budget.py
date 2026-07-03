"""Drip-exclusion for budget-tripped sessions (Fix #6 BLOCKER, issue #1821).

``paused_budget`` is a NON-drip status by design. ``session_recovery_drip.run()``
re-queues ONLY ``paused`` / ``paused_circuit`` sessions back to ``pending``; a
``paused_budget`` session must NEVER be dripped, or a
``pending→denied→paused→pending`` runaway would form (``tool_call_count`` /
``total_cost_usd`` are cumulative and never reset). A flag-only
``budget_tripped`` session whose status is still ``running`` is likewise
untouched (the drip only ever looks at paused states).

Uses real Redis (integration-style, matching the rest of the drip suite).
"""

from __future__ import annotations

import uuid

import pytest

from models.agent_session import AgentSession, SessionType
from models.session_lifecycle import transition_status
from reflections.agents import session_recovery_drip


@pytest.fixture
def drip_project(monkeypatch):
    """Scope the drip to a unique per-test project + arm the recovery flag."""
    pk = f"test-drip-budget-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("VALOR_PROJECT_KEY", pk)
    r = session_recovery_drip._get_redis()
    # Plain coordination key (NOT Popoto-managed); short TTL so it self-cleans.
    r.set(f"{pk}:recovery:active", "1", ex=120)
    created: list[AgentSession] = []

    def _make(status: str, *, budget_tripped: bool = False) -> AgentSession:
        s = AgentSession.create(
            project_key=pk,
            chat_id="x",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=f"{pk}-{status}-{uuid.uuid4().hex[:6]}",
            working_dir="/tmp",
        )
        if budget_tripped:
            s.budget_tripped = True
            s.budget_tripped_reason = "per-session tool budget reached: test"
            s.save()
        if status != "pending":
            transition_status(s, status, reason="test setup")
        created.append(s)
        return s

    yield pk, _make

    for s in created:
        try:
            s.delete()
        except Exception:
            pass


def _status_of(session_id: str) -> str:
    rows = AgentSession.query.filter(session_id=session_id)
    return rows[0].status if rows else "<gone>"


def test_drip_skips_paused_budget_but_drips_paused(drip_project):
    """A control ``paused`` session drips to pending; ``paused_budget`` does not."""
    _pk, make = drip_project
    control = make("paused")
    budget = make("paused_budget")

    # Run enough ticks to drain every paused/paused_circuit candidate.
    for _ in range(5):
        session_recovery_drip.run()

    assert _status_of(control.session_id) == "pending", "paused control should drip to pending"
    assert _status_of(budget.session_id) == "paused_budget", (
        "paused_budget must never be dripped — it is the flapping-loop guard"
    )


def test_drip_ignores_flag_only_running_session(drip_project):
    """A ``running`` session carrying the ``budget_tripped`` flag is untouched.

    The drip only ever transitions paused states; a flag-only running session
    is never a candidate, so no status change can occur.
    """
    _pk, make = drip_project
    running = make("running", budget_tripped=True)

    for _ in range(3):
        session_recovery_drip.run()

    assert _status_of(running.session_id) == "running"
