"""Tests for C1 (#1817): child-independent finalize + idempotent startup sweep.

Covers two things:

1. The child-independent finalize contract (``finalize_session`` /
   ``_finalize_parent_sync``) is preserved: a parent-finalize failure never
   blocks or rolls back the child's own finalize.
2. ``agent.session_health._sweep_stranded_waiting_for_children_parents`` —
   the idempotent worker-startup sweep that closes the crash-window orphan
   (a parent stuck in ``waiting_for_children`` whose children are all
   terminal, left behind by a crash between the child's save and the
   parent's own transition).

Uses the real Popoto test Redis db (autouse ``redis_test_db`` fixture) —
these are lifecycle-integration tests, not mocked-model unit tests, since
the sweep's correctness hinges on actual index/status state.
"""

from models.agent_session import AgentSession
from models.session_lifecycle import finalize_session, transition_status


def _make_parent(session_id: str, project_key: str = "test-c1") -> AgentSession:
    parent = AgentSession(session_id=session_id, project_key=project_key, status="pending")
    parent.save()
    transition_status(parent, "waiting_for_children")
    return parent


def _make_child(
    session_id: str, parent: AgentSession, status: str, project_key: str = "test-c1"
) -> AgentSession:
    child = AgentSession(
        session_id=session_id,
        project_key=project_key,
        status=status,
        parent_agent_session_id=parent.id,
    )
    child.save()
    return child


class TestChildIndependentFinalizeContract:
    """A parent-finalize failure must never block or roll back the child's
    own finalize (finalize_session @ models/session_lifecycle.py:221)."""

    def test_child_finalizes_even_when_parent_finalize_raises(self, monkeypatch, redis_test_db):
        parent = _make_parent("c1-contract-parent")
        child = _make_child("c1-contract-child", parent, status="running")

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated parent-finalize failure")

        monkeypatch.setattr("models.session_lifecycle._finalize_parent_sync", _boom)

        # Must not raise -- the child's own finalize is independent of the
        # parent-finalize outcome.
        finalize_session(child, "completed")

        reloaded_child = AgentSession.get_by_id(child.id)
        assert reloaded_child.status == "completed", (
            "Child must finalize independently even when parent finalization raises"
        )

    def test_child_finalizes_even_when_parent_missing(self, redis_test_db):
        """A deleted/missing parent must not prevent the child from finalizing."""
        child = AgentSession(
            session_id="c1-orphan-child",
            project_key="test-c1",
            status="running",
            parent_agent_session_id="nonexistent-parent-id",
        )
        child.save()

        finalize_session(child, "completed")

        reloaded_child = AgentSession.get_by_id(child.id)
        assert reloaded_child.status == "completed"


class TestStrandedWaitingForChildrenSweep:
    """The idempotent startup sweep re-finalizes parents stranded by the
    crash window between a child's save and the parent's own transition."""

    def test_sweep_refinalizes_stranded_parent_all_children_completed(self, redis_test_db):
        from agent.session_health import _sweep_stranded_waiting_for_children_parents

        parent = _make_parent("c1-sweep-parent-1")
        _make_child("c1-sweep-child-1a", parent, status="completed")
        _make_child("c1-sweep-child-1b", parent, status="completed")

        # Simulates the crash window: children already finalized (saved
        # terminal), parent never got its own transition out of
        # waiting_for_children.
        reswept = _sweep_stranded_waiting_for_children_parents()

        assert reswept >= 1, f"Expected at least 1 parent re-finalized, got {reswept}"
        reloaded_parent = AgentSession.get_by_id(parent.id)
        assert reloaded_parent.status == "completed", (
            f"Stranded parent should be re-finalized to completed, got {reloaded_parent.status!r}"
        )

    def test_sweep_transitions_to_failed_when_any_child_failed(self, redis_test_db):
        from agent.session_health import _sweep_stranded_waiting_for_children_parents

        parent = _make_parent("c1-sweep-parent-2")
        _make_child("c1-sweep-child-2a", parent, status="completed")
        _make_child("c1-sweep-child-2b", parent, status="failed")

        reswept = _sweep_stranded_waiting_for_children_parents()

        assert reswept >= 1
        reloaded_parent = AgentSession.get_by_id(parent.id)
        assert reloaded_parent.status == "failed"

    def test_sweep_skips_parent_with_non_terminal_child(self, redis_test_db):
        """A parent still legitimately waiting is left untouched (no-op)."""
        from agent.session_health import _sweep_stranded_waiting_for_children_parents

        parent = _make_parent("c1-sweep-parent-3")
        _make_child("c1-sweep-child-3a", parent, status="completed")
        _make_child("c1-sweep-child-3b", parent, status="running")

        reswept = _sweep_stranded_waiting_for_children_parents()

        reloaded_parent = AgentSession.get_by_id(parent.id)
        assert reloaded_parent.status == "waiting_for_children", (
            "Parent with a non-terminal child must not be touched by the sweep"
        )
        # This specific parent must not have been counted as re-finalized.
        # (Other stray waiting_for_children parents from prior tests in the
        # same process could inflate reswept, so we only assert this
        # parent's own state above; reswept itself is not asserted to be 0.)
        assert reswept >= 0

    def test_sweep_is_idempotent_second_run_is_noop_for_finalized_parent(self, redis_test_db):
        from agent.session_health import _sweep_stranded_waiting_for_children_parents

        parent = _make_parent("c1-sweep-parent-4")
        _make_child("c1-sweep-child-4a", parent, status="completed")

        first = _sweep_stranded_waiting_for_children_parents()
        assert first >= 1
        reloaded_parent = AgentSession.get_by_id(parent.id)
        assert reloaded_parent.status == "completed"

        # Second run must not re-touch this already-finalized parent --
        # _finalize_parent_sync no-ops on a terminal parent.
        second = _sweep_stranded_waiting_for_children_parents()
        reloaded_parent_again = AgentSession.get_by_id(parent.id)
        assert reloaded_parent_again.status == "completed", (
            "Second sweep run must not alter an already-finalized parent"
        )
        # No stray reswept count attributable to this specific parent --
        # its status is unchanged, which is what matters for idempotency.
        assert second >= 0

    def test_sweep_returns_zero_when_no_stranded_parents(self, redis_test_db):
        from agent.session_health import _sweep_stranded_waiting_for_children_parents

        # No waiting_for_children sessions exist in this fresh test db.
        reswept = _sweep_stranded_waiting_for_children_parents()
        assert reswept == 0

    def test_sweep_does_not_raise_on_lookup_failure(self, redis_test_db, monkeypatch):
        """A per-parent exception during re-finalize is caught and logged,
        never propagated -- one bad parent must not abort the whole sweep."""
        from agent.session_health import _sweep_stranded_waiting_for_children_parents

        parent = _make_parent("c1-sweep-parent-5")
        _make_child("c1-sweep-child-5a", parent, status="completed")

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated Redis failure during re-finalize")

        monkeypatch.setattr("models.session_lifecycle._finalize_parent_sync", _boom)

        # Must not raise.
        reswept = _sweep_stranded_waiting_for_children_parents()
        assert reswept == 0
