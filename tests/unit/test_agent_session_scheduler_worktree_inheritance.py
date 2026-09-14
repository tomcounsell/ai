"""Unit tests for the #3176 scheduler inheritance guard.

``cmd_schedule --parent-session`` must not inherit a parent ``working_dir``
that points inside ``.worktrees/`` — a scheduled child synthesizes its own
slug and provisions its own worktree, so inheriting a parent's lane path
makes it skip that provisioning and then fail ``verify_worktree_branch``
against another session's live lane (spike-6). A plain-checkout parent
``working_dir`` is still inherited unchanged.

Follows ``tests/unit/test_child_session_gate.py::TestSchedulerChokepoint``'s
pattern: real ``AgentSession`` rows under ``redis_test_db``, everything else
(persona gate, rate limit, issue validation, scheduling depth, project
config) faked so the test is hermetic and touches no GitHub API or
filesystem project config.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime

from models.agent_session import AgentSession
from models.child_session_gate import BYPASS_ENV_VAR


def _args(**overrides) -> argparse.Namespace:
    base = {
        "issue": 999001,
        "project": "test-3176-sched",
        "priority": None,
        "after": None,
        "session_type": None,
        "parent_session": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _patch_scheduler_scaffolding(sched, monkeypatch):
    """Stub every dependency of cmd_schedule except the inheritance guard
    itself and the real AgentSession create/query calls."""
    monkeypatch.setattr(sched, "_check_persona_permission", lambda _cmd: None)
    monkeypatch.setattr(
        sched,
        "_get_env_context",
        lambda: {
            "chat_id": "0",
            "project_key": "test-3176-sched",
            "session_id": "",
            "message_id": "0",
        },
    )
    monkeypatch.setattr(sched, "_get_scheduling_depth", lambda: 0)
    monkeypatch.setattr(sched, "_check_rate_limit", lambda _key: True)
    monkeypatch.setattr(
        sched,
        "_validate_issue",
        lambda _n: {
            "title": "Test issue",
            "url": "https://example.test/999001",
            "state": "open",
        },
    )
    # No projects.json entry for this project_key -> DEFAULT_WORKING_DIR,
    # so the only source of a non-default working_dir is parent inheritance.
    monkeypatch.setattr(
        "bridge.routing.load_config",
        lambda: {"projects": {}},
    )


def _make_parent(working_dir: str) -> AgentSession:
    return AgentSession.create(
        session_id=f"sched-parent-{uuid.uuid4().hex[:10]}",
        session_type="eng",
        project_key="test-3176-sched",
        working_dir=working_dir,
        status="running",
        chat_id=f"chat-{uuid.uuid4().hex[:8]}",
        message_text="parent work",
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )


class TestWorktreeRootedParentDeclined:
    def test_worktree_rooted_parent_working_dir_is_not_inherited(self, redis_test_db, monkeypatch):
        from tools import agent_session_scheduler as sched

        _patch_scheduler_scaffolding(sched, monkeypatch)
        monkeypatch.setenv(BYPASS_ENV_VAR, "1")

        parent = _make_parent("/Users/tester/src/ai/.worktrees/sdlc-1218")

        rc = sched.cmd_schedule(_args(parent_session=parent.agent_session_id))
        assert rc == 0

        children = list(AgentSession.query.filter(parent_agent_session_id=parent.agent_session_id))
        assert len(children) == 1
        child = children[0]
        assert child.working_dir != parent.working_dir
        assert child.working_dir == sched.DEFAULT_WORKING_DIR


class TestPlainCheckoutParentStillInherited:
    def test_plain_checkout_parent_working_dir_is_inherited(self, redis_test_db, monkeypatch):
        from tools import agent_session_scheduler as sched

        _patch_scheduler_scaffolding(sched, monkeypatch)
        monkeypatch.setenv(BYPASS_ENV_VAR, "1")

        parent = _make_parent("/Users/tester/src/ai")

        rc = sched.cmd_schedule(_args(parent_session=parent.agent_session_id))
        assert rc == 0

        children = list(AgentSession.query.filter(parent_agent_session_id=parent.agent_session_id))
        assert len(children) == 1
        child = children[0]
        assert child.working_dir == parent.working_dir
