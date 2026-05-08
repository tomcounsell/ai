"""Integration test: --after future-ISO writes a Reflection record."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from models.reflection import Reflection
from tools.agent_session_scheduler import _create_after_reflection


def test_after_future_iso_writes_one_shot_reflection():
    iso = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    rc = _create_after_reflection(
        iso=iso,
        scheduled_at=time.time() + 3600,
        command="/sdlc 4242",
        issue_number=4242,
        issue_title="Issue 4242 title",
        issue_url="https://example/issues/4242",
        priority="normal",
        parent_id=None,
    )
    assert rc == 0

    matches = [r for r in Reflection.query.filter() if "4242" in (r.name or "")]
    assert matches, "Expected a Reflection with issue number in name"
    rec = matches[0]
    assert rec.schedule.startswith("at:")
    assert iso in rec.schedule
    assert rec.execution_type == "agent"
    assert "/sdlc 4242" in (rec.command or "")
    assert bool(rec.auto_delete_after_run) is True
