"""Regression tests for the KeyField row-fork hazard (issue #3210).

``AgentSession.slug`` is a ``KeyField``. Popoto concatenates key-field values
into the row's Redis primary key, so assigning ``slug`` on an
already-persisted instance and saving writes a **new row at a new db_key**,
orphaning the original at the ``slug=None`` key.

``bridge.session_transcript.start_transcript`` hydrates the enqueue-time row
and applies transcript-phase fields to it, and assigned ``slug`` unguarded.

Under popoto 1.9.0 the divergence guard converts that silent fork into a
raised ``Cannot change KeyField 'slug' ... without migrate_key=True``. That
raise lands in ``start_transcript``'s broad ``except Exception``, so the
damage on the current pin is not a duplicate row but a **silent total loss of
the transcript-phase update**: ``log_path``, ``sender_name``, ``branch_name``,
``classification_type`` and ``correlation_id`` are all dropped, and the
``active`` lifecycle transition is never logged. Older pins forked the row
instead.

These tests therefore assert on both faces of the hazard, on actual row
identity and row count rather than merely that the code path ran: exactly one
row survives with the original ``db_key``, *and* the non-key transcript fields
actually land.

Fixtures use the autouse ``redis_test_db`` fixture for Popoto isolation.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from bridge.session_transcript import start_transcript
from models.agent_session import AgentSession


@pytest.fixture
def session_id() -> str:
    return f"test-keyfield-fork-{uuid.uuid4().hex[:12]}"


def _make_row(session_id: str, **overrides) -> AgentSession:
    fields = {
        "session_id": session_id,
        "session_type": "eng",
        "project_key": "test-3210",
        "status": "running",
        "chat_id": "12345",
        "turn_count": 0,
        "tool_call_count": 0,
    }
    fields.update(overrides)
    return AgentSession.create(**fields)


def test_slug_is_populated_when_empty(redis_test_db, session_id):
    """Initial population is still allowed: an empty slug takes the value."""
    _make_row(session_id, slug=None)

    start_transcript(session_id=session_id, project_key="test-3210", slug="lane-alpha")

    rows = AgentSession.rows_for_session_id(session_id)
    assert len(rows) == 1, f"expected exactly one row, got {[r.slug for r in rows]}"
    assert rows[0].slug == "lane-alpha"


def test_conflicting_slug_does_not_fork_the_row(redis_test_db, session_id, caplog):
    """A different incoming slug must not create a second row, and must warn.

    The row-count/db_key assertions lock the fork shut on any pin. The warning
    assertion is the pre-fix red on popoto 1.9.0: the unguarded assignment
    raised inside the broad ``except``, so nothing named ``slug`` was ever
    logged and the skip was invisible.
    """
    original = _make_row(session_id, slug="lane-alpha")
    original_db_key = original.db_key

    with caplog.at_level(logging.WARNING, logger="bridge.session_transcript"):
        start_transcript(session_id=session_id, project_key="test-3210", slug="lane-beta")

    rows = AgentSession.rows_for_session_id(session_id)
    assert len(rows) == 1, (
        "KeyField fork: start_transcript created a duplicate AgentSession row "
        f"(slugs={[r.slug for r in rows]})"
    )
    assert rows[0].slug == "lane-alpha", "existing slug must win; KeyField is immutable"
    assert rows[0].db_key == original_db_key, "row identity (db_key) must be preserved"
    assert any("slug mismatch" in rec.message for rec in caplog.records), (
        "a skipped KeyField mutation must be logged, not silent"
    )


def test_non_key_fields_still_update_on_the_same_row(redis_test_db, session_id):
    """The whole transcript-phase update must survive a conflicting slug.

    This is the sharpest pre-fix red on popoto 1.9.0: the unguarded
    ``s.slug = slug`` raised before ``s.save()``, so branch_name,
    classification_type and log_path were all silently discarded.
    """
    original = _make_row(session_id, slug="lane-alpha")
    original_db_key = original.db_key

    start_transcript(
        session_id=session_id,
        project_key="test-3210",
        slug="lane-beta",
        sender="Tester",
        branch_name="session/lane-alpha",
        classification_type="bug",
    )

    rows = AgentSession.rows_for_session_id(session_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.db_key == original_db_key
    assert row.branch_name == "session/lane-alpha"
    assert row.classification_type == "bug"
    assert row.log_path
