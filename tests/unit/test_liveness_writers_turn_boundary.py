"""Unit tests for ``agent.hooks.liveness_writers.record_turn_boundary``.

Issue #1935 (Element 3): ``record_turn_boundary`` previously resolved the
in-flight ``AgentSession`` exclusively via the ``AGENT_SESSION_ID`` env var
(the ``agt_xxx`` value, injected only into the harness *subprocess* env
overlay). Its single caller — the worker-side ``result``-event handler in
``agent/sdk_client.py`` — runs in the worker process where that env var is
unset, so ``last_turn_at`` was ~100% dead. This adds an optional
``session_id`` param: when provided, it is used directly to resolve the
AgentSession (the true ``AgentSession.session_id``, plumbed from the
runner); when ``None``, the function falls back to the pre-existing
``os.environ`` behavior (preserving the in-subprocess CLI-hook call sites).
"""

from __future__ import annotations

import pytest

from agent.hooks import liveness_writers


@pytest.fixture(autouse=True)
def _reset_cooldown():
    """Every test starts with a clean per-session cooldown map."""
    liveness_writers._reset_cooldown_for_tests()
    yield
    liveness_writers._reset_cooldown_for_tests()


class _FakeSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.last_turn_at = None
        self.saved_fields: list[list[str]] = []

    def save(self, update_fields=None):
        self.saved_fields.append(list(update_fields or []))


def test_explicit_session_id_resolves_and_writes(monkeypatch):
    """Passing session_id= directly resolves the AgentSession and writes
    last_turn_at — no dependency on os.environ."""
    session = _FakeSession("sess-explicit")
    monkeypatch.setattr(
        "models.agent_session.AgentSession.query.filter",
        lambda **kw: [session] if kw.get("session_id") == "sess-explicit" else [],
    )
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

    result = liveness_writers.record_turn_boundary(session_id="sess-explicit")

    assert result is True
    assert session.last_turn_at is not None
    assert ["last_turn_at"] in session.saved_fields


def test_explicit_session_id_none_falls_back_to_env(monkeypatch):
    """session_id=None (the default) preserves the pre-existing os.environ
    fallback behavior — in-subprocess CLI-hook call sites are unaffected."""
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    result = liveness_writers.record_turn_boundary()
    assert result is False  # no env var, no explicit id → no-op, unchanged


def test_explicit_session_id_no_matching_session_returns_false(monkeypatch):
    """An explicit session_id with no matching AgentSession is a no-op, never
    raises."""
    monkeypatch.setattr(
        "models.agent_session.AgentSession.query.filter",
        lambda **kw: [],
    )
    result = liveness_writers.record_turn_boundary(session_id="sess-missing")
    assert result is False


def test_explicit_session_id_save_failure_fails_silent(monkeypatch):
    """A save() failure never raises — the turn must never crash on a
    liveness-write failure."""

    class _BoomSession(_FakeSession):
        def save(self, update_fields=None):
            raise RuntimeError("redis down")

    session = _BoomSession("sess-boom")
    monkeypatch.setattr(
        "models.agent_session.AgentSession.query.filter",
        lambda **kw: [session],
    )
    result = liveness_writers.record_turn_boundary(session_id="sess-boom")
    assert result is False  # swallowed, no raise


def test_explicit_session_id_respects_cooldown_bucket(monkeypatch):
    """The explicit-session_id path still coalesces rapid repeats through the
    same per-session turn cooldown bucket used by the env-fallback path."""
    session = _FakeSession("sess-cooldown")
    monkeypatch.setattr(
        "models.agent_session.AgentSession.query.filter",
        lambda **kw: [session],
    )

    assert liveness_writers.record_turn_boundary(session_id="sess-cooldown") is True
    # Immediate second call within the cooldown window is coalesced.
    assert liveness_writers.record_turn_boundary(session_id="sess-cooldown") is False
    assert len(session.saved_fields) == 1
