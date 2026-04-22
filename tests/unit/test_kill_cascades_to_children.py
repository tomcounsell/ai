"""Regression test for issue #1113 (Root Cause 2).

When a PM session is killed, its Dev children (linked via
parent_agent_session_id) must also be killed. Previously, killing the PM
left its Dev children in pending/running state — orphans the worker kept
picking up after the parent was gone.

This test covers the fix: `_kill_agent_session()` must cascade the kill to
any non-terminal dev children.
"""

import argparse
import json
from unittest.mock import patch

from tools.agent_session_scheduler import _kill_agent_session, cmd_kill


class _FakeSession:
    def __init__(
        self,
        agent_session_id="session-123",
        session_id="sess-abc",
        status="running",
        parent_agent_session_id=None,
        **extra,
    ):
        self.agent_session_id = agent_session_id
        self.session_id = session_id
        self.status = status
        self.parent_agent_session_id = parent_agent_session_id
        self.priority = extra.get("priority", "normal")
        self.message_text = extra.get("message_text", "/sdlc test")
        self.created_at = extra.get("created_at", 1700000000)
        self.started_at = extra.get("started_at", None)
        self.scheduled_at = extra.get("scheduled_at", None)
        self.issue_url = extra.get("issue_url", None)
        self.completed_at = extra.get("completed_at", None)

    def delete(self):
        pass


class _FakeQuery:
    """Minimal stand-in for AgentSession.query with filter()."""

    def __init__(self, sessions_by_status=None, by_parent=None):
        self._sessions = sessions_by_status or {}
        self._by_parent = by_parent or {}

    def filter(self, **kwargs):
        if "parent_agent_session_id" in kwargs:
            parent_id = kwargs["parent_agent_session_id"]
            return self._by_parent.get(parent_id, [])
        status = kwargs.get("status")
        return self._sessions.get(status, [])


def _make_args(**kwargs):
    defaults = {
        "agent_session_id": None,
        "session_id": None,
        "all": False,
        "project": "valor",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestKillCascadesToChildren:
    def test_killing_pm_cascades_to_dev_child(self):
        """Killing a PM session also transitions its dev child to killed."""
        pm = _FakeSession(
            agent_session_id="agt_pm_fac44139",
            session_id="local-pm-sid",
            status="running",
        )
        dev_child = _FakeSession(
            agent_session_id="agt_dev_0efe5fd5",
            session_id="local-0efe5fd5",
            status="running",
            parent_agent_session_id="agt_pm_fac44139",
        )

        fake_query = _FakeQuery(
            sessions_by_status={"running": [pm, dev_child]},
            by_parent={"agt_pm_fac44139": [dev_child]},
        )

        finalize_calls = []

        def fake_finalize(session, status, **kwargs):
            finalize_calls.append((session.agent_session_id, status, kwargs.get("reason", "")))

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.session_lifecycle.finalize_session", side_effect=fake_finalize),
            patch("tools.agent_session_scheduler._find_process_by_session_id", return_value=None),
        ):
            result = _kill_agent_session(pm)

        # PM finalized as killed
        pm_finalizes = [c for c in finalize_calls if c[0] == "agt_pm_fac44139"]
        assert len(pm_finalizes) == 1
        assert pm_finalizes[0][1] == "killed"

        # Dev child ALSO finalized as killed with cascade reason
        child_finalizes = [c for c in finalize_calls if c[0] == "agt_dev_0efe5fd5"]
        assert len(child_finalizes) == 1, (
            f"Dev child was NOT cascade-killed (zombie leak). finalize_calls={finalize_calls!r}"
        )
        assert child_finalizes[0][1] == "killed"
        assert (
            "cascade" in child_finalizes[0][2].lower() or "parent" in child_finalizes[0][2].lower()
        )

        # Result reports killed children
        assert "cascaded_children" in result
        assert len(result["cascaded_children"]) == 1
        assert result["cascaded_children"][0]["agent_session_id"] == "agt_dev_0efe5fd5"

    def test_terminal_children_not_recascaded(self):
        """Children already in terminal states are NOT re-finalized."""
        pm = _FakeSession(
            agent_session_id="agt_pm_2",
            session_id="local-pm-2",
            status="running",
        )
        already_done = _FakeSession(
            agent_session_id="agt_dev_done",
            session_id="local-dev-done",
            status="completed",
            parent_agent_session_id="agt_pm_2",
        )
        active_child = _FakeSession(
            agent_session_id="agt_dev_active",
            session_id="local-dev-active",
            status="running",
            parent_agent_session_id="agt_pm_2",
        )

        fake_query = _FakeQuery(
            sessions_by_status={"running": [pm, active_child]},
            by_parent={"agt_pm_2": [already_done, active_child]},
        )

        finalize_calls = []

        def fake_finalize(session, status, **kwargs):
            finalize_calls.append((session.agent_session_id, status))

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.session_lifecycle.finalize_session", side_effect=fake_finalize),
            patch("tools.agent_session_scheduler._find_process_by_session_id", return_value=None),
        ):
            result = _kill_agent_session(pm)

        # Only the active child cascades; completed child is left alone
        cascaded_ids = [c["agent_session_id"] for c in result.get("cascaded_children", [])]
        assert "agt_dev_active" in cascaded_ids
        assert "agt_dev_done" not in cascaded_ids

        # finalize_session called for PM and active_child only, not already_done
        finalized_ids = [c[0] for c in finalize_calls]
        assert "agt_pm_2" in finalized_ids
        assert "agt_dev_active" in finalized_ids
        assert "agt_dev_done" not in finalized_ids

    def test_no_children_returns_empty_cascade(self):
        """Sessions without children kill cleanly without cascaded_children errors."""
        solo = _FakeSession(
            agent_session_id="agt_solo",
            session_id="local-solo",
            status="running",
        )

        fake_query = _FakeQuery(
            sessions_by_status={"running": [solo]},
            by_parent={},
        )

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.session_lifecycle.finalize_session"),
            patch("tools.agent_session_scheduler._find_process_by_session_id", return_value=None),
        ):
            result = _kill_agent_session(solo)

        assert result["status"] == "killed"
        # cascaded_children field should exist and be empty
        assert result.get("cascaded_children", []) == []


class TestCmdKillCascadeCount:
    def test_cmd_kill_reports_cascade_count(self, capsys):
        """CLI output includes cascaded_children per session."""
        pm = _FakeSession(
            agent_session_id="agt_pm_kill",
            session_id="local-pm-kill",
            status="running",
        )
        child = _FakeSession(
            agent_session_id="agt_child_kill",
            session_id="local-child-kill",
            status="running",
            parent_agent_session_id="agt_pm_kill",
        )

        fake_query = _FakeQuery(
            sessions_by_status={"running": [pm]},
            by_parent={"agt_pm_kill": [child]},
        )

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.session_lifecycle.finalize_session"),
            patch("tools.agent_session_scheduler._find_process_by_session_id", return_value=None),
        ):
            ret = cmd_kill(_make_args(agent_session_id="agt_pm_kill"))

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "killed"
        assert len(output["sessions"]) == 1
        assert len(output["sessions"][0].get("cascaded_children", [])) == 1
