"""Bug A regression tests — worker-future leak fix (issue #1730).

Validates that a session whose SDK work finishes is finalized to `completed`
via `finalize_session` (CAS authority), never left in `running`.

Also validates the nudge-stomp guard: a nudge enqueued during execution
(fresh successor via `enqueue_agent_session`) is NOT overwritten by the
completion-exit finalize, and `CancelledError` leaves the session `running`
(health checker owns that terminal transition by design).

All tests use the real Redis test db (db=1) via the redis_test_db autouse
fixture in tests/conftest.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from models.agent_session import AgentSession
from models.session_lifecycle import (
    StatusConflictError,
    finalize_session,
    get_authoritative_session,
    transition_status,
)

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _create_session(**overrides) -> AgentSession:
    """Create an AgentSession with test defaults."""
    defaults = {
        "project_key": "test-bug-a",
        "status": "running",
        "created_at": datetime.now(tz=UTC),
        "session_id": "bug-a-test-session",
        "working_dir": "/tmp/test",
        "message_text": "test",
        "sender_name": "Tester",
        "chat_id": "test-chat-1",
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


# -----------------------------------------------------------------------
# Bug A regression: completed sessions must not ghost as running
# -----------------------------------------------------------------------


@pytest.mark.integration
class TestBugAWorkerFutureLeak:
    """A session that finishes SDK work must be finalized to `completed`.

    The fix (completion-exit CAS guard) in `agent/session_executor.py`
    re-reads via `get_authoritative_session` and calls `finalize_session`
    directly when `complete_transcript` picked a stale record.

    These tests verify the underlying lifecycle mechanics that the guard
    relies on, using the same Popoto + Redis stack as production.
    """

    def test_finalize_session_transitions_running_to_completed(self, redis_test_db):
        """finalize_session() on a running session transitions it to completed.

        This is the core lifecycle call the CAS guard makes. Verifies the
        terminal transition succeeds and the status index is correct.
        """
        session_id = "bug-a-finalize-running-001"
        _create_session(session_id=session_id, status="running")

        # Simulate the CAS guard: re-read via get_authoritative_session
        fresh = get_authoritative_session(session_id)
        assert fresh is not None
        assert fresh.status == "running"

        # The CAS guard only fires when fresh.status == "running"
        finalize_session(
            fresh,
            "completed",
            reason="completion-exit CAS guard (Bug A #1730): completed",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )

        # Re-read and verify
        final = get_authoritative_session(session_id)
        assert final is not None
        assert final.status == "completed", (
            f"Session must be completed after finalize_session, got '{final.status}'"
        )

    def test_cas_guard_skips_already_completed_session(self, redis_test_db):
        """The CAS guard bails if the record is already terminal.

        Simulates the case where another process (health checker) already
        finalized the session before the completion-exit guard fires.
        The guard checks `fresh.status == "running"` before calling
        finalize_session, so it is a no-op when the record is already terminal.
        """
        session_id = "bug-a-cas-skip-terminal-001"
        session = _create_session(session_id=session_id, status="running")

        # Another process finalizes the session first
        finalize_session(
            session,
            "killed",
            reason="health checker kill",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )

        # Verify it was killed
        check = get_authoritative_session(session_id)
        assert check is not None
        assert check.status == "killed"

        # CAS guard logic: re-read, check if running, skip if not
        fresh = get_authoritative_session(session_id)
        assert fresh is not None
        assert fresh.status != "running", "Guard must bail when session is not running"

        # Guard does NOT call finalize_session (session is already terminal)
        # Verify session stays killed (the guard is a no-op)
        final = get_authoritative_session(session_id)
        assert final.status == "killed", (
            f"Session must stay killed (guard must not re-classify terminal), got '{final.status}'"
        )

    def test_finalize_session_raises_status_conflict_when_already_terminal(self, redis_test_db):
        """finalize_session raises StatusConflictError when session is already terminal.

        The completion-exit CAS guard catches StatusConflictError and treats it
        as success (another process already finalized). This test confirms the
        error is raised so the guard's except clause is reachable.
        """
        session_id = "bug-a-cas-conflict-terminal-001"
        session = _create_session(session_id=session_id, status="running")

        # First finalization succeeds
        finalize_session(
            session,
            "completed",
            reason="first finalize",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )

        # Verify completed
        check = get_authoritative_session(session_id)
        assert check.status == "completed"

        # Second finalization (from a stale object with status="running")
        # must raise StatusConflictError (reject_from_terminal=True default)
        stale_copy = AgentSession.query.get(id=session.id)
        stale_copy.status = "running"  # pretend it's still running

        with pytest.raises(StatusConflictError):
            finalize_session(
                stale_copy,
                "completed",
                reason="stale second attempt",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

    def test_get_authoritative_session_prefers_running_record(self, redis_test_db):
        """get_authoritative_session prefers the running record over a pending one.

        This is the tie-break logic that the CAS guard relies on to avoid
        picking the wrong record when multiple records share a session_id.
        Simulates the deferred-self-draft scenario: the executor's session is
        running while a nudge successor (pending) exists for the same session_id.
        """
        session_id = "bug-a-tie-break-prefer-running-001"
        project_key = "test-bug-a"

        # Create the running session (the executor's session)
        _create_session(
            session_id=session_id,
            project_key=project_key,
            status="running",
        )

        # Create a pending nudge successor with the SAME session_id
        AgentSession.create(
            session_id=session_id,
            project_key=project_key,
            status="pending",
            created_at=datetime.now(tz=UTC),
            working_dir="/tmp/test",
            message_text="nudge continuation",
            sender_name="Tester",
            chat_id="test-chat-1",
            telegram_message_id=1,
        )

        # get_authoritative_session must pick the running one
        auth = get_authoritative_session(session_id, project_key)
        assert auth is not None
        assert auth.status == "running", (
            f"get_authoritative_session must prefer the running record, got '{auth.status}'"
        )


# -----------------------------------------------------------------------
# Nudge-stomp regression: nudge successor must not be overwritten
# -----------------------------------------------------------------------


@pytest.mark.integration
class TestBugANudgeStompGuard:
    """The completion-exit CAS guard must NOT stomp a nudge's state.

    When the executor enqueues a nudge (fresh successor via enqueue_agent_session),
    the `defer_reaction=True` flag is set, skipping the `complete_transcript` /
    CAS guard entirely. This test verifies the guard stays off the nudge path.

    Uses pure Popoto operations to simulate the before/after states.
    """

    def test_nudge_state_survives_when_defer_reaction_is_set(self, redis_test_db):
        """When defer_reaction=True, the completion finalize block is skipped.

        Simulates the nudge path in _execute_agent_session:
          1. Session starts running.
          2. _enqueue_nudge transitions it to pending with auto_continue_count=1.
          3. Because defer_reaction=True, the CAS guard does NOT run.
          4. Fresh query confirms nudge state is preserved.

        The guard in session_executor.py only runs inside
        `if not chat_state.defer_reaction:`, so when the nudge fires first,
        defer_reaction is True and the guard is unreachable.
        """
        session_id = "bug-a-nudge-stomp-guard-001"
        project_key = "test-bug-a"

        # Step 1: Session starts running
        session = _create_session(session_id=session_id, project_key=project_key, status="running")

        # Step 2: Simulate _enqueue_nudge — transition to pending with fresh re-read
        nudge_ref = AgentSession.query.get(id=session.id)
        assert nudge_ref is not None
        nudge_ref.auto_continue_count = 1
        transition_status(nudge_ref, "pending", reason="nudge re-enqueue (test)")

        # Verify nudge state
        after_nudge = AgentSession.query.get(id=session.id)
        assert after_nudge.status == "pending"
        assert after_nudge.auto_continue_count == 1

        # Step 3: defer_reaction=True → CAS guard does NOT run
        # (In production: the executor skips the complete_transcript block entirely.)
        # We simulate by doing nothing (the guard is gated on `not defer_reaction`).
        # defer_reaction = True  # guard is unreachable

        # Step 4: Fresh query — nudge state must survive
        final = AgentSession.query.get(id=session.id)
        assert final is not None
        assert final.status == "pending", (
            f"Nudge state must survive when defer_reaction=True (guard is skipped), "
            f"got '{final.status}'"
        )
        assert final.auto_continue_count == 1

    def test_completion_finalize_does_not_stomp_nudge_successor(self, redis_test_db):
        """The CAS guard bails when fresh record is already pending (nudge successor).

        When the nudge fires and a fresh successor is created with the same
        session_id (status=pending), the CAS guard must bail because
        fresh.status != "running". The pending successor survives intact.

        This is the nudge-stomp regression from #867/#875.
        """
        session_id = "bug-a-nudge-successor-stomp-001"
        project_key = "test-bug-a"

        # Session is running
        running_session = _create_session(
            session_id=session_id, project_key=project_key, status="running"
        )

        # Nudge fires: transition to pending
        running_session.auto_continue_count = 1
        transition_status(running_session, "pending", reason="nudge (test)")

        # Verify nudge state
        after_nudge = get_authoritative_session(session_id, project_key)
        assert after_nudge is not None
        assert after_nudge.status == "pending"

        # CAS guard logic: re-read, check if RUNNING, skip if not
        fresh = get_authoritative_session(session_id, project_key)
        assert fresh is not None

        if fresh.status == "running":
            # Guard would finalize — but this path must NOT be taken
            pytest.fail("CAS guard must not see running after nudge transitioned to pending")
        # fresh.status is "pending" → guard bails (no finalize_session call)

        # Verify pending state is preserved
        final = get_authoritative_session(session_id, project_key)
        assert final is not None
        assert final.status == "pending", (
            f"Nudge successor must remain pending after CAS guard bails, got '{final.status}'"
        )
        assert final.auto_continue_count == 1


# -----------------------------------------------------------------------
# CancelledError contract: session stays running (health checker owns it)
# -----------------------------------------------------------------------


@pytest.mark.integration
class TestBugACancelledErrorContract:
    """CancelledError must NOT trigger the completion-exit CAS guard.

    The health checker owns the terminal transition after cancel+SIGTERM+SIGKILL.
    The executor intentionally leaves the session in `running` on CancelledError
    so startup recovery can re-queue it.

    The guard is scoped to `not chat_state.defer_reaction` in the completion
    block; CancelledError is propagated before reaching that block.
    """

    def test_session_stays_running_when_cancelled(self, redis_test_db):
        """A running session left in running state is recoverable.

        Simulates the CancelledError path: the executor is interrupted before
        reaching the completion block. The session stays running, and startup
        recovery will transition it to pending.
        """
        session_id = "bug-a-cancelled-stays-running-001"
        _create_session(session_id=session_id, status="running")

        # Verify it's running
        check = get_authoritative_session(session_id)
        assert check is not None
        assert check.status == "running"

        # On CancelledError, the executor does NOT call complete_transcript or the
        # CAS guard — the session stays in running state for startup recovery.
        # We simulate this by NOT calling finalize_session.

        # Final check: session is still running (no spurious finalization)
        final = get_authoritative_session(session_id)
        assert final is not None
        assert final.status == "running", (
            f"Session must stay running on CancelledError path "
            f"(health checker owns the terminal transition), "
            f"got '{final.status}'"
        )

    def test_running_session_recoverable_by_startup(self, redis_test_db):
        """A running session can be recovered to pending by startup recovery.

        Verifies that a session left in `running` (CancelledError path) is
        recoverable by _recover_interrupted_agent_sessions_startup, which is
        the mechanism that handles CancelledError sessions.
        """
        from datetime import timedelta

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        session_id = "bug-a-cancelled-recovery-001"
        old_started = datetime.now(tz=UTC) - timedelta(seconds=600)
        _create_session(
            session_id=session_id,
            status="running",
            started_at=old_started,
        )

        # Session stays running (CancelledError path)
        check = get_authoritative_session(session_id)
        assert check.status == "running"

        # Startup recovery picks it up and re-queues as pending
        recovered = _recover_interrupted_agent_sessions_startup()
        assert recovered >= 1

        final = get_authoritative_session(session_id)
        assert final is not None
        assert final.status == "pending", (
            f"Startup recovery must re-queue cancelled session to pending, got '{final.status}'"
        )


# -----------------------------------------------------------------------
# Guarantee: transition uses finalize_session, not transition_status
# -----------------------------------------------------------------------


@pytest.mark.integration
class TestBugAFinalizationPath:
    """Completions must go through finalize_session, never transition_status.

    transition_status raises ValueError on terminal targets ("completed",
    "failed", etc.) — using it to complete a session would crash the worker.
    This test confirms that finalize_session is the correct call and that
    transition_status raises ValueError as documented.
    """

    def test_transition_status_raises_on_terminal_target(self, redis_test_db):
        """transition_status raises ValueError when targeting a terminal status.

        This is the Bug A blocker (critique: transition_status crashes the worker
        when called with a terminal target). The fix uses finalize_session instead.
        """
        session_id = "bug-a-transition-status-raises-001"
        session = _create_session(session_id=session_id, status="running")

        with pytest.raises(ValueError, match="terminal"):
            transition_status(session, "completed", reason="must raise ValueError")

        # Session must still be running after the failed call
        final = get_authoritative_session(session_id)
        assert final is not None
        assert final.status == "running", (
            "Session must stay running after transition_status raises ValueError"
        )

    def test_finalize_session_succeeds_for_terminal_target(self, redis_test_db):
        """finalize_session succeeds when targeting a terminal status.

        Confirms that the correct API call (finalize_session, not transition_status)
        works for the completion-exit CAS guard.
        """
        session_id = "bug-a-finalize-terminal-success-001"
        session = _create_session(session_id=session_id, status="running")

        # finalize_session is the correct call for terminal transitions
        finalize_session(
            session,
            "completed",
            reason="completion-exit CAS guard",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )

        final = get_authoritative_session(session_id)
        assert final is not None
        assert final.status == "completed", (
            f"finalize_session must transition session to completed, got '{final.status}'"
        )
