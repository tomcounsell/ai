"""Regression tests: the session-pickup path drops phantom records before mutation.

Root cause (Sentry VALOR-E5 / #2374, with VALOR-36 / #2369 + VALOR-35 / #2370 as
its two preceding ``logger.error`` lines): a phantom / partial ``AgentSession``
(``session_id=None``, ``created_at=None`` — the #2207 phantom-AgentSession family)
was returned by the pending-status query, passed the terminal-status guard
(``status`` decodes to its ``"pending"`` default on a phantom), and reached
``transition_status().save()``. Popoto's ``pre_save`` → ``is_valid()`` then rejected
the null ``created_at`` ``SortedField`` and raised ``ModelException`` on the worker's
hot pickup loop.

The fix routes every ``AgentSession.query.*`` result in ``agent/session_pickup.py``
through ``_filter_hydrated_sessions`` (the canonical, blessed phantom-drop point
that every caller iterating query results is required to use before any mutation
decision) BEFORE the candidate loop / ``.worker_key`` reads. A phantom is therefore
dropped and never transitioned.

These tests patch ``AgentSession.query.async_filter`` to force a phantom into the
pop path — the production route by which a phantom entered the pending index
(rebuild re-inflation) is not reproducible in a fresh per-worker test DB because
Popoto's own ``if redis_hash`` ghost guard skips most of them. Patching the query
result is the robust way to lock in the pickup-path contract itself.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.session_pickup import _pop_agent_session
from models.agent_session import AgentSession


def _phantom():
    """A phantom stand-in: key identity fields are non-string (unhydrated).

    ``_filter_hydrated_sessions`` keys hydration on ``isinstance(agent_session_id,
    str) and isinstance(session_id, str)`` — both ``None`` here, so this is dropped
    as a phantom exactly as a real orphan-index materialization would be. It has no
    ``worker_key``/``created_at``: if the filter ever failed to drop it, the pop
    path would blow up on the very next attribute read, making a regression loud.
    """
    return SimpleNamespace(agent_session_id=None, session_id=None)


def test_pop_drops_phantom_and_picks_healthy(monkeypatch):
    """A phantom returned alongside a healthy pending session is filtered out;
    the healthy session is chosen and transitioned without raising ModelException."""
    pk = "test-2374-pickup-a"
    for s in AgentSession.query.filter(project_key=pk):
        s.delete()

    healthy = AgentSession(session_id="healthy-2374-a", project_key=pk, status="pending")
    healthy.save()

    phantom = _phantom()

    async_mock = AsyncMock(return_value=[phantom, healthy])
    with patch.object(AgentSession.query, "async_filter", async_mock):
        result = asyncio.run(_pop_agent_session(pk, is_project_keyed=True))

    assert result is not None
    assert result.session_id == "healthy-2374-a"
    assert result.status == "running"

    for s in AgentSession.query.filter(project_key=pk):
        s.delete()


def test_pop_returns_none_when_only_phantom(monkeypatch):
    """A pending query yielding ONLY a phantom returns None (nothing to run) rather
    than reaching transition_status().save() and raising ModelException."""
    pk = "test-2374-pickup-b"

    async_mock = AsyncMock(return_value=[_phantom()])
    with patch.object(AgentSession.query, "async_filter", async_mock):
        # Must not raise — the phantom is dropped before any transition.
        result = asyncio.run(_pop_agent_session(pk, is_project_keyed=True))

    assert result is None
