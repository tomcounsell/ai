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


class TestPreferTypeOrdering:
    """``prefer_type`` is a STABLE PARTITION, not a composite sort key (#3091 Risk 2).

    The two proofs in this class are the plan's non-waivable behavioral gates.
    Neither can be replaced by a grep: a genuine regression is written in the
    same vocabulary a correct build uses, so only the returned ORDER
    distinguishes them.
    """

    def test_default_order_unchanged_by_null_session_type_row(self, session_id):
        """Proof C — ``prefer_type`` omitted must stay plain newest-first even
        when a row carries ``session_type=None``.

        ``session_type`` is ``KeyField(null=True)`` (``models/agent_session.py:165``),
        so a null-typed row is legal. A partition applied UNCONDITIONALLY tests
        ``getattr(row, "session_type", None) == prefer_type``, which MATCHES the
        null row when ``prefer_type`` takes its ``None`` default — floating it to
        the head and silently reordering every one of the ~74 callers that pass
        no preference. That failure returns every row, so the "no match, return
        nothing" bullet never fires; only the order betrays it.

        The null row is CONSTRUCTED here on purpose. The live table has 165 rows
        and zero null-ish ``session_type``, so a production-shaped or
        fixture-derived row set is GREEN against the broken build and proves
        nothing (#3348). It is seeded OLDEST so the broken order (null first)
        and the correct order (null last) cannot coincide.
        """
        eng = _seed(session_id, _T0 + timedelta(hours=2), session_type="eng")
        teammate = _seed(session_id, _T0 + timedelta(hours=1), session_type="teammate")
        untyped = _seed(session_id, _T0, session_type=None)

        assert getattr(untyped, "session_type", "sentinel") is None

        rows = AgentSession.rows_for_session_id(session_id)

        # Asserted on session_type first so a RED run names the defect ("the
        # null row floated to the head") rather than printing opaque uuids.
        assert [getattr(r, "session_type", None) for r in rows] == ["eng", "teammate", None]
        assert [r.id for r in rows] == [eng.id, teammate.id, untyped.id]
        assert AgentSession.newest_for_session_id(session_id).id == eng.id

    def test_prefer_type_partitions_all_matching_first_then_newest_within_groups(self, session_id):
        """Proof D — the partition-shape proof.

        The groups are INTERLEAVED by ``created_at``. That is what makes this
        test capable of going RED: with the groups already time-separated, the
        deliberately-wrong composite key
        ``sorted(rows, key=lambda r: (session_type != prefer_type, _newest_first_key(r)))``
        yields the same list as a correct partition and the test pins nothing.
        Interleaved, the composite key sorts each group OLDEST-first (it has no
        ``reverse=True``, and adding one would invert the group order instead),
        so both halves of the contract are exercised at once:
        all-matching-then-all-non-matching AND newest-first within each group.
        """
        eng_new = _seed(session_id, _T0 + timedelta(hours=4), session_type="eng")
        pm_new = _seed(session_id, _T0 + timedelta(hours=3), session_type="teammate")
        eng_old = _seed(session_id, _T0 + timedelta(hours=2), session_type="eng")
        pm_old = _seed(session_id, _T0 + timedelta(hours=1), session_type="teammate")

        names = {
            eng_new.id: "eng_new",
            pm_new.id: "pm_new",
            eng_old.id: "eng_old",
            pm_old.id: "pm_old",
        }

        rows = AgentSession.rows_for_session_id(session_id, prefer_type="eng")

        # All matching rows precede every non-matching row.
        assert [getattr(r, "session_type", None) for r in rows] == [
            "eng",
            "eng",
            "teammate",
            "teammate",
        ]
        # Newest-first WITHIN each group. Named rather than compared by uuid so
        # a RED run names the defect instead of printing opaque ids.
        assert [names[r.id] for r in rows] == ["eng_new", "eng_old", "pm_new", "pm_old"]


class TestFetchLiveActiveRunId:
    """Proof A — the Risk 2 ordering proof for ``_fetch_live_active_run_id``.

    ``agent/session_executor.py:298`` (called at ``:384``) scans rows for the
    first non-empty ``active_run_id``, eng-first. Collapsing its two passes into
    one pass over ``rows_for_session_id(sid, prefer_type="eng")`` is
    behavior-preserving ONLY if the ordering is a stable partition. The two
    cases below pin the two halves that a naive collapse breaks in opposite
    directions:

    * dropping the eng preference (one pass, no ``prefer_type``) breaks
      ``test_..._both_rows_carry_run_id_prefers_eng``;
    * collapsing to preference-then-return (return the eng row's id even when it
      is empty) breaks ``test_..._prefers_older_non_eng_with_run_id`` by
      returning ``None`` — and the docstring at
      ``agent/session_executor.py:307-309`` records what a ``None`` costs:
      renewal skips forever and the lock lapses mid-stage (#1915).
    """

    def test_fetch_live_active_run_id_prefers_older_non_eng_with_run_id(self, session_id):
        """An eng row with no run id must NOT shadow an older non-eng row that has one."""
        from agent.session_executor import _fetch_live_active_run_id

        eng = _seed(
            session_id,
            _T0 + timedelta(hours=1),
            session_type="eng",
            active_run_id=None,
        )
        older_non_eng = _seed(
            session_id,
            _T0,
            session_type="teammate",
            active_run_id="run-older-non-eng",
        )

        assert not getattr(eng, "active_run_id", None)

        assert _fetch_live_active_run_id(eng) == "run-older-non-eng"
        assert _fetch_live_active_run_id(older_non_eng) == "run-older-non-eng"

    def test_fetch_live_active_run_id_both_rows_carry_run_id_prefers_eng(self, session_id):
        """When both rows carry a run id the ENG row wins, even though it is OLDER.

        This is the half a one-pass collapse that forgets ``prefer_type="eng"``
        loses: plain newest-first hands back the non-eng row's id, which is the
        "wrong id" branch of Risk 2 — a lapsed lock re-acquired under a dead
        identity and renewed every tick.
        """
        from agent.session_executor import _fetch_live_active_run_id

        newer_non_eng = _seed(
            session_id,
            _T0 + timedelta(hours=1),
            session_type="teammate",
            active_run_id="run-newer-non-eng",
        )
        older_eng = _seed(
            session_id,
            _T0,
            session_type="eng",
            active_run_id="run-older-eng",
        )

        assert _fetch_live_active_run_id(newer_non_eng) == "run-older-eng"
        assert _fetch_live_active_run_id(older_eng) == "run-older-eng"
