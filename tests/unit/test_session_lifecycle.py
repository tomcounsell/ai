"""Unit tests for models/session_lifecycle.py — session lifecycle management.

Tests cover:
- finalize_session() calls update_task_type_profile after auto_tag_session
- finalize_session() skips profile update when skip_auto_tag=True
- finalize_session() profile update failure never prevents session finalization
- StatusConflictError behavior
- finalize_session() validation (None session, non-terminal status)
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from models.session_lifecycle import (
    RUN_CLAIM_TTL_SECONDS,
    StatusConflictError,
    claim_pending_run,
    finalize_session,
    transition_status,
)


def _make_session(session_id="test-session-lc", status="running", project_key="test"):
    """Create a minimal mock AgentSession for lifecycle tests."""
    session = MagicMock()
    session.session_id = session_id
    session.status = status
    session.project_key = project_key
    session.parent_agent_session_id = None
    session._saved_field_values = {}
    return session


def _build_mock_modules():
    """Build mock session_tags and task_type_profile modules for patching."""
    mock_auto_tag_module = MagicMock()
    mock_profile_module = MagicMock()
    return mock_auto_tag_module, mock_profile_module


# ===================================================================
# finalize_session — TaskTypeProfile update hook
# ===================================================================


class TestFinalizeSessionProfileHook:
    """Tests for the step 2.5 TaskTypeProfile update hook in finalize_session()."""

    def test_profile_update_called_when_auto_tag_runs(self):
        """update_task_type_profile is called when skip_auto_tag=False (default)."""
        session = _make_session()
        mock_auto_tag_module, mock_profile_module = _build_mock_modules()

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed")

        # Both auto_tag and profile update should have been called
        mock_auto_tag_module.auto_tag_session.assert_called_once_with(session.session_id)
        mock_profile_module.update_task_type_profile.assert_called_once_with(session.session_id)

    def test_profile_update_call_order(self):
        """update_task_type_profile is called AFTER auto_tag_session (and after status save)."""
        session = _make_session()
        call_order = []

        # Track save() calls to verify profile update comes after
        def tracking_save():
            call_order.append("session_save")

        session.save = tracking_save

        mock_auto_tag_module = MagicMock()
        mock_auto_tag_module.auto_tag_session = lambda sid: call_order.append("auto_tag")

        mock_profile_module = MagicMock()
        mock_profile_module.update_task_type_profile = lambda sid: call_order.append(
            "update_profile"
        )

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed")

        assert "auto_tag" in call_order
        assert "update_profile" in call_order
        # auto_tag must precede update_profile, and profile update must come after session save
        assert call_order.index("auto_tag") < call_order.index("update_profile")
        assert call_order.index("session_save") < call_order.index("update_profile")

    def test_profile_update_skipped_when_skip_auto_tag(self):
        """update_task_type_profile is NOT called when skip_auto_tag=True."""
        session = _make_session()
        mock_auto_tag_module, mock_profile_module = _build_mock_modules()

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed", skip_auto_tag=True)

        # Profile update must NOT have been called
        mock_profile_module.update_task_type_profile.assert_not_called()
        # auto_tag must also NOT have been called
        mock_auto_tag_module.auto_tag_session.assert_not_called()

    def test_profile_update_failure_does_not_prevent_finalization(self):
        """Exception in update_task_type_profile must not block session status save."""
        session = _make_session()
        mock_auto_tag_module = MagicMock()
        mock_profile_module = MagicMock()
        mock_profile_module.update_task_type_profile.side_effect = Exception("Redis is down")

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            # Must not raise — finalization must complete
            finalize_session(session, "completed")

        # Status must have been set to "completed"
        assert session.status == "completed"
        # save() must have been called
        session.save.assert_called()

    def test_finalization_sets_completed_status_despite_profile_error(self):
        """Session status reaches 'completed' even when profile update throws."""
        session = _make_session(status="running")
        mock_auto_tag_module = MagicMock()
        mock_profile_module = MagicMock()
        mock_profile_module.update_task_type_profile.side_effect = RuntimeError(
            "intentional failure"
        )

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed", reason="test")

        assert session.status == "completed"
        assert session.completed_at is not None


# ===================================================================
# finalize_session — idempotency
# ===================================================================


class TestFinalizeSessionIdempotency:
    def test_idempotent_when_already_in_target_status(self):
        """finalize_session is a no-op if session already in target terminal state."""
        session = _make_session(status="completed")

        finalize_session(session, "completed")

        # save must NOT have been called (skipped early)
        session.save.assert_not_called()


# ===================================================================
# finalize_session — validation
# ===================================================================


class TestFinalizeSessionValidation:
    def test_raises_for_non_terminal_status(self):
        """finalize_session raises ValueError for non-terminal statuses."""
        session = _make_session()
        with pytest.raises(ValueError, match="terminal"):
            finalize_session(session, "running")

    def test_raises_for_none_session(self):
        """finalize_session raises ValueError when session is None."""
        with pytest.raises(ValueError, match="session must not be None"):
            finalize_session(None, "completed")


# ===================================================================
# finalize_session — reject_from_terminal guard (kill-is-terminal, #1208)
# ===================================================================


class TestFinalizeSessionRejectFromTerminal:
    """Tests for the kill-is-terminal invariant on finalize_session().

    These test names embed ``reject_from_terminal`` so the verification command
    ``pytest -k reject_from_terminal`` (from the plan's Verification table)
    selects the full suite by parameter name.
    """

    def test_finalize_session_reject_from_terminal_blocks_by_default(self):
        """A killed session cannot be flipped to completed by default."""
        session = _make_session(status="killed")

        with pytest.raises(StatusConflictError) as exc_info:
            finalize_session(session, "completed")

        # The error must surface the opt-out instruction so the operator can
        # see how to override if the call was legitimate.
        assert "reject_from_terminal=False" in str(exc_info.value)
        assert exc_info.value.expected_status == "killed"
        assert exc_info.value.actual_status == "completed"

        # No mutation should have happened.
        session.save.assert_not_called()
        assert session.status == "killed"

    def test_finalize_session_reject_from_terminal_opt_out_succeeds(self):
        """Passing reject_from_terminal=False permits terminal->terminal escalation."""
        session = _make_session(status="abandoned")
        mock_auto_tag_module, mock_profile_module = _build_mock_modules()

        with (
            patch("models.session_lifecycle.get_authoritative_session") as mock_cas,
            patch.dict(
                sys.modules,
                {
                    "tools.session_tags": mock_auto_tag_module,
                    "models.task_type_profile": mock_profile_module,
                },
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "abandoned"
            mock_cas.return_value = mock_fresh

            # Must not raise — explicit opt-out grants the escalation.
            finalize_session(session, "failed", reject_from_terminal=False)

        assert session.status == "failed"
        session.save.assert_called()

    def test_finalize_session_reject_from_terminal_idempotent_same_state(self):
        """Re-finalizing with the same terminal status is a no-op (regression)."""
        session = _make_session(status="killed")

        finalize_session(session, "killed")

        # Idempotent path runs before the reject_from_terminal guard, so save() is skipped.
        session.save.assert_not_called()
        assert session.status == "killed"

    def test_finalize_session_reject_from_terminal_completed_to_killed(self):
        """The guard fires for any terminal-to-different-terminal pair, not just killed->X."""
        session = _make_session(status="completed")

        with pytest.raises(StatusConflictError):
            finalize_session(session, "killed")

        session.save.assert_not_called()


# ===================================================================
# claim_pending_run — narrow SETNX gate for pending->running (issue #1817 B2)
# ===================================================================


class TestClaimPendingRun:
    """Tests for the narrow pending->running run-claim.

    Uses the real Redis client (matching the existing SETNX-idiom test style
    elsewhere, e.g. tests/unit/test_dedup.py::TestMessageClaim) since
    claim_pending_run is a thin, real-Redis SETNX wrapper -- mocking it would
    just re-test the mock.
    """

    def _cleanup(self, session_id):
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.delete(f"session:runclaim:{session_id}")

    def teardown_method(self):
        self._cleanup("test-runclaim-fresh")
        self._cleanup("test-runclaim-contested")

    def test_claim_fresh_session_succeeds(self):
        assert claim_pending_run("test-runclaim-fresh", worker_id="worker-A") is True

    def test_second_claim_on_same_session_fails(self):
        """Two concurrent claimants on the SAME session_id: exactly one wins."""
        first = claim_pending_run("test-runclaim-contested", worker_id="worker-A")
        second = claim_pending_run("test-runclaim-contested", worker_id="worker-B")
        assert first is True
        assert second is False

    def test_claim_fails_open_on_redis_error(self):
        """A Redis error must fail OPEN (return True), not starve the queue."""
        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.side_effect = RuntimeError("redis down")
            result = claim_pending_run("test-runclaim-fresh", worker_id="worker-A")
        assert result is True

    def test_ttl_is_short(self):
        """RUN_CLAIM_TTL_SECONDS must be short -- only needs to cover the
        query -> transition_status window, not a long-lived lock."""
        assert isinstance(RUN_CLAIM_TTL_SECONDS, int)
        assert 0 < RUN_CLAIM_TTL_SECONDS <= 120


class TestConcurrentPendingRunClaim:
    """End-to-end (within this module) proof that the run-claim + generic CAS
    together guarantee exactly one actor transitions a pending session to
    running (issue #1817 B2).
    """

    def _cleanup(self, session_id):
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.delete(f"session:runclaim:{session_id}")

    def teardown_method(self):
        self._cleanup("test-runclaim-e2e-1")
        self._cleanup("test-runclaim-e2e-2")

    def test_two_claimants_exactly_one_wins_and_transitions(self):
        """Winner transitions to running; loser is stopped by the run-claim
        alone (never even attempts transition_status)."""
        session_id = "test-runclaim-e2e-1"
        session = _make_session(session_id=session_id, status="pending")

        winner_claim = claim_pending_run(session_id, worker_id="worker-A")
        loser_claim = claim_pending_run(session_id, worker_id="worker-B")
        assert winner_claim is True
        assert loser_claim is False

        # Only the winner proceeds to call transition_status -- mirrors the
        # real call-site pattern in agent/session_pickup.py.
        with patch("models.session_lifecycle.get_authoritative_session") as mock_cas:
            mock_fresh = MagicMock()
            mock_fresh.status = "pending"
            mock_cas.return_value = mock_fresh

            transition_status(session, "running", reason="worker picked up session")

        assert session.status == "running"
        session.save.assert_called()

    def test_claim_bypass_still_blocked_by_generic_cas(self):
        """Anti-criterion (round-4 BLOCKER): even if a caller somehow bypasses
        the run-claim, the generic CAS inside transition_status() -- the
        ``on_disk_status != current_status`` compare -- remains a second line
        of defense and rejects the stale-status transition.
        """
        session_id = "test-runclaim-e2e-2"
        # Caller's in-memory snapshot still says "pending", but the on-disk
        # record (as re-read by transition_status's CAS) already says
        # "running" -- e.g. a peer won and completed the transition first.
        stale_session = _make_session(session_id=session_id, status="pending")

        with patch("models.session_lifecycle.get_authoritative_session") as mock_cas:
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            with pytest.raises(StatusConflictError) as exc_info:
                transition_status(stale_session, "running", reason="stale bypass attempt")

        assert exc_info.value.expected_status == "pending"
        assert exc_info.value.actual_status == "running"
        stale_session.save.assert_not_called()


class TestGenericCasStillGovernsWaitingForChildren:
    """Anti-criterion (round-4 BLOCKER): the generic CAS compare must still be
    present in models/session_lifecycle.py and still govern C1's
    waiting_for_children transition. An earlier plan draft proposed deleting
    this compare; a critique round flagged that as a BLOCKER because it
    would strip optimistic-concurrency protection from every non-terminal
    status edge in the system, not just pending->running.
    """

    def test_cas_compare_present_in_source(self):
        import inspect

        import models.session_lifecycle as lifecycle_module

        source = inspect.getsource(lifecycle_module)
        assert "on_disk_status != current_status" in source, (
            "the generic optimistic-concurrency CAS compare must remain in "
            "transition_status() -- see the BLOCKER rationale in "
            "docs/plans/correctness-delivery-integrity.md"
        )

    def test_finalize_parent_sync_still_calls_transition_status_for_waiting_for_children(self):
        """C1's waiting_for_children transition still routes through
        transition_status() (and therefore through the generic CAS), rather
        than through some bypass path added alongside the new run-claim."""
        import inspect

        import models.session_lifecycle as lifecycle_module

        source = inspect.getsource(lifecycle_module._finalize_parent_sync)
        assert 'transition_status(parent, "waiting_for_children"' in source, (
            "C1's parent transition must still call the shared transition_status() "
            "helper so the generic CAS applies to it"
        )
