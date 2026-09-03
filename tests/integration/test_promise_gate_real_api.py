"""Integration tests for ``bridge.promise_gate`` against the real Anthropic API.

Validates the LLM classifier prompt end-to-end with a live Haiku call.
Mocked unit tests verify every branch of the gate's control flow, but
they do NOT validate the prompt itself — only a real-API test can prove
the prompt's few-shot examples and forward-deferral class definition
actually steer Haiku to the right verdict on representative inputs.

Plan: docs/plans/sdlc-1219.md (issue #1219), §Step-by-Step Tasks Task 5
and §Success Criteria "Integration test exercises one bypass path
end-to-end with a real Anthropic API key". Reconciled for #3016 and
extended for #3027 (docs/plans/promise-gate-recorded-obligations.md
Task 7) with the Job-scoped override case.

Cost / latency: each call is ~$0.001 and ~500ms-3s. Three tests total.

Skipped automatically when ``ANTHROPIC_API_KEY`` is unset (CI without
secrets, local runs without the key file).

These tests use **fuzzy assertions** — verdict shape, action membership,
audit-record shape — not exact-string matches on Haiku's ``reason`` field
and never on ``class_``. Haiku's reasoning text is non-deterministic
across runs, and ``class_`` is optional in the verdict tool schema (only
``action``/``reason`` are required) — a real call can legitimately return
``action="block"`` with ``class_=None``. Asserting on the ``class_`` string
label is a label-stability assertion, not a verdict-correctness one, and is
exactly what broke nightly under #3016: never do it here again.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

import pytest

from bridge.promise_gate import _AUDIT_LOG_PATH, PromiseVerdict, evaluate_promise

pytestmark = [
    pytest.mark.integration,
    pytest.mark.sdlc,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set -- skipping real-API promise-gate test",
    ),
]


def _last_audit_row_for_session(session_id: str) -> dict | None:
    """Read the most recent audit JSONL row for ``session_id``.

    Best-effort: the audit log may have rotated or the write may be racing
    a concurrent test process, so a missing row is a soft failure the
    caller decides how to handle, not an exception here.
    """
    if not _AUDIT_LOG_PATH.exists():
        return None
    match = None
    with open(_AUDIT_LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("session_id") == session_id:
                match = row
    return match


def test_forward_deferral_blocks_real_api(monkeypatch):
    """An empty forward-deferral promise → real Haiku returns BLOCK.

    Validates the LLM-first path (no mocks, no heuristic fallback) by
    feeding a canonical forward-deferral phrase and asserting the real
    classifier blocks it. Asserts on ``action`` and the audit record only
    (#3016) — never on ``class_``, which is optional in the verdict tool
    schema and legitimately came back ``None`` on a real BLOCK in nightly.
    """
    monkeypatch.setenv("PROMISE_GATE_ENABLED", "true")

    session_id = f"cli-real-api-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    verdict = evaluate_promise(
        "Reading the docs now, will come back with thoughts in a bit.",
        transport="telegram",
        session_id=session_id,
    )

    assert isinstance(verdict, PromiseVerdict)
    assert verdict.action == "block", (
        f"Expected real Haiku to BLOCK forward-deferral; got "
        f"action={verdict.action!r} reason={verdict.reason!r}"
    )
    assert isinstance(verdict.reason, str) and verdict.reason

    audit_row = _last_audit_row_for_session(session_id)
    assert audit_row is not None, "expected an audit JSONL row for this session_id"
    assert audit_row["action"] == "block"
    assert audit_row["source"] in ("promise_gate_llm", "promise_gate_heuristic")


def test_honest_completion_allows_real_api(monkeypatch):
    """An honest completion message with evidence → real Haiku returns ALLOW.

    The complement to the forward-deferral test. Confirms the prompt
    does not over-block — concrete actions with file-path / commit-hash
    evidence pass cleanly through the real classifier.

    Fuzzy assertion: ``action == "allow"``. Haiku's ``reason`` is
    free-form so we only check it's a non-empty string.
    """
    monkeypatch.setenv("PROMISE_GATE_ENABLED", "true")

    verdict = evaluate_promise(
        "Updated bridge/promise_gate.py to handle the empty-string "
        "env-var case. Committed abc1234. Tests pass.",
        transport="telegram",
        session_id=f"cli-real-api-test-{os.getpid()}",
    )

    assert isinstance(verdict, PromiseVerdict)
    assert verdict.action == "allow", (
        f"Expected real Haiku to ALLOW an honest completion with "
        f"evidence; got action={verdict.action!r} reason={verdict.reason!r} "
        f"class_={verdict.class_!r}"
    )
    assert isinstance(verdict.reason, str) and verdict.reason


@pytest.fixture
def scratch_session_with_job():
    """A test- session whose trigger message is bound to a Job (real API fixture).

    Mirrors ``tests/unit/test_promise_advisory.py::scratch_session_with_job``
    — kept as an independent copy here since integration tests must not
    import fixtures from the unit tree, and a real-API deferral is the
    override path's own honest end-to-end proof, not a mock of the drafter.
    """
    from bridge.job_router import bind_message_to_job, telegram_message_key
    from models.agent_session import AgentSession
    from models.job import Job
    from models.room import room_id as make_room_id

    key = f"test-real-api-override-{uuid.uuid4().hex[:8]}"
    chat_id = "77"
    msg_id = 5
    session = AgentSession.create(
        session_id=f"tg_{key}_{chat_id}_{msg_id}",
        project_key=key,
        status="active",
        chat_id=chat_id,
        message_text="x",
        working_dir="/tmp",
        created_at=datetime.now(tz=UTC),
    )
    rid = make_room_id(key, f"telegram:{chat_id}")
    job = Job.mint(rid, "check the deploy status")
    message_key = telegram_message_key(chat_id, msg_id)
    bind_message_to_job(message_key, job.job_id, room_id=rid)

    yield session, job

    from popoto.redis_db import POPOTO_REDIS_DB

    POPOTO_REDIS_DB.delete(f"reply:{message_key}")
    for j in Job.query.filter(room_id=rid):
        j.delete()
    session.delete()


@pytest.mark.asyncio
async def test_forward_deferral_with_recorded_expectation_allows_real_api(
    monkeypatch, scratch_session_with_job
):
    """R1 discriminator, exercised against the real LLM (#3016 override case).

    Same forward-looking text blocks 8/8 on the real LLM layer per the
    plan's measured history. With an open inbound expectation recorded on
    the session's bound Job, the identical text passes as
    ``promise_recorded_override`` — the durably-recorded-obligation
    discriminator is what clears the gate, not any change to the text's
    grammar. Exercises the drafter's main path (``use_llm=True``) directly,
    since the override check lives in ``_evaluate_drafter_promise``, not in
    the CLI's ``evaluate_promise``.
    """
    from bridge.message_drafter import _evaluate_drafter_promise
    from bridge.promise_gate import promise_override_active

    monkeypatch.setenv("PROMISE_GATE_ENABLED", "true")

    session, job = scratch_session_with_job
    deferral_text = "Say the word and I'll re-run that same dispatch."

    verdict = await _evaluate_drafter_promise(
        deferral_text, medium="telegram", session=session, use_llm=True
    )
    assert verdict.action == "block", (
        f"Expected real Haiku to BLOCK the unrecorded forward-deferral; got "
        f"action={verdict.action!r} reason={verdict.reason!r}"
    )

    job.add_expectation("re-run that same dispatch")
    assert promise_override_active(session)

    verdict = await _evaluate_drafter_promise(
        deferral_text, medium="telegram", session=session, use_llm=True
    )
    assert verdict.action == "allow", (
        f"Expected the recorded inbound expectation to override the BLOCK; "
        f"got action={verdict.action!r} reason={verdict.reason!r}"
    )
    assert verdict.reason == "promise_recorded_override"
