"""Incident replay (#3588): the reconciler never speaks in human chat.

The 2026-09-29 incident: an outbound expectation owned by a role placeholder
(`dev`) whose lane had shipped a merged PR was escalated straight to the human
chat with internal IDs and shell commands. Replayed against a real Redis: the
reconciler now writes nothing to any `telegram:outbox:*` list and instead
creates a handoff session in the Job's Room carrying typed evidence.
"""

import json
import subprocess
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import reflections.agent_handoff as ah
import reflections.expectation_reconciler as er
from models.job import Job

pytestmark = pytest.mark.integration

CHAT = "-100555"


@pytest.fixture
def scene(monkeypatch):
    key = f"test-replay-{uuid.uuid4().hex[:8]}"
    room = f"{key}|telegram:{CHAT}"
    monkeypatch.setattr(er, "machine_owns_project", lambda _k: True)
    monkeypatch.setattr(ah, "machine_owns_project", lambda _k: True)
    yield key, room
    from agent.enqueue_idempotency import release_if_bound_to
    from models.agent_session import AgentSession

    for row in AgentSession.query.filter(project_key=key):
        extra = row.extra_context or {}
        release_if_bound_to(
            f"handoff:expectation_reconciler:{room}:{extra.get('job_id')}:"
            f"{extra.get('expectation_id')}",
            row.agent_session_id,
        )
        row.delete()
    for j in Job.query.filter(room_id=room):
        j.delete()


def _outbox_keys() -> set[str]:
    from popoto.redis_db import POPOTO_REDIS_DB

    return {
        k.decode() if isinstance(k, bytes) else k for k in POPOTO_REDIS_DB.keys("telegram:outbox:*")
    }


def _job_with_stale_expectation(room: str, owner: str) -> Job:
    job = Job.mint(room, "ship the fix end to end")
    eid = job.add_expectation("deliver the fix", direction="outbound", owner=owner)
    data = json.loads(job.goal)
    for entry in data["expectations"]:
        if entry["id"] == eid:
            entry["ts"] = (datetime.now(tz=UTC) - timedelta(hours=3)).isoformat()
    job._write_goal_data(data)
    return job


def _merged_pr(monkeypatch):
    payload = [{"number": 42, "state": "MERGED", "closingIssuesReferences": [{"number": 7}]}]

    def fake_run(cmd, **kw):
        out = json.dumps(payload) if cmd[:3] == ["gh", "pr", "list"] else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(er.subprocess, "run", fake_run)


def test_merged_lane_hands_typed_evidence_to_an_agent_and_writes_no_chat(scene, monkeypatch):
    from models.agent_session import AgentSession

    key, room = scene
    _job_with_stale_expectation(room, "session/replay-lane")
    _merged_pr(monkeypatch)
    before = _outbox_keys()

    result = er._reconcile_project({"slug": key, "working_directory": "/tmp"})

    assert any("handed-off: created" in f for f in result["findings"]), result
    assert _outbox_keys() == before
    rows = list(AgentSession.query.filter(project_key=key))
    assert len(rows) == 1
    brief = rows[0].message_text or ""
    assert (rows[0].extra_context or {}).get("origin") == ah.HANDOFF_ORIGIN
    assert str(rows[0].chat_id) == CHAT
    assert '"shipped_kind": "merged"' in brief and '"pr_number": 42' in brief


def test_placeholder_owner_hands_off_unrecorded_owner_evidence_without_a_chat_write(
    scene, monkeypatch
):
    from models.agent_session import AgentSession

    key, room = scene
    _job_with_stale_expectation(room, "dev")
    _merged_pr(monkeypatch)
    before = _outbox_keys()

    result = er._reconcile_project({"slug": key, "working_directory": "/tmp"})

    assert any("handed-off" in f for f in result["findings"]), result
    assert _outbox_keys() == before
    rows = list(AgentSession.query.filter(project_key=key))
    assert len(rows) == 1 and (rows[0].extra_context or {}).get("origin") == ah.HANDOFF_ORIGIN
    # A placeholder owner names no lane, so there is no slug to probe for a PR:
    # the agent gets "owner unrecorded" evidence, not typed merged-PR evidence.
    brief = rows[0].message_text or ""
    assert "no respawnable lane slug (owner: unrecorded)" in brief
    assert "deliver the fix" in brief
    assert "shipped_kind" not in brief
