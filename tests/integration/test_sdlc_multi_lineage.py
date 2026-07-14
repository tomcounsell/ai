"""Concurrent multi-lineage contention test (issue #2026, WS1 acceptance).

The 2026-07-13 batch failure mode was never a single sequential run — it was
two-or-more fork lineages contending on ONE issue: forks re-minting fresh
``run_id``s, self-locking against the supervisor's live lease, and merging
past blocked gates. The plan's Success Criteria therefore pin a concurrent
acceptance: with ≥2 lineages contending, exactly ONE identity drives the
pipeline; every other lineage receives the named refusal
(``SUPERVISED_RUN_ACTIVE`` carrying the owner's ``run_id``) or the
``ISSUE_LOCKED`` block, and nothing is double-minted.

Exercises the REAL ``touch_issue_lock`` / supervised-run signal against the
per-worker test Redis db (only the AgentSession lookup is mocked — Popoto
model I/O is not what is under test).

Marked ``sdlc``; runs in the integration tier.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.sdlc]


def _make_session(session_id: str) -> MagicMock:
    session = MagicMock()
    session.session_id = session_id
    session.active_run_id = None
    session.working_dir = None
    return session


def _readback_as(session: MagicMock) -> MagicMock:
    mock_as = MagicMock()
    mock_as.query.filter.return_value = [session]
    return mock_as


def _bare_ensure(issue_number: int, session_id: str) -> dict:
    """One lineage's bare ``ensure_session`` against the real lock/signal."""
    from tools.sdlc_session_ensure import ensure_session

    session = _make_session(session_id)
    with (
        patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
        patch("models.agent_session.AgentSession", _readback_as(session)),
    ):
        return ensure_session(issue_number=issue_number)


class TestConcurrentMultiLineageContention:
    """≥2 lineages contend on one issue — exactly-one-owner semantics."""

    def test_concurrent_bare_ensures_mint_exactly_one_owner(self):
        """Four lineages race the SAME fresh issue concurrently. Exactly one
        wins the SET-NX lock contest and mints; every loser is blocked with a
        named reason (SUPERVISED_RUN_ACTIVE once the winner's signal is
        visible, ISSUE_LOCKED in the pre-signal window) — never a second
        mint, never an unnamed failure.

        All four lineages resolve the SAME per-issue session record (as in
        production, where ``find_session_by_issue`` is issue-keyed), so the
        session mocks are patched ONCE around the whole pool — per-thread
        patching would race the global patch state, not the lock.
        """
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 92026
        session = _make_session(f"sdlc-local-{issue_number}")

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", _readback_as(session)),
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            results = list(pool.map(lambda _i: ensure_session(issue_number=issue_number), range(4)))

        winners = [r for r in results if not r.get("blocked") and r.get("run_id")]
        losers = [r for r in results if r.get("blocked")]

        assert len(winners) == 1, f"exactly one lineage must mint; got {results!r}"
        assert len(losers) == 3
        owner_run_id = winners[0]["run_id"]
        # The one identity is bound on the shared per-issue record.
        assert session.active_run_id == owner_run_id

        for loser in losers:
            assert loser["reason"] in ("SUPERVISED_RUN_ACTIVE", "ISSUE_LOCKED")
            # Whichever refusal shape fired, it must name the live owner —
            # never a fresh identity.
            assert loser.get("owner_run_id") == owner_run_id

    def test_second_lineage_inherits_owner_run_id_via_named_refusal(self):
        """Sequential shape of the same contention: a supervisor holds the
        run; a later fork lineage's bare ensure returns the named
        SUPERVISED_RUN_ACTIVE refusal carrying the supervisor's run_id to
        inherit — it mints nothing and the ledger shows one identity."""
        issue_number = 92027

        supervisor = _bare_ensure(issue_number, f"sdlc-local-{issue_number}-supervisor")
        assert not supervisor.get("blocked")
        owner_run_id = supervisor["run_id"]

        fork = _bare_ensure(issue_number, f"sdlc-local-{issue_number}-fork")
        assert fork["blocked"] is True
        assert fork["reason"] == "SUPERVISED_RUN_ACTIVE"
        assert fork["run_id"] == owner_run_id
        assert fork["owner_run_id"] == owner_run_id
        assert fork.get("created") is None

    def test_release_frees_issue_for_a_fresh_lineage(self):
        """After the owner releases the lease (run end), the signal goes
        stale and a new lineage mints fresh — the refusal never outlives the
        ownership it reports."""
        from models.session_lifecycle import release_issue_lock

        issue_number = 92028

        first = _bare_ensure(issue_number, f"sdlc-local-{issue_number}-a")
        assert not first.get("blocked")
        released = release_issue_lock(issue_number, first["run_id"])
        assert released is True

        second = _bare_ensure(issue_number, f"sdlc-local-{issue_number}-b")
        assert not second.get("blocked"), f"expected fresh mint, got {second!r}"
        assert second["run_id"] != first["run_id"]

    def test_non_owner_lineage_cannot_pass_single_owner_merge_gate(self):
        """Race 2 (fork merges past a blocked gate): the merge predicate's
        group (d) refuses a run_id that does not hold the live issue lease."""
        from tools.merge_predicate import _check_lease_ownership

        issue_number = 92029

        supervisor = _bare_ensure(issue_number, f"sdlc-local-{issue_number}-sup")
        assert not supervisor.get("blocked")

        failed: list[str] = []
        notes: list[str] = []
        _check_lease_ownership(issue_number, "never-held-this-lease", failed, notes)
        assert failed, "a non-lease-holding run_id must be refused"
        assert "does not hold the issue lease" in failed[0]

        # The genuine owner passes the same gate.
        failed2: list[str] = []
        notes2: list[str] = []
        _check_lease_ownership(issue_number, supervisor["run_id"], failed2, notes2)
        assert failed2 == []
        assert any("holds the issue lease" in n for n in notes2)
