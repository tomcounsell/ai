"""Integration tests for liveness fields in /dashboard.json (issue #1269, #2518).

The dashboard JSON payload includes ``exec_pid``, ``pid_create_time``,
``last_heartbeat_at``, ``last_sdk_heartbeat_at``, ``last_stdout_at``,
``recovery_attempts``, ``reprieve_count``, ``process_alive`` for every session
entry. These tests exercise the FastAPI route end-to-end with a synthetic
AgentSession.

Fence note (#2518). The ``alive_session`` fixture used to set
``exec_pid=os.getpid()`` with NO ``pid_create_time`` and assert
``process_alive is True``. That assertion would have passed against a
completely broken fence -- it only ever established that a PID exists, which is
the question the fence was built to stop answering. Under the current contract
that row is ``None`` (unknown, ``legacy_session`` below), and ``True`` requires
both halves to be recorded and to match. The recycled case is now covered too;
it never was.

Cleanup hygiene (per CLAUDE.md "Manual Testing Hygiene"):
- All synthetic sessions use a ``test-`` prefixed ``project_key`` and are
  deleted in the fixture teardown.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from agent.pid_fence import proc_create_time

pytestmark = [pytest.mark.integration, pytest.mark.webui]


def _own_create_time() -> float:
    """This pytest process' real psutil ``create_time`` -- a genuine fence half."""
    ct = proc_create_time(os.getpid())
    assert ct is not None, "cannot fence our own process -- psutil is unavailable"
    return ct


def _entry_for(payload, session):
    target = next(
        (s for s in payload["sessions"] if s["agent_session_id"] == session.agent_session_id),
        None,
    )
    assert target is not None, (
        f"synthetic session {session.agent_session_id} missing from /dashboard.json"
    )
    return target


@pytest.fixture
def client():
    from ui.app import create_app

    app = create_app()
    return TestClient(app)


def _make_session(**fence_fields):
    from models.agent_session import AgentSession, SessionType

    return AgentSession.create(
        project_key="test-dashboard-liveness-endpoint",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"dashboard-liveness-endpoint-{time.time_ns()}",
        working_dir="/tmp",
        status="running",
        recovery_attempts=2,
        reprieve_count=1,
        **fence_fields,
    )


def _delete(s):
    try:
        s.delete()
    except Exception:
        pass


@pytest.fixture
def alive_session():
    """A fully fenced row: this pytest process, with its real create_time."""
    s = _make_session(exec_pid=os.getpid(), pid_create_time=_own_create_time())
    yield s
    _delete(s)


@pytest.fixture
def recycled_session():
    """A live PID whose recorded identity belongs to a different process.

    The case the pre-fence dashboard rendered as a green live chip.
    """
    s = _make_session(exec_pid=os.getpid(), pid_create_time=_own_create_time() - 5000.0)
    yield s
    _delete(s)


@pytest.fixture
def legacy_session():
    """A pre-fence row: a PID that exists, with no identity recorded for it."""
    s = _make_session(exec_pid=os.getpid())
    yield s
    _delete(s)


class TestDashboardLivenessFields:
    def test_dashboard_json_includes_liveness_keys(self, client, alive_session):
        resp = client.get("/dashboard.json")
        assert resp.status_code == 200
        payload = resp.json()
        assert "sessions" in payload
        target = _entry_for(payload, alive_session)

        for key in (
            "exec_pid",
            "pid_create_time",
            "last_heartbeat_at",
            "last_sdk_heartbeat_at",
            "last_stdout_at",
            "recovery_attempts",
            "reprieve_count",
            "process_alive",
        ):
            assert key in target, f"missing key {key!r} in /dashboard.json session entry"

    def test_dashboard_json_carries_both_halves_of_the_fence(self, client, alive_session):
        """``exec_pid`` alone is not the identity -- the operator surface needs both.

        Without ``pid_create_time`` in the payload the compare is structurally
        impossible on the client side, which is how the dashboard came to render
        a recycled PID as live.
        """
        resp = client.get("/dashboard.json")
        target = _entry_for(resp.json(), alive_session)
        assert target["exec_pid"] == os.getpid()
        assert target["pid_create_time"] == pytest.approx(_own_create_time())
        assert target["recovery_attempts"] == 2
        assert target["reprieve_count"] == 1

    def test_matching_fence_renders_alive(self, client, alive_session):
        """Both halves recorded and matching -> the one route to a live chip."""
        resp = client.get("/dashboard.json")
        target = _entry_for(resp.json(), alive_session)
        assert target["process_alive"] is True

    def test_recycled_fence_renders_not_live(self, client, recycled_session):
        """The load-bearing case: a live PID under a different identity.

        The PID in this row is genuinely alive (it is this test process), so the
        pre-fence ``os.kill(pid, 0)`` probe rendered a green live chip for it.
        The fence must report it as not-live.
        """
        resp = client.get("/dashboard.json")
        target = _entry_for(resp.json(), recycled_session)
        assert target["process_alive"] is False, (
            "a recycled exec_pid must never reach the dashboard as a live chip"
        )

    def test_legacy_row_renders_unknown_not_alive(self, client, legacy_session):
        """A PID with no recorded identity is unknown, not alive.

        This is the row shape the old fixture used while asserting ``True`` --
        an assertion that would have passed against a completely broken fence.
        """
        resp = client.get("/dashboard.json")
        target = _entry_for(resp.json(), legacy_session)
        assert target["pid_create_time"] is None
        assert target["process_alive"] is None, (
            "the dashboard must not vouch for identity it never recorded"
        )
        assert target["process_alive"] is not True

    def test_existing_keys_still_present(self, client, alive_session):
        """Ensure backward-compat — all pre-existing dashboard keys remain."""
        resp = client.get("/dashboard.json")
        payload = resp.json()
        target = _entry_for(payload, alive_session)
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
