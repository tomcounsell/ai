"""Unit tests for ``agent.hooks.session_resolver`` (issue #2205).

Covers every branch of ``resolve_inflight_session()`` (VALOR match, VALOR
miss -> AGENT get_by_id fallthrough, VALOR miss + AGENT unset -> None,
AGENT-only hit, AGENT miss -> None, both unset -> None, infra error
propagates) and ``inflight_cooldown_key()`` (VALOR else AGENT else None,
pure env read with no Redis touch).
"""

from __future__ import annotations

import uuid

import pytest

from agent.hooks.session_resolver import inflight_cooldown_key, resolve_inflight_session
from models.agent_session import AgentSession, SessionType


@pytest.fixture
def live_session():
    """A real AgentSession with distinct session_id / agent_session_id."""
    s = AgentSession.create(
        project_key="test-session-resolver",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"resolver-{uuid.uuid4().hex[:8]}",
        working_dir="/tmp",
    )
    assert s.agent_session_id != s.session_id
    yield s
    try:
        s.delete()
    except Exception:
        pass


class TestResolveInflightSession:
    def test_valor_session_id_matches(self, live_session, monkeypatch):
        monkeypatch.setenv("VALOR_SESSION_ID", live_session.session_id)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        resolved = resolve_inflight_session()

        assert resolved is not None
        assert resolved.session_id == live_session.session_id

    def test_valor_miss_falls_through_to_agent_session_id(self, live_session, monkeypatch):
        """The critique-concern branch: a stale/non-matching VALOR_SESSION_ID
        must not shadow a resolvable AGENT_SESSION_ID."""
        monkeypatch.setenv("VALOR_SESSION_ID", "no-such-session-id")
        monkeypatch.setenv("AGENT_SESSION_ID", live_session.agent_session_id)

        resolved = resolve_inflight_session()

        assert resolved is not None
        assert resolved.session_id == live_session.session_id

    def test_valor_miss_and_agent_unset_returns_none(self, monkeypatch):
        monkeypatch.setenv("VALOR_SESSION_ID", "no-such-session-id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        assert resolve_inflight_session() is None

    def test_agent_session_id_only_hits_via_get_by_id(self, live_session, monkeypatch):
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.setenv("AGENT_SESSION_ID", live_session.agent_session_id)

        resolved = resolve_inflight_session()

        assert resolved is not None
        assert resolved.session_id == live_session.session_id

    def test_agent_session_id_nonexistent_returns_none(self, monkeypatch):
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.setenv("AGENT_SESSION_ID", "nonexistent-hex-id")

        assert resolve_inflight_session() is None

    def test_both_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        assert resolve_inflight_session() is None

    def test_infra_error_propagates(self, monkeypatch):
        """Popoto/Redis errors are NOT swallowed -- they propagate to the caller."""
        monkeypatch.setenv("VALOR_SESSION_ID", "some-session-id")

        def _boom(**_kwargs):
            raise RuntimeError("simulated Redis outage")

        monkeypatch.setattr(AgentSession.query, "filter", _boom)

        with pytest.raises(RuntimeError, match="simulated Redis outage"):
            resolve_inflight_session()


class TestInflightCooldownKey:
    def test_returns_valor_session_id_when_set(self, monkeypatch):
        monkeypatch.setenv("VALOR_SESSION_ID", "valor-value")
        monkeypatch.setenv("AGENT_SESSION_ID", "agent-value")

        assert inflight_cooldown_key() == "valor-value"

    def test_falls_back_to_agent_session_id(self, monkeypatch):
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.setenv("AGENT_SESSION_ID", "agent-value")

        assert inflight_cooldown_key() == "agent-value"

    def test_returns_none_when_both_unset(self, monkeypatch):
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        assert inflight_cooldown_key() is None

    def test_pure_env_read_no_redis_touch(self, monkeypatch):
        """No live session fixture needed -- proves this never queries Popoto."""
        monkeypatch.setenv("VALOR_SESSION_ID", "some-value")

        def _boom(**_kwargs):
            raise AssertionError("inflight_cooldown_key must not touch Popoto/Redis")

        monkeypatch.setattr(AgentSession.query, "filter", _boom)
        monkeypatch.setattr(AgentSession, "get_by_id_strict", classmethod(_boom))

        assert inflight_cooldown_key() == "some-value"
