"""Unit tests for tools/agent_session_scheduler.py --after writing a Reflection."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from models.reflection import Reflection


def test_create_after_reflection_writes_record():
    """--after future-ISO writes a one-shot Reflection record."""
    from tools.agent_session_scheduler import _create_after_reflection

    iso = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    rc = _create_after_reflection(
        iso=iso,
        scheduled_at=time.time() + 7200,
        command="/sdlc 999",
        issue_number=999,
        issue_title="Test issue",
        issue_url="https://example/issues/999",
        priority="normal",
        parent_id=None,
    )
    assert rc == 0

    rows = list(Reflection.query.filter())
    matching = [r for r in rows if "999" in (r.name or "")]
    assert matching, (
        f"Expected at least one Reflection with '999' in name, got {[r.name for r in rows]}"
    )
    rec = matching[0]
    assert rec.schedule.startswith("at:")
    assert iso in rec.schedule
    assert rec.execution_type == "agent"
    assert bool(rec.auto_delete_after_run) is True


def test_create_after_reflection_records_creator(monkeypatch):
    """created_by_session_id is captured from AGENT_SESSION_ID."""
    from tools.agent_session_scheduler import _create_after_reflection

    monkeypatch.setenv("AGENT_SESSION_ID", "session-abc-123")
    iso = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _create_after_reflection(
        iso=iso,
        scheduled_at=time.time() + 3600,
        command="/sdlc 1",
        issue_number=1,
        issue_title="t",
        issue_url="u",
        priority="normal",
        parent_id=None,
    )
    rows = [r for r in Reflection.query.filter() if "1" in (r.name or "")]
    assert any(r.created_by_session_id == "session-abc-123" for r in rows)
