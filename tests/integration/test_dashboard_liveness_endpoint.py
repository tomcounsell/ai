"""Integration tests for liveness fields in /dashboard.json (issue #1269).

The dashboard JSON payload now includes ``harness_pid``, ``last_heartbeat_at``,
``last_sdk_heartbeat_at``, ``last_stdout_at``, ``recovery_attempts``,
``reprieve_count``, ``process_alive`` for every session entry. These tests
exercise the FastAPI route end-to-end with a synthetic AgentSession.

Cleanup hygiene (per CLAUDE.md "Manual Testing Hygiene"):
- All synthetic sessions use a ``test-`` prefixed ``project_key`` and are
  deleted in the fixture teardown.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.webui]


@pytest.fixture
def client():
    from ui.app import create_app

    app = create_app()
    return TestClient(app)


@pytest.fixture
def alive_session():
    from models.agent_session import AgentSession, SessionType

    s = AgentSession.create(
        project_key="test-dashboard-liveness-endpoint",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"dashboard-liveness-endpoint-{time.time_ns()}",
        working_dir="/tmp",
        status="running",
        harness_pid=os.getpid(),  # this test process — known alive
        recovery_attempts=2,
        reprieve_count=1,
    )
    yield s
    try:
        s.delete()
    except Exception:
        pass


class TestDashboardLivenessFields:
    def test_dashboard_json_includes_liveness_keys(self, client, alive_session):
        resp = client.get("/dashboard.json")
        assert resp.status_code == 200
        payload = resp.json()
        assert "sessions" in payload
        sessions = payload["sessions"]

        target = next(
            (s for s in sessions if s["agent_session_id"] == alive_session.agent_session_id),
            None,
        )
        assert target is not None, (
            f"synthetic session {alive_session.agent_session_id} missing from /dashboard.json"
        )

        for key in (
            "harness_pid",
            "last_heartbeat_at",
            "last_sdk_heartbeat_at",
            "last_stdout_at",
            "recovery_attempts",
            "reprieve_count",
            "process_alive",
        ):
            assert key in target, f"missing key {key!r} in /dashboard.json session entry"

    def test_dashboard_json_harness_pid_value(self, client, alive_session):
        resp = client.get("/dashboard.json")
        payload = resp.json()
        target = next(
            s
            for s in payload["sessions"]
            if s["agent_session_id"] == alive_session.agent_session_id
        )
        assert target["harness_pid"] == os.getpid()
        assert target["recovery_attempts"] == 2
        assert target["reprieve_count"] == 1

    def test_dashboard_json_process_alive_true_for_running_session(self, client, alive_session):
        """Running session with PID = current process → process_alive == True."""
        resp = client.get("/dashboard.json")
        payload = resp.json()
        target = next(
            s
            for s in payload["sessions"]
            if s["agent_session_id"] == alive_session.agent_session_id
        )
        assert target["process_alive"] is True

    def test_existing_keys_still_present(self, client, alive_session):
        """Ensure backward-compat — all pre-existing dashboard keys remain."""
        resp = client.get("/dashboard.json")
        payload = resp.json()
        target = next(
            s
            for s in payload["sessions"]
            if s["agent_session_id"] == alive_session.agent_session_id
        )
        for key in (
            "agent_session_id",
            "session_id",
            "status",
            "current_tool_name",
            "last_evidence_at",
            "last_tool_use_at",
            "last_turn_at",
            "unhealthy_reason",
            "is_stale",
        ):
            assert key in target, f"backward-compat key {key!r} missing from /dashboard.json"
