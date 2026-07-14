"""Unit tests for models/session_lifecycle.py — session lifecycle management.

Tests cover:
- finalize_session() calls update_task_type_profile after auto_tag_session
- finalize_session() skips profile update when skip_auto_tag=True
- finalize_session() profile update failure never prevents session finalization
- StatusConflictError behavior
- finalize_session() validation (None session, non-terminal status)
"""

import json
import os
import subprocess
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
# finalize_session — session_archive export hook (docs/plans/session-archive-sqlite.md Task 2)
# ===================================================================


class TestFinalizeSessionArchiveHook:
    """Tests for the terminal-transition session_archive.export_session() hook."""

    def test_archive_export_called_after_save_on_terminal_status(self):
        """export_session is called with the session after finalize_session runs
        to a terminal status."""
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
            patch("agent.session_archive.export_session") as mock_export,
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            finalize_session(session, "completed")

        mock_export.assert_called_once_with(session)

    def test_archive_export_failure_does_not_break_finalization(self):
        """A raising export_session must not prevent finalize_session from
        completing or propagate out of it."""
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
            patch(
                "agent.session_archive.export_session",
                side_effect=RuntimeError("disk full"),
            ) as mock_export,
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh

            # Must not raise -- finalization must complete despite the archive failure.
            finalize_session(session, "completed")

        mock_export.assert_called_once_with(session)
        assert session.status == "completed"
        session.save.assert_called()


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


# ===================================================================
# touch_issue_lock — issue-level SDLC ownership lock (issues #1954/#2003)
# ===================================================================


class TestTouchIssueLock:
    """Tests for touch_issue_lock() under the run_id ownership model (#2003).

    Unlike claim_pending_run's real-Redis tests, these mock Redis SET/GET/
    EXPIRE directly: the tests need to control the exact stored JSON payload
    to simulate a foreign run_id cheaply, including the critical regression
    case (identical session_id, different run_id) that a real second Redis
    client couldn't easily reproduce in-process.
    """

    def test_acquire_when_key_does_not_exist(self):
        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = True
            result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954")

        assert result.acquired is True
        assert result.owner_session_id == "sdlc-local-1954"
        assert result.owner_run_id == "run-a"
        mock_redis.set.assert_called_once()
        _args, kwargs = mock_redis.set.call_args
        assert _args[0] == "session:issuelock:1954"
        assert kwargs.get("nx") is True
        payload = json.loads(_args[1])
        assert payload["run_id"] == "run-a"
        assert payload["session_id"] == "sdlc-local-1954"

    def test_renew_by_same_run_id(self):
        """Same run calling again (same run_id, any process) renews.

        Self-healing renewal (BLOCKER round-2, issue #2012): renewal is a
        full payload re-SET, never a bare EXPIRE -- see
        test_renewal_self_heals_legacy_payload_missing_target_repo below for
        the regression this replaces guarding against.
        """
        from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS, touch_issue_lock

        stored = json.dumps(
            {"run_id": "run-a", "session_id": "sdlc-local-1954", "pid": 1, "hostname": "h"}
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False  # NX fails -- key already exists
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954")

        assert result.acquired is True
        assert result.owner_session_id == "sdlc-local-1954"
        assert result.owner_run_id == "run-a"
        # Renewal re-SETs the full payload (never a bare EXPIRE) so a legacy
        # payload missing target_repo can self-heal on its next renewal tick.
        # set() is called twice: once for the initial SET-NX attempt (which
        # fails, hence the mocked return_value=False below) and once for the
        # renewal itself -- call_args below grabs the latter (most recent).
        mock_redis.expire.assert_not_called()
        assert mock_redis.set.call_count == 2
        _args, _kwargs = mock_redis.set.call_args
        assert _args[0] == "session:issuelock:1954"
        renewed_payload = json.loads(_args[1])
        assert renewed_payload["run_id"] == "run-a"
        assert renewed_payload["session_id"] == "sdlc-local-1954"
        assert renewed_payload["pid"] == 1
        assert renewed_payload["hostname"] == "h"
        assert _kwargs.get("ex") == ISSUE_LOCK_TTL_SECONDS

    def test_reject_foreign_run_same_session_id(self):
        """Critical regression: SAME session_id string, DIFFERENT run_id.
        Both a local CLI run and the worker resolve the identical
        deterministic session_id for the same issue -- comparing by
        session_id would make the lock a no-op. Ownership must be decided
        by run_id alone."""
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {
                "run_id": "foreign-run-abc",
                "session_id": "sdlc-local-1954",
                "pid": 999,
                "hostname": "other-host",
            }
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-b", session_id="sdlc-local-1954")

        assert result.acquired is False
        assert result.owner_session_id == "sdlc-local-1954"
        assert result.owner_run_id == "foreign-run-abc"
        mock_redis.expire.assert_not_called()

    def test_reject_by_non_owner_different_session_and_run(self):
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {
                "run_id": "foreign-run",
                "session_id": "worker-session-1954",
                "pid": 42,
                "hostname": "worker-host",
            }
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-b", session_id="sdlc-local-1954")

        assert result.acquired is False
        assert result.owner_session_id == "worker-session-1954"
        assert result.owner_run_id == "foreign-run"

    def test_mutation_without_run_id_never_mints(self):
        """A mutation call with no run_id must never SET NX (minting is
        exclusive to ensure_session) -- it reports the current holder."""
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {"run_id": "incumbent-run", "session_id": "sdlc-local-1954", "pid": 1, "hostname": "h"}
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, None, session_id="sdlc-local-1954")

        assert result.acquired is False
        assert result.owner_run_id == "incumbent-run"
        mock_redis.set.assert_not_called()
        mock_redis.expire.assert_not_called()

    def test_mutation_without_run_id_on_free_lock_does_not_mint(self):
        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = None
            result = touch_issue_lock(1954, None, session_id="sdlc-local-1954")

        assert result.acquired is True  # nothing blocking, but...
        mock_redis.set.assert_not_called()  # ...nothing was minted either

    def test_fail_open_on_redis_exception_names_error_class(self, caplog):
        """Redis-error fail-open (advisory lock) must log the swallowed error
        CLASS explicitly alongside the open behavior."""
        import logging

        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.side_effect = RuntimeError("redis down")
            with caplog.at_level(logging.WARNING):
                result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954")

        assert result.acquired is True
        assert any("failing open" in r.message for r in caplog.records)
        assert any("RuntimeError" in r.message for r in caplog.records)

    def test_malformed_legacy_value_treated_as_foreign(self):
        """A non-JSON stored value (malformed or legacy) must never raise --
        it fails toward 'not acquired', treated as a foreign holder."""
        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False
            mock_redis.get.return_value = "not-json-legacy-value"
            result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954")

        assert result.acquired is False

    def test_ttl_expiry_race_reclaim_succeeds(self):
        """SET NX fails (key existed at that instant), but the key has since
        expired by the time of the follow-up GET -- treated as free."""
        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False
            mock_redis.get.return_value = None
            result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954")

        assert result.acquired is True

    def test_peek_has_no_side_effect_on_free_lock(self):
        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = None
            result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954", peek=True)

        assert result.acquired is True
        assert result.owner_session_id is None
        mock_redis.set.assert_not_called()
        mock_redis.expire.assert_not_called()

    def test_peek_same_run_id_reports_acquired(self):
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {"run_id": "run-a", "session_id": "sdlc-local-1954", "pid": 1, "hostname": "h"}
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954", peek=True)

        assert result.acquired is True
        assert result.owner_run_id == "run-a"
        mock_redis.set.assert_not_called()
        mock_redis.expire.assert_not_called()

    def test_peek_reports_foreign_holder_without_mutating(self):
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {
                "run_id": "foreign-run",
                "session_id": "worker-session-1954",
                "pid": 42,
                "hostname": "worker-host",
            }
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-b", session_id="sdlc-local-1954", peek=True)

        assert result.acquired is False
        assert result.owner_session_id == "worker-session-1954"
        assert result.owner_run_id == "foreign-run"
        mock_redis.set.assert_not_called()
        mock_redis.expire.assert_not_called()

    def test_peek_flags_orphaned_lock_when_no_live_session_carries_run_id(self):
        """A held lock whose run_id matches no live session's active_run_id
        is a ghost (acquire->save crash window) -- flagged orphaned_lock."""
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {"run_id": "ghost-run", "session_id": "sdlc-local-1954", "pid": 1, "hostname": "h"}
        )

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []  # no live session carries ghost-run

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
            patch("models.agent_session.AgentSession", mock_as),
        ):
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-b", session_id="sdlc-local-1954", peek=True)

        assert result.acquired is False
        assert result.orphaned_lock is True

    def test_peek_does_not_flag_orphan_when_live_session_carries_run_id(self):
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {"run_id": "live-run", "session_id": "sdlc-local-1954", "pid": 1, "hostname": "h"}
        )

        live = MagicMock()
        live.active_run_id = "live-run"
        live.status = "running"
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [live]

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
            patch("models.agent_session.AgentSession", mock_as),
        ):
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-b", session_id="sdlc-local-1954", peek=True)

        assert result.acquired is False
        assert result.orphaned_lock is False

    def test_guard_falsy_issue_number_is_noop_fail_open(self):
        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            result_none = touch_issue_lock(None, "run-x", session_id="sdlc-local-x")
            result_zero = touch_issue_lock(0, "run-x", session_id="sdlc-local-x")

        assert result_none.acquired is True
        assert result_zero.acquired is True
        mock_redis.set.assert_not_called()
        mock_redis.get.assert_not_called()

    def test_default_ttl_is_issue_lock_ttl_seconds(self):
        from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS, touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = True
            touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954")

        _args, kwargs = mock_redis.set.call_args
        assert kwargs.get("ex") == ISSUE_LOCK_TTL_SECONDS


class TestTouchIssueLockTargetRepoPinning:
    """Tests for target_repo pinning on the issue lock payload (issue #2012).

    target_repo is resolved ONCE at lease-acquire time by the caller
    (_acquire_run_lock_and_bind) and passed into touch_issue_lock so every
    subsequent writer/reader of the issue-keyed PipelineLedger reads it from
    the lease instead of re-resolving via `gh repo view` per write.
    """

    def test_fresh_acquire_pins_target_repo_in_payload(self):
        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = True  # SET NX succeeds -- fresh acquire
            result = touch_issue_lock(
                1954,
                "run-a",
                session_id="sdlc-local-1954",
                target_repo="tomcounsell/ai",
            )

        assert result.acquired is True
        assert result.target_repo == "tomcounsell/ai"
        _args, _kwargs = mock_redis.set.call_args
        payload = json.loads(_args[1])
        assert payload["target_repo"] == "tomcounsell/ai"

    def test_reacquire_after_expiry_pins_target_repo(self):
        """Key existed at SET-NX time but expired before the follow-up GET
        (race window) -- this attempt succeeds and still pins target_repo."""
        from models.session_lifecycle import touch_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False  # NX fails
            mock_redis.get.return_value = None  # but key has since expired
            result = touch_issue_lock(
                1954,
                "run-a",
                session_id="sdlc-local-1954",
                target_repo="tomcounsell/ai",
            )

        assert result.acquired is True
        assert result.target_repo == "tomcounsell/ai"

    def test_peek_reports_pinned_target_repo_for_same_owner(self):
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {
                "run_id": "run-a",
                "session_id": "sdlc-local-1954",
                "pid": 1,
                "hostname": "h",
                "target_repo": "tomcounsell/ai",
            }
        )
        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954", peek=True)

        assert result.acquired is True
        assert result.target_repo == "tomcounsell/ai"

    def test_peek_reports_pinned_target_repo_for_foreign_owner(self):
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {
                "run_id": "run-a",
                "session_id": "sdlc-local-1954",
                "pid": 1,
                "hostname": "h",
                "target_repo": "tomcounsell/ai",
            }
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
            patch("models.agent_session.AgentSession", mock_as),
        ):
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-b", session_id="sdlc-local-1954", peek=True)

        assert result.acquired is False
        assert result.target_repo == "tomcounsell/ai"

    def test_renewal_self_heals_legacy_payload_missing_target_repo(self):
        """BLOCKER round-2 fix: a legacy payload (pre-#2012, no target_repo)
        gains the field on its next same-owner renewal instead of never --
        a bare EXPIRE would have left it permanently absent."""
        from models.session_lifecycle import touch_issue_lock

        legacy_stored = json.dumps(
            {"run_id": "run-a", "session_id": "sdlc-local-1954", "pid": 1, "hostname": "h"}
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False  # NX fails -- key already exists
            mock_redis.get.return_value = legacy_stored
            result = touch_issue_lock(
                1954,
                "run-a",
                session_id="sdlc-local-1954",
                target_repo="tomcounsell/ai",
            )

        assert result.acquired is True
        assert result.target_repo == "tomcounsell/ai"
        mock_redis.expire.assert_not_called()
        _args, _kwargs = mock_redis.set.call_args
        renewed_payload = json.loads(_args[1])
        assert renewed_payload["target_repo"] == "tomcounsell/ai"

    def test_renewal_preserves_pid_and_hostname_from_original_payload(self):
        """Regression guard for the 'never reconstruct a subset' fix-note:
        renewal must spread the existing payload, not hand-rebuild it --
        a hand-rebuilt subset would silently drop pid/hostname."""
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {
                "run_id": "run-a",
                "session_id": "sdlc-local-1954",
                "pid": 4242,
                "hostname": "original-host",
            }
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False
            mock_redis.get.return_value = stored
            touch_issue_lock(
                1954,
                "run-a",
                session_id="sdlc-local-1954",
                target_repo="tomcounsell/ai",
            )

        _args, _kwargs = mock_redis.set.call_args
        renewed_payload = json.loads(_args[1])
        assert renewed_payload["pid"] == 4242
        assert renewed_payload["hostname"] == "original-host"

    def test_renewal_without_target_repo_arg_preserves_existing_pinned_value(self):
        """A renewal call that doesn't pass target_repo (e.g. a caller that
        only peeked previously) must not blank out an already-pinned value."""
        from models.session_lifecycle import touch_issue_lock

        stored = json.dumps(
            {
                "run_id": "run-a",
                "session_id": "sdlc-local-1954",
                "pid": 1,
                "hostname": "h",
                "target_repo": "tomcounsell/ai",
            }
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.set.return_value = False
            mock_redis.get.return_value = stored
            result = touch_issue_lock(1954, "run-a", session_id="sdlc-local-1954")

        assert result.target_repo == "tomcounsell/ai"
        _args, _kwargs = mock_redis.set.call_args
        renewed_payload = json.loads(_args[1])
        assert renewed_payload["target_repo"] == "tomcounsell/ai"


class TestReleaseIssueLock:
    """Tests for release_issue_lock() -- the COMPARE-AND-DELETE release
    (issue #2003, cycle-2 CONCERN 2). Never a raw DEL."""

    def test_releases_when_run_id_matches(self):
        from models.session_lifecycle import release_issue_lock

        stored = json.dumps(
            {"run_id": "run-a", "session_id": "sdlc-local-2003", "pid": 1, "hostname": "h"}
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = stored
            mock_redis.eval.return_value = 1
            released = release_issue_lock(2003, "run-a")

        assert released is True
        # Compare-and-delete goes through Lua eval with the exact raw value,
        # never a bare DEL.
        mock_redis.eval.assert_called_once()
        eval_args = mock_redis.eval.call_args.args
        assert eval_args[1] == 1
        assert eval_args[2] == "session:issuelock:2003"
        assert eval_args[3] == stored
        mock_redis.delete.assert_not_called()

    def test_refuses_release_of_foreign_lock(self):
        from models.session_lifecycle import release_issue_lock

        stored = json.dumps(
            {"run_id": "successor-run", "session_id": "sdlc-local-2003", "pid": 2, "hostname": "h"}
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = stored
            released = release_issue_lock(2003, "run-a")

        assert released is False
        mock_redis.eval.assert_not_called()
        mock_redis.delete.assert_not_called()

    def test_missing_key_returns_false(self):
        from models.session_lifecycle import release_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.return_value = None
            released = release_issue_lock(2003, "run-a")

        assert released is False
        mock_redis.eval.assert_not_called()

    def test_redis_error_fails_safe_and_names_error_class(self, caplog):
        import logging

        from models.session_lifecycle import release_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.side_effect = RuntimeError("redis down")
            with caplog.at_level(logging.WARNING):
                released = release_issue_lock(2003, "run-a")

        assert released is False
        assert any("RuntimeError" in r.message for r in caplog.records)

    def test_falsy_args_are_noop(self):
        from models.session_lifecycle import release_issue_lock

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            assert release_issue_lock(None, "run-a") is False
            assert release_issue_lock(2003, None) is False

        mock_redis.get.assert_not_called()


_SUBPROCESS_LOCK_SCRIPT = (
    "import sys, json\n"
    "from models.session_lifecycle import touch_issue_lock\n"
    "mode, issue, run_id, session_id = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]\n"
    "res = touch_issue_lock(issue, run_id, session_id=session_id, peek=(mode == 'peek'))\n"
    "print(json.dumps({'acquired': res.acquired, 'owner_run_id': res.owner_run_id}))\n"
)


class TestRunIdentityAcrossProcesses:
    """The #1971 scenario, inverted (issue #2003): two SEPARATE OS processes
    sharing one run_id via explicit passing are the SAME owner -- with no
    SDLC_HOLDER_TOKEN in the environment (that seam is deleted). Runs the
    REAL touch_issue_lock() in real subprocesses against the per-worker test
    Redis db (REDIS_URL injection)."""

    @staticmethod
    def _subprocess_env():
        import popoto.redis_db as rdb

        kwargs = rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs
        host = kwargs.get("host") or "localhost"
        port = kwargs.get("port") or 6379
        db = kwargs.get("db", 1)
        env = {**os.environ, "REDIS_URL": f"redis://{host}:{port}/{db}"}
        env.pop("SDLC_HOLDER_TOKEN", None)  # the env seam is GONE -- prove it
        return env

    def _run_lock_subprocess(self, mode, issue, run_id, session_id):
        proc = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_LOCK_SCRIPT, mode, str(issue), run_id, session_id],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=self._subprocess_env(),
            timeout=60,
        )
        assert proc.returncode == 0, f"subprocess failed: {proc.stderr}"
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_run_identity_two_subprocesses_share_one_run_id(self):
        """Process A acquires with run_id R; process B (a fresh OS process,
        same R passed explicitly) peeks AND renews as the same owner."""
        run_id = "shared-run-identity-2003"

        result_a = self._run_lock_subprocess("acquire", 62003, run_id, "sdlc-local-62003")
        assert result_a["acquired"] is True

        result_b_peek = self._run_lock_subprocess("peek", 62003, run_id, "sdlc-local-62003")
        assert result_b_peek["acquired"] is True
        assert result_b_peek["owner_run_id"] == run_id

        result_b_renew = self._run_lock_subprocess("acquire", 62003, run_id, "sdlc-local-62003")
        assert result_b_renew["acquired"] is True

    def test_run_identity_foreign_run_id_blocked_across_processes(self):
        """A second process presenting a DIFFERENT run_id is a foreign run --
        blocked, with the incumbent's run_id surfaced."""
        result_a = self._run_lock_subprocess("acquire", 62004, "incumbent-run", "sdlc-local-62004")
        assert result_a["acquired"] is True

        result_b = self._run_lock_subprocess("acquire", 62004, "intruder-run", "sdlc-local-62004")
        assert result_b["acquired"] is False
        assert result_b["owner_run_id"] == "incumbent-run"


# ===================================================================
# finalize_session — single-owner lease release (issue #2026, WS1)
# ===================================================================


class TestFinalizeSessionLeaseRelease:
    """WS1 (#2026): finalize_session frees the issue lease IMMEDIATELY on every
    terminal transition (run completion and graceful failure), so the happy
    path never waits out the crash-backstop TTL. The release is compare-and-
    delete (release_issue_lock) and clears the supervised-run signal; both are
    best-effort and never break the terminal transition."""

    def _finalize(self, session, status="completed"):
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
            patch("agent.session_archive.export_session"),
            patch("models.session_lifecycle.release_issue_lock") as mock_release,
            patch("agent.supervised_run.clear_supervised_run_signal") as mock_clear,
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh
            finalize_session(session, status)
        return mock_release, mock_clear

    def test_completion_releases_lease_and_clears_signal(self):
        session = _make_session()
        session.issue_number = 2026
        session.active_run_id = "owner-run"
        session.working_dir = "/tmp/wt"

        mock_release, mock_clear = self._finalize(session, "completed")

        mock_release.assert_called_once_with(2026, "owner-run")
        mock_clear.assert_called_once_with(2026, "owner-run", working_dir="/tmp/wt")

    def test_graceful_failure_also_releases(self):
        session = _make_session()
        session.issue_number = 2026
        session.active_run_id = "owner-run"
        session.working_dir = None

        mock_release, _ = self._finalize(session, "failed")

        mock_release.assert_called_once_with(2026, "owner-run")

    def test_no_issue_or_run_id_is_noop(self):
        session = _make_session()
        session.issue_number = None
        session.active_run_id = None

        mock_release, mock_clear = self._finalize(session, "completed")

        mock_release.assert_not_called()
        mock_clear.assert_not_called()

    def test_release_failure_never_breaks_finalization(self):
        session = _make_session()
        session.issue_number = 2026
        session.active_run_id = "owner-run"
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
            patch("agent.session_archive.export_session"),
            patch(
                "models.session_lifecycle.release_issue_lock",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            mock_fresh = MagicMock()
            mock_fresh.status = "running"
            mock_cas.return_value = mock_fresh
            # Must not raise.
            finalize_session(session, "completed")

        assert session.status == "completed"


class TestIssueLockTtlDefault:
    """WS1 (#2026): the lease TTL default is sized to p99 stage wall time
    (1800s provisional/tunable), env-overridable via ISSUE_LOCK_TTL_SECONDS.
    The TTL is only the crash backstop -- the happy path releases explicitly."""

    def test_default_is_1800_seconds(self):
        import importlib
        import os

        assert os.environ.get("ISSUE_LOCK_TTL_SECONDS") is None, (
            "test env must not override ISSUE_LOCK_TTL_SECONDS"
        )
        import models.session_lifecycle as sl

        importlib.reload(sl)
        assert sl.ISSUE_LOCK_TTL_SECONDS == 1800
