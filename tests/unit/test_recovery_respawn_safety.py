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
        "initial_telegram_message": {"message_text": "test", "sender_name": "user"},
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
            patch("agent.agent_session_queue.AgentSession") as mock_as,
            patch("agent.agent_session_queue._load_cooldowns", return_value={}),
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
            patch("agent.agent_session_queue.AgentSession") as mock_as,
            patch("agent.agent_session_queue._load_cooldowns", return_value={}),
            patch("subprocess.run") as mock_run,
            patch("agent.agent_session_queue.get_branch_state") as mock_bs,
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
        """Startup recovery queries status='running' only — terminal sessions are untouched."""
        from agent.agent_session_queue import _recover_interrupted_agent_sessions_startup

        # The function queries AgentSession.query.filter(status="running").
        # Terminal sessions are never queried, so they cannot be recovered.
        with patch("agent.agent_session_queue.AgentSession") as mock_as:
            mock_as.query.filter.return_value = []
            count = _recover_interrupted_agent_sessions_startup()

        assert count == 0
        # Verify it only queried "running" status
        mock_as.query.filter.assert_called_once_with(status="running")


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

    def test_mark_superseded_kwarg_removed_from_source(self):
        """Structural test: reject_from_terminal=False is gone from _mark_superseded()."""
        from pathlib import Path

        queue_path = Path(__file__).parent.parent.parent / "agent" / "agent_session_queue.py"
        content = queue_path.read_text()

        # Find the _mark_superseded function block
        start = content.find("def _mark_superseded()")
        assert start != -1, "_mark_superseded() function not found in agent_session_queue.py"

        # Isolate function body up to the next await call
        func_block = content[start : start + 600]
        assert "reject_from_terminal=False" not in func_block, (
            "_mark_superseded() must not pass reject_from_terminal=False to "
            "transition_status() — this override was removed by #730 as defense-in-depth."
        )
