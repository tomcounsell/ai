"""Dashboard exposes Pillar A in-flight visibility fields (issue #1172).

`/dashboard.json` ``sessions[]`` entries gain five new keys:

- ``current_tool_name``       — name of the tool currently in flight, or None.
- ``last_tool_use_at``        — float epoch of the most recent tool boundary.
- ``last_turn_at``            — float epoch of the most recent SDK ``result`` event.
- ``recent_thinking_excerpt`` — last 280 chars of extended-thinking content.
- ``last_evidence_at``        — max of every evidence timestamp (heartbeats,
                                stdout, tool, turn, compaction). None when no
                                contributing field has been written yet.

These keys must always be present (with None values when no writer has fired)
so external consumers see a stable JSON shape.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from models.agent_session import AgentSession, SessionType


@pytest.fixture
def sample_session(monkeypatch):
    s = AgentSession.create(
        project_key="test-dashboard-pillar-a",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"dashboard-pillar-a-{time.time_ns()}",
        working_dir="/tmp",
        status="running",
    )
    yield s
    try:
        s.delete()
    except Exception:
        pass


def _pipeline_for(session):
    from ui.data.sdlc import _session_to_pipeline

    return _session_to_pipeline(session)


def test_pillar_a_keys_present_with_none_defaults(sample_session):
    p = _pipeline_for(sample_session)
    assert p.current_tool_name is None
    assert p.last_tool_use_at is None
    assert p.last_turn_at is None
    assert p.recent_thinking_excerpt is None
    assert p.last_evidence_at is None


def test_last_evidence_at_uses_max_of_available_timestamps(sample_session):
    now = datetime.now(tz=UTC)
    # Simulate writes from various sources.
    sample_session.last_heartbeat_at = now - timedelta(seconds=120)
    sample_session.last_tool_use_at = now - timedelta(seconds=30)  # newest
    sample_session.last_turn_at = now - timedelta(seconds=90)
    sample_session.save(update_fields=["last_heartbeat_at", "last_tool_use_at", "last_turn_at"])

    p = _pipeline_for(sample_session)
    assert p.last_evidence_at is not None
    assert p.last_tool_use_at is not None
    # The newest-of-all rule: tool_use_at wins.
    assert p.last_evidence_at == p.last_tool_use_at


def test_last_evidence_at_none_when_every_field_absent(sample_session):
    p = _pipeline_for(sample_session)
    assert p.last_evidence_at is None


# ---------------------------------------------------------------------------
# Untyped Popoto string-boolean regression (#2439)
# ---------------------------------------------------------------------------
#
# `requires_real_chrome` / `user_facing_routed` are untyped Popoto
# `Field(default=False)` fields, which round-trip through Redis as the
# *string* 'False'/'True' rather than a real bool. A naive `bool(...)` read
# treats the non-empty string 'False' as truthy. `_session_to_pipeline` must
# route these reads through `_truthy()` so the dashboard always renders a
# real Python bool.


def test_requires_real_chrome_string_false_renders_as_false(sample_session):
    """Simulate the untyped-field Redis round-trip and assert `_truthy()` wins."""
    sample_session.requires_real_chrome = "False"
    p = _pipeline_for(sample_session)
    assert p.requires_real_chrome is False


def test_requires_real_chrome_string_true_renders_as_true(sample_session):
    sample_session.requires_real_chrome = "True"
    p = _pipeline_for(sample_session)
    assert p.requires_real_chrome is True


def test_user_facing_routed_string_false_renders_as_false(sample_session):
    sample_session.user_facing_routed = "False"
    p = _pipeline_for(sample_session)
    assert p.user_facing_routed is False


def test_user_facing_routed_string_true_renders_as_true(sample_session):
    sample_session.user_facing_routed = "True"
    p = _pipeline_for(sample_session)
    assert p.user_facing_routed is True
