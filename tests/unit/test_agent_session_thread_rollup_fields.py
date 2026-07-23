"""Field guards for the thread-level rollup fields (dashboard-thread-timing-aggregation).

Each reply-resume of a Telegram thread creates a fresh AgentSession record
with created_at=now and turn_count=0/tool_call_count=0, and the prior
terminal record is deleted before the new one is created. That made the
dashboard misreport a resumed thread's history as if the whole conversation
were only the most recent resume.

These four fields carry thread-level history forward across resumes:

- ``thread_first_created_at`` (DatetimeField, null=True) — timestamp of the
  very first session in the thread's resume chain.
- ``thread_turn_count`` (IntField, default=0) — cumulative turn count across
  all resumes in the thread.
- ``thread_tool_call_count`` (IntField, default=0) — cumulative tool call
  count across all resumes in the thread.
- ``thread_run_count`` (IntField, default=0) — number of resumes/runs in the
  thread's chain.

All four are nullable/additive — no backfill needed, Popoto's descriptor
self-heal path covers new fields on existing records. This module only
covers the model fields; capture/aggregation wiring lands in follow-up tasks.
"""

from __future__ import annotations

from models.agent_session import AgentSession, SessionType


def _make_session(suffix: str) -> AgentSession:
    return AgentSession(
        project_key=f"test-thread-rollup-fields-{suffix}",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        agent_session_id=f"thread-rollup-fields-{suffix}",
    )


def test_thread_rollup_fields_default_without_crashing():
    """A fresh session must construct fine with thread_first_created_at left None."""
    s = _make_session("defaults")
    try:
        assert s.thread_first_created_at is None
        assert s.thread_turn_count == 0
        assert s.thread_tool_call_count == 0
        assert s.thread_run_count == 0
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_thread_rollup_fields_persist_after_save_and_reload():
    """Writing then reloading must roundtrip without losing the new fields."""
    from datetime import UTC, datetime

    s = _make_session("roundtrip")
    sid = s.agent_session_id
    try:
        first_created = datetime.now(tz=UTC)
        s.thread_first_created_at = first_created
        s.thread_turn_count = 7
        s.thread_tool_call_count = 12
        s.thread_run_count = 3
        s.save()

        loaded = AgentSession.get_by_id(sid)
        assert loaded is not None
        assert loaded.thread_first_created_at is not None
        assert loaded.thread_turn_count == 7
        assert loaded.thread_tool_call_count == 12
        assert loaded.thread_run_count == 3
    finally:
        try:
            s.delete()
        except Exception:
            pass
