"""Field guards for the PM session liveness fields (issue #1172).

Pillar A: in-flight visibility
- ``current_tool_name`` (Field, null=True, default=None) — written by
  ``agent/hooks/pre_tool_use.py``, cleared by ``agent/hooks/post_tool_use.py``.
- ``last_tool_use_at`` (DatetimeField, null=True) — bumped at tool boundaries.
- ``last_turn_at`` (DatetimeField, null=True) — bumped on SDK ``result`` events.
- ``recent_thinking_excerpt`` (Field, null=True, default=None) — last 280 chars
  of extended-thinking content.

Phase 1 self-report cap:
- ``self_report_sent_at`` (DatetimeField, null=True) — frequency-cap state for
  the PM mid-work self-report.

These fields are nullable + additive; sessions running across the deploy
boundary keep ``None`` until the writer fires. A pre-existing AgentSession row
in Redis (one written before the field was added) MUST load and re-save
without crashing — the same backcompat guarantee enforced for
``exit_returncode``.
"""

from __future__ import annotations

from models.agent_session import AgentSession, SessionType


def _make_session(suffix: str) -> AgentSession:
    return AgentSession(
        project_key=f"test-liveness-fields-{suffix}",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id=f"liveness-fields-{suffix}",
    )


def test_pillar_a_fields_default_to_none():
    s = _make_session("defaults")
    try:
        assert s.current_tool_name is None
        assert s.last_tool_use_at is None
        assert s.last_turn_at is None
        assert s.recent_thinking_excerpt is None
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_self_report_field_defaults_to_none():
    s = _make_session("selfreport-default")
    try:
        assert s.self_report_sent_at is None
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_pillar_a_fields_persist_after_save_and_reload():
    """Writing then reloading must roundtrip without losing the new fields."""
    from datetime import UTC, datetime

    s = _make_session("roundtrip")
    sid = s.agent_session_id
    try:
        s.current_tool_name = "Read"
        now = datetime.now(tz=UTC)
        s.last_tool_use_at = now
        s.last_turn_at = now
        s.recent_thinking_excerpt = "checking the file structure"
        s.save()

        loaded = AgentSession.get_by_id(sid)
        assert loaded is not None
        assert loaded.current_tool_name == "Read"
        assert loaded.last_tool_use_at is not None
        assert loaded.last_turn_at is not None
        assert loaded.recent_thinking_excerpt == "checking the file structure"
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_self_report_sent_at_persists_roundtrip():
    from datetime import UTC, datetime

    s = _make_session("selfreport-roundtrip")
    sid = s.agent_session_id
    try:
        ts = datetime.now(tz=UTC)
        s.self_report_sent_at = ts
        s.save()
        loaded = AgentSession.get_by_id(sid)
        assert loaded is not None
        assert loaded.self_report_sent_at is not None
    finally:
        try:
            s.delete()
        except Exception:
            pass
