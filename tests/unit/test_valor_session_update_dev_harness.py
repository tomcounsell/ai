"""Unit tests for the one-way dev-lane downgrade CLI (plan #2001 Task 3).

``cmd_update_dev_harness`` is the only post-creation writer of
``dev_harness``: codex → None (Claude), lease-gated, preserving
``codex_thread_id`` for forensics. Redis isolation comes from the
autouse ``redis_test_db`` fixture; rows use the ``test-2001`` project
prefix and are deleted afterward per manual-testing hygiene.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime

from models.agent_session import AgentSession
from tools import valor_session


def _make_flagged_session(codex_thread_id="thread-forensic-1"):
    session = AgentSession.create(
        session_id=f"update-dh-{uuid.uuid4().hex[:12]}",
        session_type="eng",
        project_key="test-2001",
        working_dir="/tmp",
        status="pending",
        chat_id="999",
        message_text="flagged work",
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
        dev_harness="codex",
        codex_thread_id=codex_thread_id,
        codex_turn_count=2,
        dev_lane_fence="fence-1",
    )
    return session


def _args(session_id, as_json=False):
    return argparse.Namespace(id=session_id, json=as_json)


def _delete(session):
    try:
        session.delete()
    except Exception:  # noqa: BLE001 -- fixture flush is the backstop
        pass


def _refetch(session_id):
    rows = list(AgentSession.query.filter(session_id=session_id))
    assert rows, "session row vanished"
    return rows[0]


def test_downgrade_clears_flag_and_preserves_thread(capsys):
    session = _make_flagged_session()
    try:
        rc = valor_session.cmd_update_dev_harness(_args(session.session_id))
        assert rc == 0
        fresh = _refetch(session.session_id)
        assert fresh.dev_harness is None
        assert fresh.codex_thread_id == "thread-forensic-1"
        out = capsys.readouterr().out
        assert "Claude" in out and "thread-forensic-1" in out
    finally:
        _delete(session)


def test_downgrade_json_output(capsys):
    session = _make_flagged_session()
    try:
        rc = valor_session.cmd_update_dev_harness(_args(session.session_id, as_json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dev_harness"] is None
        assert payload["preserved_codex_thread_id"] == "thread-forensic-1"
    finally:
        _delete(session)


def test_busy_lane_refuses_downgrade(capsys):
    from agent.codex_dev_lease import acquire_dev_lease

    session = _make_flagged_session()
    try:
        with acquire_dev_lease(str(session.id), timeout_s=0):
            rc = valor_session.cmd_update_dev_harness(_args(session.session_id))
        assert rc == 1
        assert "busy" in capsys.readouterr().err.lower()
        assert _refetch(session.session_id).dev_harness == "codex"
    finally:
        _delete(session)


def test_non_codex_session_refuses(capsys):
    session = _make_flagged_session()
    session.dev_harness = None
    session.save(update_fields=["dev_harness"])
    try:
        rc = valor_session.cmd_update_dev_harness(_args(session.session_id))
        assert rc == 1
        assert "not 'codex'" in capsys.readouterr().err
    finally:
        _delete(session)


def test_missing_session_refuses(capsys):
    rc = valor_session.cmd_update_dev_harness(_args("no-such-session"))
    assert rc == 1
    assert "no session found" in capsys.readouterr().err
