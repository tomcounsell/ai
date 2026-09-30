"""``AgentSession.rows_for_session_id`` / ``newest_for_session_id`` (#3091).

``session_id`` is a plain ``Field()`` and the primary key is the ``AutoKeyField``
``id``, so two rows can share one ``session_id``. Popoto resolves the filter via
``SMEMBERS`` on the class set, whose order is arbitrary, so any caller taking
``[0]`` from the raw filter got a coin flip. These tests seed the duplicate
shape against real Redis (autouse ``redis_test_db``) and prove the helper is
the deterministic resolver: newest ``created_at`` first, ``id`` on a tie.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from models.agent_session import AgentSession

_T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session_id():
    sid = f"test-newest-wins-{uuid.uuid4().hex[:8]}"
    yield sid
    for row in AgentSession.query.filter(session_id=sid):
        row.delete()


def _seed(session_id: str, created_at: datetime, **fields) -> AgentSession:
    row = AgentSession(
        session_id=session_id,
        project_key="test-newest-wins",
        status=fields.pop("status", "pending"),
        working_dir="/tmp",
        created_at=created_at,
        **fields,
    )
    row.save()
    return row


class TestNewestWins:
    def test_newest_row_wins_regardless_of_insertion_order(self, session_id):
        # Insert the NEWER row first so class-set insertion order and the
        # correct answer disagree; a helper that returned the first SMEMBERS
        # hit in insertion order would fail here.
        newer = _seed(session_id, _T0 + timedelta(hours=1))
        older = _seed(session_id, _T0)

        assert AgentSession.query.filter(session_id=session_id).count() == 2

        picked = AgentSession.newest_for_session_id(session_id)
        assert picked is not None
        assert picked.id == newer.id
        assert picked.id != older.id

        rows = AgentSession.rows_for_session_id(session_id)
        assert [r.id for r in rows] == [newer.id, older.id]

    def test_is_stable_across_repeated_calls(self, session_id):
        _seed(session_id, _T0)
        _seed(session_id, _T0 + timedelta(seconds=30))
        _seed(session_id, _T0 + timedelta(seconds=60))
        picks = {AgentSession.newest_for_session_id(session_id).id for _ in range(10)}
        assert len(picks) == 1

    def test_equal_created_at_breaks_tie_on_id(self, session_id):
        a = _seed(session_id, _T0)
        b = _seed(session_id, _T0)
        expected = max(a.id, b.id)
        for _ in range(5):
            assert AgentSession.newest_for_session_id(session_id).id == expected

    def test_missing_created_at_sorts_oldest(self):
        stamped = AgentSession(session_id="x", project_key="p", created_at=_T0)
        blank = AgentSession(session_id="x", project_key="p", created_at=None)
        ordered = sorted([blank, stamped], key=AgentSession._newest_first_key, reverse=True)
        assert ordered[0] is stamped

    def test_extra_filters_narrow_before_ordering(self, session_id):
        _seed(session_id, _T0 + timedelta(hours=2), status="completed")
        live = _seed(session_id, _T0, status="pending")

        assert AgentSession.newest_for_session_id(session_id).status == "completed"
        assert AgentSession.newest_for_session_id(session_id, status="pending").id == live.id
        assert AgentSession.newest_for_session_id(session_id, status="running") is None

    def test_unknown_session_id_returns_none_and_empty(self):
        sid = f"test-newest-wins-absent-{uuid.uuid4().hex[:8]}"
        assert AgentSession.newest_for_session_id(sid) is None
        assert AgentSession.rows_for_session_id(sid) == []
