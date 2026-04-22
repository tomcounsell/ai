"""Regression test for issue #1113 (Root Cause 3).

`python -m tools.agent_session_scheduler status` reports pending_count by
querying the pending IndexedField set. After a session is killed, its hash
status flips to "killed" but stale entries can linger in the pending index,
inflating pending_count. This mirrors the #1006 running-index zombie bug.

This test covers the fix: cmd_status must filter out sessions whose actual
(hash) status is terminal, even when they appear in the pending index.
"""

import argparse
import json
from unittest.mock import patch

from tools.agent_session_scheduler import cmd_status


class _FakeSession:
    def __init__(
        self,
        agent_session_id="session-123",
        session_id="sess-abc",
        status="pending",
        **extra,
    ):
        self.agent_session_id = agent_session_id
        self.session_id = session_id
        self.status = status
        self.priority = extra.get("priority", "normal")
        self.message_text = extra.get("message_text", "test")
        self.created_at = extra.get("created_at", 1700000000)
        self.started_at = extra.get("started_at", None)
        self.scheduled_at = extra.get("scheduled_at", None)
        self.issue_url = extra.get("issue_url", None)
        self.parent_agent_session_id = extra.get("parent_agent_session_id", None)
        self.completed_at = extra.get("completed_at", None)


class _FakeQuery:
    def __init__(self, sessions_by_status=None):
        self._sessions = sessions_by_status or {}

    def filter(self, **kwargs):
        status = kwargs.get("status")
        return list(self._sessions.get(status, []))


def _make_status_args(**kwargs):
    defaults = {"project": "valor"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestStatusExcludesTerminalSessions:
    def test_killed_session_in_pending_index_excluded(self, capsys):
        """A killed session that lingers in the pending index must not inflate pending_count.

        Simulates the #1006 pattern: pending IndexedField set contains a member
        whose hash status has already flipped to 'killed'. pending_count should
        count only non-terminal members.
        """
        # Real pending session (healthy)
        real_pending = _FakeSession(
            agent_session_id="agt_pend_real",
            session_id="local-real",
            status="pending",
        )
        # Zombie: hash status=killed but still in pending index set
        zombie = _FakeSession(
            agent_session_id="agt_zombie",
            session_id="local-zombie",
            status="killed",  # actual hash status is terminal
        )

        # Index query returns both — the pending IndexedField set is stale
        fake_query = _FakeQuery(
            sessions_by_status={
                "pending": [real_pending, zombie],
                "running": [],
                "completed": [],
                "waiting_for_children": [],
                "killed": [zombie],  # zombie also lives in killed index (correctly)
            }
        )

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch(
                "agent.agent_session_queue.PRIORITY_RANK",
                {"urgent": 0, "high": 1, "normal": 2, "low": 3},
            ),
        ):
            ret = cmd_status(_make_status_args())

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        # pending_count must EXCLUDE the zombie (1, not 2)
        assert output["pending_count"] == 1, (
            f"Expected pending_count=1 (zombie excluded), got {output['pending_count']}"
        )
        # Only the real pending session should appear in pending_sessions
        pending_ids = [s["agent_session_id"] for s in output.get("pending_sessions", [])]
        assert "agt_pend_real" in pending_ids
        assert "agt_zombie" not in pending_ids

    def test_no_zombies_normal_case(self, capsys):
        """When no stale index entries exist, pending_count equals index size."""
        a = _FakeSession(agent_session_id="a", session_id="la", status="pending")
        b = _FakeSession(agent_session_id="b", session_id="lb", status="pending")

        fake_query = _FakeQuery(
            sessions_by_status={
                "pending": [a, b],
                "running": [],
                "completed": [],
                "waiting_for_children": [],
                "killed": [],
            }
        )

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch(
                "agent.agent_session_queue.PRIORITY_RANK",
                {"urgent": 0, "high": 1, "normal": 2, "low": 3},
            ),
        ):
            ret = cmd_status(_make_status_args())

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert output["pending_count"] == 2

    def test_running_index_zombie_also_excluded(self, capsys):
        """Mirror #1006: running index zombies excluded from running_count too.

        Consistency — if we're filtering pending_count, we should filter
        running_count with the same rule.
        """
        real_running = _FakeSession(
            agent_session_id="agt_run", session_id="local-run", status="running"
        )
        zombie_running = _FakeSession(
            agent_session_id="agt_run_zombie",
            session_id="local-run-zombie",
            status="killed",
        )

        fake_query = _FakeQuery(
            sessions_by_status={
                "pending": [],
                "running": [real_running, zombie_running],
                "completed": [],
                "waiting_for_children": [],
                "killed": [zombie_running],
            }
        )

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch(
                "agent.agent_session_queue.PRIORITY_RANK",
                {"urgent": 0, "high": 1, "normal": 2, "low": 3},
            ),
        ):
            ret = cmd_status(_make_status_args())

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert output["running_count"] == 1, (
            f"Expected running_count=1 (zombie excluded), got {output['running_count']}"
        )
