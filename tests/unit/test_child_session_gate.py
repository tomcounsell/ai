"""Tests for the #1633 child-session creation stopgap.

PR #1612 cut session execution over to granite PTY containers with a bounded
PM+Dev TUI pool. NEW parent-attached AgentSession creation is refused until
the #1633 refactor lands (dependent work runs as subagents within a session).

Covered here:
- ``models/child_session_gate.py`` helper semantics (env escape hatch, payload)
- queue chokepoint: ``_push_agent_session`` refuses parent-attached creates
  before any persistence call, allows them under the bypass env, and leaves
  parentless creates untouched
- scheduler chokepoint: ``cmd_schedule --parent-session`` exits 2 with the
  structured error and creates nothing

All persistence is faked -- no real Redis is touched (repo test-isolation rule).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from models.child_session_gate import (
    BYPASS_ENV_VAR,
    ChildSessionsDisabledError,
    child_sessions_allowed,
    child_sessions_disabled_json,
)


class TestGateHelper:
    """Escape-hatch env var semantics."""

    def test_blocked_by_default(self, monkeypatch):
        monkeypatch.delenv(BYPASS_ENV_VAR, raising=False)
        assert child_sessions_allowed() is False

    @pytest.mark.parametrize("value", ["0", "true", "yes", ""])
    def test_non_one_values_do_not_bypass(self, monkeypatch, value):
        monkeypatch.setenv(BYPASS_ENV_VAR, value)
        assert child_sessions_allowed() is False

    def test_env_one_bypasses(self, monkeypatch):
        monkeypatch.setenv(BYPASS_ENV_VAR, "1")
        assert child_sessions_allowed() is True

    def test_json_payload_shape(self):
        payload = child_sessions_disabled_json()
        assert payload["error"] == "child_sessions_disabled"
        assert payload["issue"] == 1633
        assert "subagents" in payload["message"]
        assert payload["bypass"] == f"{BYPASS_ENV_VAR}=1"


class _FakeQuery:
    """Stand-in for AgentSession.query -- never touches Redis."""

    @staticmethod
    def filter(**_kw):
        return []

    @staticmethod
    async def async_count(**_kw):
        return 0


class _FakeAgentSession:
    """Stand-in for the AgentSession model inside agent_session_queue."""

    query = _FakeQuery()

    def __init__(self):
        self.created: list[dict] = []

    async def async_create(self, **kwargs):
        self.created.append(kwargs)
        return MagicMock()


def _push_kwargs(**overrides) -> dict:
    base = {
        "project_key": "test-1633",
        "session_id": "test-1633-session",
        "working_dir": "/tmp",
        "message_text": "do the thing",
        "sender_name": "test",
        "chat_id": "0",
        "telegram_message_id": 0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_persistence(monkeypatch):
    """Replace the queue's AgentSession + Redis pub/sub with in-memory fakes."""
    import popoto.redis_db as redis_db_module

    from agent import agent_session_queue

    fake_cls = _FakeAgentSession()
    # The queue module references AgentSession as a class; expose the fake
    # instance's methods through a class-like shim.
    shim = MagicMock()
    shim.query = _FakeQuery()
    shim.async_create = fake_cls.async_create
    monkeypatch.setattr(agent_session_queue, "AgentSession", shim)
    monkeypatch.setattr(redis_db_module, "POPOTO_REDIS_DB", MagicMock())
    return fake_cls


class TestQueueChokepoint:
    """_push_agent_session refuses parent-attached creates (#1633)."""

    def test_parent_attached_push_refused(self, monkeypatch, fake_persistence):
        from agent.agent_session_queue import _push_agent_session

        monkeypatch.delenv(BYPASS_ENV_VAR, raising=False)
        with pytest.raises(ChildSessionsDisabledError):
            asyncio.run(_push_agent_session(**_push_kwargs(parent_agent_session_id="agt_parent")))
        assert fake_persistence.created == [], "refused path must not persist anything"

    def test_bypass_env_allows_parent_attached_push(self, monkeypatch, fake_persistence):
        from agent.agent_session_queue import _push_agent_session

        monkeypatch.setenv(BYPASS_ENV_VAR, "1")
        asyncio.run(_push_agent_session(**_push_kwargs(parent_agent_session_id="agt_parent")))
        assert len(fake_persistence.created) == 1
        assert fake_persistence.created[0]["parent_agent_session_id"] == "agt_parent"

    def test_parentless_push_unaffected(self, monkeypatch, fake_persistence):
        from agent.agent_session_queue import _push_agent_session

        monkeypatch.delenv(BYPASS_ENV_VAR, raising=False)
        asyncio.run(_push_agent_session(**_push_kwargs()))
        assert len(fake_persistence.created) == 1
        assert fake_persistence.created[0]["parent_agent_session_id"] is None


class TestSchedulerChokepoint:
    """cmd_schedule --parent-session is refused with exit 2 and no create."""

    def _args(self, **overrides) -> argparse.Namespace:
        base = {
            "issue": 123,
            "project": "test-1633",
            "priority": None,
            "after": None,
            "session_type": None,
            "parent_session": "agt_parent",
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_parent_session_schedule_refused(self, monkeypatch, capsys):
        from tools import agent_session_scheduler as sched

        monkeypatch.delenv(BYPASS_ENV_VAR, raising=False)
        monkeypatch.setattr(sched, "_check_persona_permission", lambda _cmd: None)
        monkeypatch.setattr(
            sched,
            "_get_env_context",
            lambda: {
                "chat_id": "0",
                "project_key": "test-1633",
                "session_id": "",
                "message_id": "0",
            },
        )
        monkeypatch.setattr(sched, "_get_scheduling_depth", lambda: 0)
        monkeypatch.setattr(sched, "_check_rate_limit", lambda _key: True)
        monkeypatch.setattr(
            sched,
            "_validate_issue",
            lambda _n: {"title": "Test issue", "url": "https://example.test/1", "state": "open"},
        )

        with patch("models.agent_session.AgentSession.create") as mock_create:
            rc = sched.cmd_schedule(self._args())

        assert rc == 2
        mock_create.assert_not_called()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"
        assert out["error"] == "child_sessions_disabled"
        assert out["issue"] == 1633
