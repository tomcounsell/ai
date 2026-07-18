"""Regression tests for session recovery respawn safety.

Each test proves that a specific recovery mechanism cannot respawn a session
that is in a terminal status (completed, failed, killed, abandoned, cancelled).

Covers all 5 active mechanisms + 2 confirmed-safe mechanisms:
1. _recover_interrupted_agent_sessions_startup() — startup recovery
2. _agent_session_health_check() — periodic health check
3. _agent_session_hierarchy_health_check() — parent/child health check
4. _enqueue_nudge() main path — nudge re-enqueue
5. _enqueue_nudge() fallback path — nudge fallback recreate
6. determine_delivery_action() — delivery routing (all terminal statuses)
7. check_revival() — revival detection
8. Message intake path — intake terminal guard (#730)
9. Session watchdog — confirmed safe (only sets flags, never mutates status)
10. Bridge watchdog — confirmed safe (no AgentSession imports)

Also tests transition_status() reject_from_terminal guard.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from config.enums import SessionType
from models.session_lifecycle import TERMINAL_STATUSES, transition_status


def _mock_agent_session(**kwargs):
    """Create a mock AgentSession with sensible defaults."""
    defaults = {
        "session_id": "test-session-123",
        "agent_session_id": "agent-sess-456",
        "status": "running",
        "project_key": "test-project",
        "chat_id": "12345",
        "working_dir": "/tmp/test",
        "priority": "normal",
        "started_at": 1000.0,
        "completed_at": None,
        "parent_agent_session_id": None,
        "message_text": "test message",
        "auto_continue_count": 0,
        "task_list_id": None,
        "slug": None,
        "session_type": None,  # default None so tests must opt into a specific type
        "initial_telegram_message": {"message_text": "test", "sender_name": "user"},
        # Real AgentSession rows default is_ledger=False (models/agent_session.py).
        # Explicit here because MagicMock auto-vivifies any unset attribute as a
        # truthy child Mock, which would make getattr(entry, "is_ledger", False)
        # in agent/session_health.py's _is_ledger() guard misfire (#2042).
        "is_ledger": False,
        # Ownership stamp (#2148): None = legacy row → recovery falls back to
        # the age guard. Same auto-vivification rationale as is_ledger above.
        "worker_pid": None,
        # Detached-harness pids: None so _terminate_detached_harness no-ops.
        "claude_pid": None,
        "pm_pid": None,
    }
    defaults.update(kwargs)
    session = MagicMock()
    for k, v in defaults.items():
        setattr(session, k, v)
    session.save = MagicMock()
    session.delete = MagicMock()
    session.log_lifecycle_transition = MagicMock()
    return session


class TestDetermineDeliveryActionTerminalStatuses:
    """determine_delivery_action() returns deliver_already_completed for ALL terminal statuses."""

    @pytest.mark.parametrize("terminal_status", sorted(TERMINAL_STATUSES))
    def test_terminal_status_returns_already_completed(self, terminal_status):
        """Each terminal status should return deliver_already_completed."""
        from agent.agent_session_queue import determine_delivery_action

        action = determine_delivery_action(
            msg="some output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=5,
            session_status=terminal_status,
        )
        assert action == "deliver_already_completed", (
            f"Terminal status {terminal_status!r} should return "
            f"deliver_already_completed, got {action!r}"
        )

    def test_non_terminal_does_not_return_already_completed(self):
        """Non-terminal statuses should NOT return deliver_already_completed."""
        from agent.agent_session_queue import determine_delivery_action

        action = determine_delivery_action(
            msg="some output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=5,
            session_status="running",
        )
        assert action != "deliver_already_completed"


class TestEnqueueNudgeTerminalGuard:
    """_enqueue_nudge() returns early when session is in a terminal status."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal_status", sorted(TERMINAL_STATUSES))
    async def test_nudge_main_path_skips_terminal(self, terminal_status):
        """_enqueue_nudge() entry guard blocks terminal sessions from being nudged."""
        from agent.agent_session_queue import _enqueue_nudge

        session = _mock_agent_session(status=terminal_status)

        # If the guard works, it returns early without querying Redis or calling
        # transition_status. We patch AgentSession.query to detect any leak.
        with patch("agent.agent_session_queue.AgentSession") as mock_as:
            mock_as.query.filter.return_value = []
            await _enqueue_nudge(
                session=session,
                branch_name="session/test",
                task_list_id="tl-1",
                auto_continue_count=1,
                output_msg="agent output",
                nudge_feedback="continue",
            )
            # Should NOT have queried Redis (guard returns before that)
            mock_as.query.filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_fallback_path_skips_terminal(self):
        """_enqueue_nudge() fallback path blocks terminal sessions from async_create."""
        from agent.agent_session_queue import _enqueue_nudge

        # Session is completed but Redis returns empty (triggering fallback path)
        session = _mock_agent_session(status="completed")

        with (
            patch("agent.agent_session_queue.AgentSession") as mock_as,
            patch("agent.agent_session_queue._diagnose_missing_session", return_value="diag"),
            patch("agent.agent_session_queue._extract_agent_session_fields", return_value={}),
        ):
            # The entry guard catches this before we even get to the fallback,
            # so async_create should never be called.
            mock_as.query.filter.return_value = []
            mock_as.async_create = MagicMock()
            await _enqueue_nudge(
                session=session,
                branch_name="session/test",
                task_list_id="tl-1",
                auto_continue_count=1,
                output_msg="agent output",
            )
            mock_as.async_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_reread_guard_catches_late_terminal(self):
        """Main path re-read guard catches session that became terminal after entry check."""
        from agent.agent_session_queue import _enqueue_nudge

        # Session starts as "running" (passes entry guard) but Redis returns
        # a version that is now "completed" (another process finalized it).
        session = _mock_agent_session(status="running")
        completed_session = _mock_agent_session(status="completed")

        with patch(
            "models.session_lifecycle.get_authoritative_session",
            return_value=completed_session,
        ):
            await _enqueue_nudge(
                session=session,
                branch_name="session/test",
                task_list_id="tl-1",
                auto_continue_count=1,
                output_msg="agent output",
            )
            # Should NOT have called transition_status (guard returns early)
            # Verify by checking that the completed session was not modified
            assert completed_session.status == "completed"


class TestCheckRevivalTerminalFilter:
    """check_revival() filters out branches whose sessions have terminal status siblings."""

    def test_revival_skips_completed_session_branch(self):
        """check_revival() returns None when all candidate branches have terminal siblings."""
        from agent.agent_session_queue import check_revival

        pending_session = _mock_agent_session(
            session_id="chat-123-msg-1", status="pending", chat_id="12345"
        )
        completed_session = _mock_agent_session(
            session_id="chat-123-msg-1", status="completed", chat_id="12345"
        )

        def mock_filter(**kwargs):
            status = kwargs.get("status")
            if status == "pending":
                return [pending_session]
            if status == "running":
                return []
            if status == "completed":
                return [completed_session]
            # Other terminal statuses
            return []

        with (
            patch("agent.session_revival.AgentSession") as mock_as,
            patch("agent.session_revival._load_cooldowns", return_value={}),
        ):
            mock_as.query.filter.side_effect = mock_filter
            result = check_revival(
                project_key="test-project",
                working_dir="/tmp/test",
                chat_id="12345",
            )
        # Should return None because the only candidate branch has a terminal sibling
        assert result is None

    def test_revival_passes_non_terminal_branches(self):
        """check_revival() returns revival info for branches without terminal siblings."""
        from agent.agent_session_queue import check_revival

        pending_session = _mock_agent_session(
            session_id="chat-123-msg-2", status="pending", chat_id="12345"
        )

        def mock_filter(**kwargs):
            status = kwargs.get("status")
            if status == "pending":
                return [pending_session]
            if status == "running":
                return []
            # No terminal sessions exist
            return []

        with (
            patch("agent.session_revival.AgentSession") as mock_as,
            patch("agent.session_revival._load_cooldowns", return_value={}),
            patch("subprocess.run") as mock_run,
            patch("agent.session_revival.get_branch_state") as mock_bs,
        ):
            mock_as.query.filter.side_effect = mock_filter
            # Branch exists in git
            mock_run.return_value = SimpleNamespace(stdout="  session/chat-123-msg-2\n")
            mock_bs.return_value = SimpleNamespace(has_uncommitted_changes=False, active_plan=None)
            result = check_revival(
                project_key="test-project",
                working_dir="/tmp/test",
                chat_id="12345",
            )
        assert result is not None
        assert result["branch"] == "session/chat-123-msg-2"


class TestTransitionStatusRejectFromTerminal:
    """transition_status() rejects terminal->non-terminal by default."""

    @pytest.mark.parametrize("terminal_status", sorted(TERMINAL_STATUSES))
    def test_rejects_from_terminal_by_default(self, terminal_status):
        """transition_status() raises ValueError for terminal->non-terminal."""
        session = _mock_agent_session(status=terminal_status)
        with pytest.raises(ValueError, match="terminal status"):
            transition_status(session, "pending", "test")

    @pytest.mark.parametrize("terminal_status", sorted(TERMINAL_STATUSES))
    def test_allows_from_terminal_when_explicitly_permitted(self, terminal_status):
        """transition_status() allows terminal->non-terminal with reject_from_terminal=False."""
        session = _mock_agent_session(status=terminal_status)
        # Should NOT raise
        transition_status(
            session, "superseded", "intentional supersede", reject_from_terminal=False
        )
        assert session.status == "superseded"

    def test_completed_to_superseded_with_reject_false(self):
        """Protects the _mark_superseded() path: completed->superseded with explicit opt-out."""
        session = _mock_agent_session(status="completed")
        transition_status(
            session,
            "superseded",
            reason="superseded by new session",
            reject_from_terminal=False,
        )
        assert session.status == "superseded"
        session.save.assert_called_once()

    def test_error_message_includes_both_statuses(self):
        """Error message should include both current and target status for debugging."""
        session = _mock_agent_session(status="completed")
        with pytest.raises(ValueError, match="completed") as exc_info:
            transition_status(session, "pending", "bad transition")
        assert "pending" in str(exc_info.value)

    def test_non_terminal_to_non_terminal_unaffected(self):
        """Default reject_from_terminal=True does not affect non-terminal->non-terminal."""
        session = _mock_agent_session(status="running")
        transition_status(session, "pending", "re-enqueue")
        assert session.status == "pending"


class TestStartupRecoverySkipsTerminal:
    """_recover_interrupted_agent_sessions_startup() only recovers running sessions."""

    def test_startup_recovery_only_queries_running(self):
        """Startup recovery queries status='running' only — terminal sessions are untouched.

        Also asserts that local sessions in the stale set are abandoned (not re-queued),
        and that bridge sessions are recovered.
        """
        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        # The function queries AgentSession.query.filter(status="running").
        # Terminal sessions are never queried, so they cannot be recovered.
        with patch("agent.session_health.AgentSession") as mock_as:
            mock_as.query.filter.return_value = []
            count = _recover_interrupted_agent_sessions_startup()

        assert count == 0
        # Verify it only queried "running" status
        mock_as.query.filter.assert_called_once_with(status="running")

    def test_startup_recovery_does_not_requeue_local_teammate_session(self):
        """Startup recovery does not call update_session('pending') for local teammate sessions."""
        import time

        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_MIN_RUNNING,
            _recover_interrupted_agent_sessions_startup,
        )

        # Build a stale local teammate session (started_at well before the cutoff)
        local_session = _mock_agent_session(
            session_id="local-abc123",
            agent_session_id="agent-local-001",
            session_type=SessionType.TEAMMATE,
            started_at=time.time() - AGENT_SESSION_HEALTH_MIN_RUNNING - 600,
        )

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session"),
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [local_session]

            count = _recover_interrupted_agent_sessions_startup()

        # Local teammate sessions do not increment the count
        assert count == 0
        # update_session (re-queue to pending) must NOT be called for local teammate sessions
        mock_update.assert_not_called()

    def test_startup_recovery_requeues_local_eng_session(self):
        """Startup recovery calls update_session('pending') for local eng sessions (#1092)."""
        import time

        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_MIN_RUNNING,
            _recover_interrupted_agent_sessions_startup,
        )

        # Build a stale local eng session (started_at well before the cutoff)
        local_dev_session = _mock_agent_session(
            session_id="local-dev-xyz",
            agent_session_id="agent-local-dev-001",
            session_type=SessionType.ENG,
            started_at=time.time() - AGENT_SESSION_HEALTH_MIN_RUNNING - 600,
        )

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [local_dev_session]

            count = _recover_interrupted_agent_sessions_startup()

        # Local dev sessions ARE counted as recovered
        assert count == 1
        # update_session must be called with new_status="pending" and CAS on running
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs.get("new_status") == "pending"
        assert call_kwargs.get("expected_status") == "running"
        # finalize_session must NOT be called for local dev sessions
        mock_finalize.assert_not_called()


class TestStartupRecoveryLocalSessionGuard:
    """_recover_interrupted_agent_sessions_startup() abandons local sessions, not bridge."""

    def _stale_session(self, **kwargs):
        """Create a mock session that is old enough to be considered stale."""
        import time

        from agent.agent_session_queue import AGENT_SESSION_HEALTH_MIN_RUNNING

        defaults = dict(
            session_id="test-session",
            agent_session_id="agent-sess",
            worker_key="ai",
            started_at=time.time() - AGENT_SESSION_HEALTH_MIN_RUNNING - 600,
            message_text="test",
        )
        defaults.update(kwargs)
        return _mock_agent_session(**defaults)

    def test_startup_recovery_abandons_local_non_eng_sessions(self):
        """Local non-ENG session (session_id starts with 'local') is finalized as 'abandoned'."""
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        local_session = self._stale_session(
            session_id="local-abc123",
            agent_session_id="agent-local-001",
            session_type=SessionType.TEAMMATE,
        )

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [local_session]

            count = _recover_interrupted_agent_sessions_startup()

        # Count must be 0 — local non-ENG sessions are not "recovered" (they are abandoned)
        assert count == 0
        # finalize_session must have been called with "abandoned"
        mock_finalize.assert_called_once()
        call_args = mock_finalize.call_args
        assert (
            call_args[0][1] == "abandoned"
            or call_args[1].get("status") == "abandoned"
            or (len(call_args[0]) >= 2 and call_args[0][1] == "abandoned")
        )

    def test_startup_recovery_abandons_local_teammate_sessions(self):
        """Local teammate session (session_id starts with 'local') is finalized as 'abandoned'.

        The #986 hijack rationale applies to teammate sessions just as it does to PM —
        both are conversational sessions a human may be interactively driving via the
        Claude Code CLI at the same claude_session_uuid.
        """
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        local_session = self._stale_session(
            session_id="local-teammate-abc",
            agent_session_id="agent-local-teammate-001",
            session_type=SessionType.TEAMMATE,
        )

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [local_session]

            count = _recover_interrupted_agent_sessions_startup()

        assert count == 0
        mock_finalize.assert_called_once()
        # update_session must NOT be called for teammate sessions
        mock_update.assert_not_called()

    def test_startup_recovery_recovers_local_eng_sessions(self):
        """Local eng session is re-queued to pending via update_session (#1092).

        Eng sessions are worker-owned (spawned by the worker), so there is no
        human CLI competing for the claude_session_uuid. Re-queue on worker
        restart instead of abandoning.
        """
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        local_session = self._stale_session(
            session_id="local-dev-abc",
            agent_session_id="agent-local-dev-001",
            session_type=SessionType.ENG,
        )

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [local_session]

            count = _recover_interrupted_agent_sessions_startup()

        # Local eng sessions are counted as recovered
        assert count == 1
        # update_session must be called with new_status="pending" and CAS on running
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs.get("new_status") == "pending"
        assert call_kwargs.get("expected_status") == "running"
        # finalize_session must NOT be called for local eng sessions
        mock_finalize.assert_not_called()

    def test_startup_recovery_local_eng_session_type_none_defaults_to_abandon(self):
        """Legacy record (session_type=None) on a local session routes to abandon (#1092).

        session_type is gated on explicit equality with SessionType.ENG, so legacy
        pre-migration records with session_type=None fall through to the safer
        abandon path — same as teammate. This locks in the conservative default.
        """
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        local_session = self._stale_session(
            session_id="local-legacy-abc",
            agent_session_id="agent-local-legacy-001",
            session_type=None,  # legacy record
        )

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [local_session]

            count = _recover_interrupted_agent_sessions_startup()

        assert count == 0
        # finalize_session (abandon) MUST be called for legacy records
        mock_finalize.assert_called_once()
        call_args = mock_finalize.call_args
        assert call_args[0][1] == "abandoned" or call_args[1].get("status") == "abandoned"
        # update_session must NOT be called for legacy records
        mock_update.assert_not_called()

    def test_startup_recovery_recovers_bridge_sessions(self):
        """Bridge session (session_id does NOT start with 'local') is reset to pending."""
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        bridge_session = self._stale_session(
            session_id="tg-xyz789",
            agent_session_id="agent-bridge-001",
        )

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [bridge_session]

            count = _recover_interrupted_agent_sessions_startup()

        # Bridge sessions increment the count
        assert count == 1
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs.get("new_status") == "pending"

    def test_startup_recovery_mixed_local_and_bridge(self):
        """Mixed stale sessions: local teammate → abandoned, local eng → pending, bridge → pending.

        Per #1092, local eng sessions are re-queued like bridge sessions. Only local
        teammate (and legacy session_type=None) sessions are abandoned.
        """
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        local_pm_session = self._stale_session(
            session_id="local-pm-abc",
            agent_session_id="agent-local-pm-002",
            session_type=SessionType.TEAMMATE,
        )
        local_dev_session = self._stale_session(
            session_id="local-dev-def",
            agent_session_id="agent-local-dev-002",
            session_type=SessionType.ENG,
        )
        bridge_session = self._stale_session(
            session_id="tg-xyz",
            agent_session_id="agent-bridge-002",
        )

        finalize_calls = []
        update_calls = []

        def fake_finalize(entry, status, **kwargs):
            finalize_calls.append((entry, status, kwargs))

        def fake_update(session_id, **kwargs):
            update_calls.append((session_id, kwargs))

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session", side_effect=fake_finalize),
            patch("models.session_lifecycle.update_session", side_effect=fake_update),
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [
                local_pm_session,
                local_dev_session,
                bridge_session,
            ]

            count = _recover_interrupted_agent_sessions_startup()

        # Bridge + local dev both count as recovered (#1092)
        assert count == 2
        # Only the local PM session is finalized as abandoned
        assert len(finalize_calls) == 1
        assert finalize_calls[0][0] is local_pm_session
        assert finalize_calls[0][1] == "abandoned"
        # Both local dev and bridge are re-queued to pending
        assert len(update_calls) == 2
        updated_session_ids = {c[0] for c in update_calls}
        assert updated_session_ids == {"local-dev-def", "tg-xyz"}
        for _session_id, kwargs in update_calls:
            assert kwargs["new_status"] == "pending"
            assert kwargs["expected_status"] == "running"


class TestStartupRecoveryLedgerGuard:
    """_recover_interrupted_agent_sessions_startup() skips is_ledger=True anchors (#2042).

    Non-executable CLI anchor rows created by ``sdlc-tool session-ensure`` must
    never be requeued to pending or abandoned by startup recovery -- they have
    no subprocess to resume and are not "orphaned" in the sense this recovery
    path assumes for real bridge/dev sessions.
    """

    def _stale_ledger_session(self, **kwargs):
        import time

        from agent.agent_session_queue import AGENT_SESSION_HEALTH_MIN_RUNNING

        defaults = dict(
            session_id="sdlc-local-9042",
            agent_session_id="agent-ledger-001",
            worker_key="ai",
            started_at=time.time() - AGENT_SESSION_HEALTH_MIN_RUNNING - 600,
            message_text="test",
            status="running",
            is_ledger=True,
            session_type=SessionType.ENG,
        )
        defaults.update(kwargs)
        return _mock_agent_session(**defaults)

    def test_stale_ledger_session_stays_running_not_requeued_or_abandoned(self):
        """A stale is_ledger=True running session is skipped entirely: no
        update_session (requeue to pending) call, no finalize_session (abandon)
        call, and it does not count toward the recovered total."""
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        ledger_session = self._stale_ledger_session()

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [ledger_session]

            count = _recover_interrupted_agent_sessions_startup()

        assert count == 0
        mock_update.assert_not_called()
        mock_finalize.assert_not_called()
        # The session object itself must be untouched by save()/delete().
        ledger_session.save.assert_not_called()
        ledger_session.delete.assert_not_called()
        assert ledger_session.status == "running"

    def test_duplicate_ledger_sessions_both_skipped_inert(self):
        """Two AgentSession rows sharing the same session_id (a concurrent-
        creation duplicate) that both carry is_ledger=True are both skipped
        independently -- duplicates are an accepted, inert outcome (#2042 plan
        decision), not something the guard needs to dedup."""
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        dup_a = self._stale_ledger_session(agent_session_id="agent-ledger-dup-a")
        dup_b = self._stale_ledger_session(agent_session_id="agent-ledger-dup-b")

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = [dup_a, dup_b]

            count = _recover_interrupted_agent_sessions_startup()

        assert count == 0
        mock_update.assert_not_called()
        mock_finalize.assert_not_called()
        for dup in (dup_a, dup_b):
            dup.save.assert_not_called()
            dup.delete.assert_not_called()


class TestIsLedgerHelperLegacyRowSafety:
    """_is_ledger() (agent/session_health.py) treats missing/None/False as
    non-ledger, preserving executable behavior for every record predating the
    is_ledger field (#2042)."""

    def test_missing_attribute_is_not_ledger(self):
        """A bare object with no is_ledger attribute at all (legacy row shape,
        pre-#2042 Popoto hash) must NOT be treated as a ledger anchor."""
        from agent.session_health import _is_ledger

        class _LegacyRow:
            pass

        assert _is_ledger(_LegacyRow()) is False

    def test_none_is_not_ledger(self):
        from agent.session_health import _is_ledger

        row = SimpleNamespace(is_ledger=None)
        assert _is_ledger(row) is False

    def test_false_is_not_ledger(self):
        from agent.session_health import _is_ledger

        row = SimpleNamespace(is_ledger=False)
        assert _is_ledger(row) is False

    def test_string_false_is_not_ledger(self):
        """Popoto round-trips Field(default=False) through Redis as the string
        'False' -- the _truthy() coercion _is_ledger() delegates to must not
        treat that string as truthy."""
        from agent.session_health import _is_ledger

        row = SimpleNamespace(is_ledger="False")
        assert _is_ledger(row) is False

    def test_true_is_ledger(self):
        from agent.session_health import _is_ledger

        row = SimpleNamespace(is_ledger=True)
        assert _is_ledger(row) is True

    def test_string_true_is_ledger(self):
        from agent.session_health import _is_ledger

        row = SimpleNamespace(is_ledger="True")
        assert _is_ledger(row) is True


class TestSessionWatchdogSafe:
    """Session watchdog is safe — it only sets flags, never mutates session status directly."""

    def test_watchdog_unhealthy_flag_routes_to_deliver(self):
        """Watchdog sets unhealthy flag which routes to deliver, not nudge."""
        from agent.agent_session_queue import determine_delivery_action

        action = determine_delivery_action(
            msg="some output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=5,
            session_status="running",
            watchdog_unhealthy="stuck > 300s",
        )
        assert action == "deliver"


class TestBridgeWatchdogSafe:
    """Bridge watchdog is confirmed safe — it has no AgentSession imports."""

    def test_bridge_watchdog_has_no_agent_session_import(self):
        """Verify bridge_watchdog.py does not import AgentSession."""
        from pathlib import Path

        watchdog_path = Path(__file__).parent.parent.parent / "monitoring" / "bridge_watchdog.py"
        if not watchdog_path.exists():
            pytest.skip("bridge_watchdog.py not found")
        content = watchdog_path.read_text()
        assert "AgentSession" not in content, (
            "bridge_watchdog.py should not import AgentSession — "
            "it monitors the bridge process, not session state"
        )


class TestIntakePathTerminalGuard:
    """Intake path terminal guard (#730) prevents re-enqueue of terminal sessions.

    The guard fires in telegram_bridge.py after routing assigns session_id and
    before enqueue_agent_session() is called. When the existing session for that
    session_id is in a terminal status, the guard generates a fresh session_id
    from the current message.id, preventing completed->superseded cycling.
    """

    @pytest.mark.parametrize("terminal_status", sorted(TERMINAL_STATUSES))
    def test_guard_fires_for_each_terminal_status(self, terminal_status):
        """Guard generates a fresh session_id when existing session is terminal."""
        # Simulate the guard logic extracted from telegram_bridge.py
        session_id = "tg_myproj_98765_1000"
        project_key = "myproj"
        chat_id = 98765
        message_id = 2000
        is_reply_to_valor = False
        reply_to_msg_id = None

        existing_session = _mock_agent_session(session_id=session_id, status=terminal_status)

        def _run_intake_guard(session_id, existing_sessions):
            """Inline replica of the guard block from telegram_bridge.py."""
            if not (is_reply_to_valor and reply_to_msg_id):
                try:
                    if existing_sessions and existing_sessions[0].status in TERMINAL_STATUSES:
                        old_session_id = session_id
                        session_id_out = f"tg_{project_key}_{chat_id}_{message_id}"
                        return session_id_out, old_session_id
                except Exception:
                    pass
            return session_id, None

        new_session_id, old_session_id = _run_intake_guard(session_id, [existing_session])

        assert new_session_id != session_id, (
            f"Guard should generate a fresh session_id when existing session "
            f"has terminal status {terminal_status!r}"
        )
        assert new_session_id == f"tg_{project_key}_{chat_id}_{message_id}"
        assert old_session_id == session_id

    @pytest.mark.parametrize("non_terminal_status", ["pending", "running", "dormant", "active"])
    def test_guard_does_not_fire_for_non_terminal_sessions(self, non_terminal_status):
        """Guard leaves session_id unchanged when existing session is non-terminal."""
        session_id = "tg_myproj_98765_1000"
        project_key = "myproj"
        chat_id = 98765
        message_id = 2000
        is_reply_to_valor = False
        reply_to_msg_id = None

        existing_session = _mock_agent_session(session_id=session_id, status=non_terminal_status)

        def _run_intake_guard(session_id, existing_sessions):
            if not (is_reply_to_valor and reply_to_msg_id):
                try:
                    if existing_sessions and existing_sessions[0].status in TERMINAL_STATUSES:
                        return f"tg_{project_key}_{chat_id}_{message_id}", session_id
                except Exception:
                    pass
            return session_id, None

        new_session_id, old_session_id = _run_intake_guard(session_id, [existing_session])

        assert new_session_id == session_id, (
            f"Guard must NOT fire for non-terminal status {non_terminal_status!r}"
        )
        assert old_session_id is None

    def test_guard_does_not_fire_for_no_existing_session(self):
        """Guard leaves session_id unchanged when no existing session found."""
        session_id = "tg_myproj_98765_1000"
        project_key = "myproj"
        chat_id = 98765
        message_id = 2000
        is_reply_to_valor = False
        reply_to_msg_id = None

        def _run_intake_guard(session_id, existing_sessions):
            if not (is_reply_to_valor and reply_to_msg_id):
                try:
                    if existing_sessions and existing_sessions[0].status in TERMINAL_STATUSES:
                        return f"tg_{project_key}_{chat_id}_{message_id}", session_id
                except Exception:
                    pass
            return session_id, None

        new_session_id, old_session_id = _run_intake_guard(session_id, [])

        assert new_session_id == session_id
        assert old_session_id is None

    def test_guard_skipped_for_reply_to_messages(self):
        """Guard does not fire for reply-to messages (reply_to_msg_id set)."""
        session_id = "tg_myproj_98765_1000"
        project_key = "myproj"
        chat_id = 98765
        message_id = 2000
        is_reply_to_valor = True
        reply_to_msg_id = 999  # Non-None: this is a reply-to message

        # Even if the session is terminal, guard should not fire for reply-to
        existing_session = _mock_agent_session(session_id=session_id, status="completed")

        def _run_intake_guard(session_id, existing_sessions):
            if not (is_reply_to_valor and reply_to_msg_id):
                try:
                    if existing_sessions and existing_sessions[0].status in TERMINAL_STATUSES:
                        return f"tg_{project_key}_{chat_id}_{message_id}", session_id
                except Exception:
                    pass
            return session_id, None

        new_session_id, old_session_id = _run_intake_guard(session_id, [existing_session])

        assert new_session_id == session_id, (
            "Guard must be skipped for reply-to messages — reply-to resumption "
            "is a different path that intentionally resumes a prior session."
        )

    def test_guard_falls_back_gracefully_on_exception(self):
        """Guard swallows exceptions and continues without changing session_id."""
        session_id = "tg_myproj_98765_1000"
        is_reply_to_valor = False
        reply_to_msg_id = None

        def _run_intake_guard_with_exception(session_id):
            if not (is_reply_to_valor and reply_to_msg_id):
                try:
                    raise RuntimeError("Redis connection failed")
                except Exception:
                    pass  # Non-fatal fallback
            return session_id

        result = _run_intake_guard_with_exception(session_id)
        assert result == session_id, "Guard exception must not change session_id"

    def test_guard_present_in_telegram_bridge(self):
        """Structural test: intake terminal guard block exists in telegram_bridge.py."""
        from pathlib import Path

        bridge_path = Path(__file__).parent.parent.parent / "bridge" / "telegram_bridge.py"
        content = bridge_path.read_text()
        assert "Intake terminal guard" in content, (
            "telegram_bridge.py must contain the intake terminal guard block (#730). "
            "Search for 'Intake terminal guard' comment."
        )
        assert "TERMINAL_STATUSES" in content, (
            "telegram_bridge.py must import/reference TERMINAL_STATUSES for the intake guard."
        )


class TestMarkSupersededTerminalGuard:
    """_mark_superseded() defense-in-depth: reject_from_terminal=False removed (#730).

    With the kwarg removed, transition_status() uses its default (reject_from_terminal=True),
    so completed->superseded transitions are rejected by the terminal guard.

    _mark_superseded() itself was later deleted entirely (teammate-cold-start-finalize-gap,
    Defect A) and replaced by the module-level _delete_stale_terminal_duplicates() helper,
    which reconciles stale terminal duplicates via ORM ``instance.delete()`` rather than a
    transition_status() "supersede" call — see that helper's docstring for why. The structural
    test below now guards the successor helper for the same #730 invariant: the source must
    never reintroduce a completed->superseded transition_status() override.
    """

    def test_completed_to_superseded_is_now_rejected(self):
        """After removing reject_from_terminal=False, completed->superseded raises ValueError."""
        session = _mock_agent_session(status="completed")
        # With the default reject_from_terminal=True, this must raise
        with pytest.raises(ValueError, match="terminal status"):
            transition_status(
                session,
                "superseded",
                reason="superseded by new session",
                # reject_from_terminal=False intentionally NOT passed
            )
        # Session status must be unchanged
        assert session.status == "completed"

    def test_mark_superseded_removed_and_no_reintroduced_override(self):
        """Structural test: _mark_superseded() is gone, and no reject_from_terminal=False
        override has been reintroduced anywhere in agent_session_queue.py (its successor,
        _delete_stale_terminal_duplicates(), reconciles via delete rather than transition)."""
        from pathlib import Path

        queue_path = Path(__file__).parent.parent.parent / "agent" / "agent_session_queue.py"
        content = queue_path.read_text()

        assert "def _mark_superseded(" not in content, (
            "_mark_superseded() was deleted entirely by the teammate-cold-start-finalize-gap "
            "fix (replaced by _delete_stale_terminal_duplicates()) — it must not be "
            "reintroduced."
        )
        assert "def _delete_stale_terminal_duplicates(" in content, (
            "_delete_stale_terminal_duplicates() is the successor to _mark_superseded() and "
            "must exist as the terminal-duplicate reconciliation path."
        )
        assert "reject_from_terminal=False" not in content, (
            "No code path in agent_session_queue.py may pass reject_from_terminal=False to "
            "transition_status() — this override was removed by #730 as defense-in-depth and "
            "must not be reintroduced by any successor (e.g. "
            "_delete_stale_terminal_duplicates(), which deletes stale terminal duplicates "
            "instead of transitioning them)."
        )


class TestStartupRecoveryOwnershipGuard:
    """#2148: the recent-session guard keys on owning-worker liveness, not age."""

    def _recent_bridge_session(self, **kwargs):
        import time

        defaults = dict(
            session_id="0_recent",
            agent_session_id="agent-recent-001",
            worker_key="valor",
            session_type=SessionType.ENG,
            started_at=time.time() - 30,  # well inside the old 300s guard
            message_text="in-flight work",
        )
        defaults.update(kwargs)
        return _mock_agent_session(**defaults)

    def _run_recovery(self, sessions):
        import time

        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        with (
            patch("agent.session_health.AgentSession") as mock_as,
            patch("agent.session_health.time") as mock_time,
            patch("models.session_lifecycle.finalize_session"),
            patch("models.session_lifecycle.update_session") as mock_update,
        ):
            mock_time.time.return_value = time.time()
            mock_as.query.filter.return_value = sessions
            count = _recover_interrupted_agent_sessions_startup()
        return count, mock_update

    def test_recent_session_with_dead_owner_is_recovered(self):
        """The #2148 incident shape: started 30s before the crash, owner dead
        → recovered, NOT stranded."""
        session = self._recent_bridge_session(worker_pid=2**22 + 12345)  # dead pid
        with patch("agent.session_health._pid_is_alive", return_value=False):
            count, mock_update = self._run_recovery([session])
        assert count == 1
        mock_update.assert_called_once()
        assert mock_update.call_args[1]["new_status"] == "pending"

    def test_recent_session_with_live_owner_is_skipped(self):
        """Owned by a live concurrent worker → skip (the guard's real purpose)."""
        session = self._recent_bridge_session(worker_pid=99999)
        with patch("agent.session_health._pid_is_alive", return_value=True):
            count, mock_update = self._run_recovery([session])
        assert count == 0
        mock_update.assert_not_called()

    def test_recent_legacy_session_without_stamp_uses_age_fallback(self):
        """No worker_pid (legacy row) + recent → skipped, exactly as before."""
        session = self._recent_bridge_session(worker_pid=None)
        count, mock_update = self._run_recovery([session])
        assert count == 0
        mock_update.assert_not_called()

    def test_stale_legacy_session_without_stamp_still_recovered(self):
        import time

        from agent.agent_session_queue import AGENT_SESSION_HEALTH_MIN_RUNNING

        session = self._recent_bridge_session(
            worker_pid=None,
            started_at=time.time() - AGENT_SESSION_HEALTH_MIN_RUNNING - 600,
        )
        count, mock_update = self._run_recovery([session])
        assert count == 1
        mock_update.assert_called_once()

    def test_garbage_worker_pid_falls_back_to_age_guard(self):
        """Non-int worker_pid (corrupt row / Mock) → treated as absent."""
        session = self._recent_bridge_session(worker_pid="not-a-pid")
        count, mock_update = self._run_recovery([session])
        assert count == 0  # recent + fallback age guard → skipped
        mock_update.assert_not_called()

    def test_recovery_terminates_detached_live_harness(self):
        """A recovered session with a live claude_pid gets its detached
        harness SIGTERM'd before re-queue."""
        import signal as _signal

        session = self._recent_bridge_session(worker_pid=424242, claude_pid=55555)
        with (
            patch("agent.session_health._pid_is_alive", side_effect=lambda p: p == 55555),
            patch("agent.session_health.os.kill") as mock_kill,
        ):
            count, _ = self._run_recovery([session])
        assert count == 1
        mock_kill.assert_any_call(55555, _signal.SIGTERM)


class TestPickupStampsWorkerPid:
    """#2148: pending→running pickup stamps worker_pid = os.getpid()."""

    def test_pickup_source_stamps_worker_pid(self):
        """Both transition sites in session_pickup.py set worker_pid before
        transition_status (whose full save persists companion fields)."""
        from pathlib import Path

        src = (Path(__file__).parent.parent.parent / "agent" / "session_pickup.py").read_text()
        assert src.count("chosen.worker_pid = os.getpid()") == 2, (
            "Both pending->running pickup sites must stamp worker_pid (#2148)"
        )
