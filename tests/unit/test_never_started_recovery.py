"""Tests for the never-started and mid-run wedge session recovery paths (#1724)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


def _make_session(**kwargs):
    """Build a minimal mock AgentSession for testing."""
    session = MagicMock()
    session.agent_session_id = kwargs.get("agent_session_id", "test-session-id")
    session.project_key = kwargs.get("project_key", "test-project")
    session.status = kwargs.get("status", "running")
    session.last_tool_use_at = kwargs.get("last_tool_use_at", None)
    session.last_turn_at = kwargs.get("last_turn_at", None)
    session.last_heartbeat_at = kwargs.get("last_heartbeat_at", datetime.now(UTC))
    session.last_pty_read_loop_at = kwargs.get("last_pty_read_loop_at", None)
    session.last_pty_activity_at = kwargs.get("last_pty_activity_at", None)
    session.mid_run_quiescent_since = kwargs.get("mid_run_quiescent_since", None)
    session.mid_run_pty_snapshot = kwargs.get("mid_run_pty_snapshot", None)
    session.started_at = kwargs.get("started_at", None)
    session.created_at = kwargs.get("created_at", datetime.now(UTC) - timedelta(seconds=200))
    session.reprieve_count = kwargs.get("reprieve_count", 0)
    session.worker_key = kwargs.get("worker_key", "test-worker")
    session.current_tool_name = kwargs.get("current_tool_name", None)
    session.turn_count = kwargs.get("turn_count", 0)
    session.log_path = kwargs.get("log_path", None)
    session.claude_session_uuid = kwargs.get("claude_session_uuid", None)
    session.last_compaction_ts = kwargs.get("last_compaction_ts", None)
    session.get_children = MagicMock(return_value=[])
    return session


class TestNeverStartedPastGrace:
    """Tests for the _never_started_past_grace predicate."""

    def test_returns_false_for_session_with_tool_output(self):
        """Sessions that have last_tool_use_at must not be flagged."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            last_tool_use_at=datetime.now(UTC) - timedelta(seconds=10),
            created_at=datetime.now(UTC) - timedelta(seconds=500),
        )
        assert _never_started_past_grace(session) is False

    def test_returns_false_for_session_with_turn_output(self):
        """Sessions that have last_turn_at must not be flagged."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            last_turn_at=datetime.now(UTC) - timedelta(seconds=10),
            created_at=datetime.now(UTC) - timedelta(seconds=500),
        )
        assert _never_started_past_grace(session) is False

    def test_returns_false_inside_grace_window(self):
        """Sessions still within grace+margin window must not be flagged."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=100),
        )
        # 100s < 150s (120 + 30)
        assert _never_started_past_grace(session) is False

    def test_returns_true_past_grace_window(self):
        """Sessions past grace+margin window with no output must be flagged."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=200),
        )
        # 200s > 150s (120 + 30)
        assert _never_started_past_grace(session) is True

    def test_returns_false_for_missing_timestamps(self):
        """Sessions with no started_at or created_at must not be flagged (safe default)."""
        from agent.session_health import _never_started_past_grace

        session = _make_session()
        session.started_at = None
        session.created_at = None
        assert _never_started_past_grace(session) is False

    def test_uses_started_at_when_available(self):
        """When started_at is set, it should be used over created_at."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            started_at=datetime.now(UTC) - timedelta(seconds=200),
            created_at=datetime.now(UTC) - timedelta(seconds=50),  # inside grace
        )
        # 200s > threshold using started_at
        assert _never_started_past_grace(session) is True


class TestSubCheckBDeniedPastGrace:
    """Test that sub-check B is denied for never-started past grace (D0 gate)."""

    def test_fresh_heartbeat_denied_past_grace(self):
        """Fresh heartbeat must not return True for never-started-past-grace session."""
        from agent.session_health import _has_progress

        # Session with fresh heartbeat but no SDK output, past grace window
        session = _make_session(
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=5),
            created_at=datetime.now(UTC) - timedelta(seconds=200),  # > 150s threshold
        )
        # Should return False: past grace, no SDK output
        result = _has_progress(session)
        assert result is False

    def test_fresh_heartbeat_allowed_within_grace(self):
        """Fresh heartbeat should return True for never-started WITHIN grace window."""
        from agent.session_health import _has_progress

        # Session with fresh heartbeat, still within grace window (50s < 150s)
        session = _make_session(
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=5),
            created_at=datetime.now(UTC) - timedelta(seconds=50),
        )
        result = _has_progress(session)
        assert result is True

    def test_sdk_output_session_not_affected_by_d0_gate(self):
        """Sessions with recent SDK output must pass _has_progress regardless of age."""
        from agent.session_health import _has_progress

        session = _make_session(
            last_tool_use_at=datetime.now(UTC) - timedelta(seconds=60),
            created_at=datetime.now(UTC) - timedelta(seconds=300),
        )
        # Sub-check A should pass (last_tool_use_at is within SDK_PROGRESS_FRESHNESS_WINDOW)
        result = _has_progress(session)
        assert result is True


class TestReprieveBypassed:
    """Tests for _tier2_reprieve_signal bypass when never-started past grace."""

    def test_reprieve_bypassed_past_grace(self):
        """Reprieve must be suppressed for never-started-past-grace session."""
        from agent.session_health import _tier2_reprieve_signal

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=200),
            reprieve_count=0,
        )
        result = _tier2_reprieve_signal(None, session)
        assert result is None  # Bypassed due to past-grace

    def test_reprieve_not_bypassed_for_output_session(self):
        """Sessions with SDK output must not be affected by the past-grace bypass.

        When sdk_ever_output=True, the bypass does not apply regardless of age.
        The session falls through to psutil checks.
        """
        from agent.session_health import _tier2_reprieve_signal

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=200),  # past grace
            last_turn_at=datetime.now(UTC) - timedelta(seconds=10),  # has output
            reprieve_count=0,
        )
        # Mock a live process
        with patch("psutil.Process") as mock_proc:
            proc_instance = MagicMock()
            proc_instance.status.return_value = "running"
            proc_instance.children.return_value = []
            mock_proc.return_value = proc_instance

            handle = MagicMock()
            handle.pid = 12345

            result = _tier2_reprieve_signal(handle, session)
            assert result is not None  # NOT bypassed (has output)

    def test_reprieve_bypassed_past_reprieve_cap(self):
        """Sessions exceeding MAX_NO_OUTPUT_REPRIEVES must also be suppressed."""
        from agent.session_health import MAX_NO_OUTPUT_REPRIEVES, _tier2_reprieve_signal

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=50),  # within grace
            reprieve_count=MAX_NO_OUTPUT_REPRIEVES,
        )
        result = _tier2_reprieve_signal(None, session)
        assert result is None  # Bypassed due to reprieve cap


class TestNeverStartedGraceConstantAlignment:
    """Drift-pin test: verify grace constants are aligned across modules."""

    def test_grace_constant_alignment(self):
        """The effective grace in session_health must equal classifier's constants."""
        from agent.session_health import _never_started_past_grace
        from agent.session_stall_classifier import (
            NEVER_STARTED_CONFIRM_MARGIN_SECS,
            NEVER_STARTED_GRACE_SECS,
        )

        threshold = NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS

        # Session slightly UNDER threshold should NOT fire.
        # Use threshold - 2s to give a 2-second safety margin against test timing jitter.
        session_under = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=threshold - 2),
        )
        assert _never_started_past_grace(session_under) is False

        # Session 5 seconds PAST threshold should fire
        session_past = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=threshold + 5),
        )
        assert _never_started_past_grace(session_past) is True

    def test_session_health_imports_from_classifier(self):
        """session_health must import grace constants from session_stall_classifier."""
        import agent.session_health as sh
        import agent.session_stall_classifier as sc

        assert sh.NEVER_STARTED_GRACE_SECS == sc.NEVER_STARTED_GRACE_SECS
        assert sh.NEVER_STARTED_CONFIRM_MARGIN_SECS == sc.NEVER_STARTED_CONFIRM_MARGIN_SECS


class TestMidRunStage1EvalFunction:
    """Tests for the _eval_mid_run_pty_stage1 function."""

    def test_abstains_on_missing_loop_marker(self):
        """Stage-1 must abstain when last_pty_read_loop_at is None."""
        from agent.session_health import _eval_mid_run_pty_stage1

        now = datetime.now(UTC)
        session = _make_session(
            last_turn_at=now - timedelta(seconds=10),
            current_tool_name="Bash",
        )
        session.last_pty_read_loop_at = None

        _eval_mid_run_pty_stage1(session, now)
        session.save.assert_not_called()

    def test_abstains_on_stale_loop_marker(self):
        """Stage-1 must abstain when last_pty_read_loop_at is stale vs heartbeat."""
        from agent.session_health import HEARTBEAT_FRESHNESS_WINDOW, _eval_mid_run_pty_stage1

        now = datetime.now(UTC)
        session = _make_session(
            last_turn_at=now - timedelta(seconds=10),
            current_tool_name="Bash",
            last_pty_read_loop_at=now - timedelta(seconds=HEARTBEAT_FRESHNESS_WINDOW + 30),
            last_heartbeat_at=now,
        )
        session.mid_run_quiescent_since = None

        _eval_mid_run_pty_stage1(session, now)
        # When quiescent_since is None there should be no save on abstain
        session.save.assert_not_called()

    def test_not_suspect_clears_quiescent_since_when_activity_fresh(self):
        """Stage-1 must clear mid_run_quiescent_since when screen recently painted."""
        from agent.session_health import _eval_mid_run_pty_stage1

        now = datetime.now(UTC)
        session = _make_session(
            last_turn_at=now - timedelta(seconds=10),
            current_tool_name="Bash",
            last_pty_read_loop_at=now - timedelta(seconds=5),
            last_heartbeat_at=now - timedelta(seconds=5),
            last_pty_activity_at=now - timedelta(seconds=10),  # fresh
        )
        session.mid_run_quiescent_since = now - timedelta(seconds=200)  # was set

        _eval_mid_run_pty_stage1(session, now)
        # Activity is fresh: should clear mid_run_quiescent_since
        assert session.mid_run_quiescent_since is None

    def test_abstains_when_no_sdk_output(self):
        """Stage-1 must abstain when sdk_ever_output is False."""
        from agent.session_health import _eval_mid_run_pty_stage1

        now = datetime.now(UTC)
        session = _make_session(
            current_tool_name="Bash",
            last_pty_read_loop_at=now - timedelta(seconds=5),
        )
        session.last_turn_at = None
        session.last_tool_use_at = None

        _eval_mid_run_pty_stage1(session, now)
        session.save.assert_not_called()

    def test_abstains_when_no_tool_in_flight(self):
        """Stage-1 must abstain when current_tool_name is None."""
        from agent.session_health import _eval_mid_run_pty_stage1

        now = datetime.now(UTC)
        session = _make_session(
            last_turn_at=now - timedelta(seconds=10),
            last_pty_read_loop_at=now - timedelta(seconds=5),
        )
        session.current_tool_name = None

        _eval_mid_run_pty_stage1(session, now)
        session.save.assert_not_called()

    def test_quiescent_since_set_on_first_stale_tick(self):
        """Stage-1 must set mid_run_quiescent_since on the first quiescent tick.

        The function uses total_input_tokens as a byte_offset proxy when building the
        snapshot string. We set it to a fixed value so the snapshot matches the prior
        tick exactly and the quiescent branch fires.
        """
        from agent.session_health import HEARTBEAT_FRESHNESS_WINDOW, _eval_mid_run_pty_stage1

        now = datetime.now(UTC)
        stale_activity = now - timedelta(seconds=HEARTBEAT_FRESHNESS_WINDOW + 30)
        # Match the exact snapshot format used inside the function:
        # current_snapshot = f"({act_iso},{byte_offset})"
        # byte_offset = getattr(entry, "total_input_tokens", None)
        fixed_tokens = 12345
        snapshot = f"({stale_activity.isoformat()},{fixed_tokens})"
        session = _make_session(
            last_turn_at=now - timedelta(seconds=10),
            current_tool_name="Bash",
            last_pty_read_loop_at=now - timedelta(seconds=5),
            last_heartbeat_at=now - timedelta(seconds=5),
            last_pty_activity_at=stale_activity,
            mid_run_quiescent_since=None,
            mid_run_pty_snapshot=snapshot,
        )
        session.total_input_tokens = fixed_tokens  # must match snapshot proxy

        _eval_mid_run_pty_stage1(session, now)
        # mid_run_quiescent_since should be set to now
        assert session.mid_run_quiescent_since == now


class TestNeverStartedConstantsPresent:
    """Verify that session_health exports the constants it needs."""

    def test_constants_accessible_from_session_health(self):
        """Confirm session_health exposes classifier constants at module level."""
        import agent.session_health as sh

        assert hasattr(sh, "NEVER_STARTED_GRACE_SECS")
        assert hasattr(sh, "NEVER_STARTED_CONFIRM_MARGIN_SECS")
        assert isinstance(sh.NEVER_STARTED_GRACE_SECS, int)
        assert isinstance(sh.NEVER_STARTED_CONFIRM_MARGIN_SECS, int)
        assert sh.NEVER_STARTED_GRACE_SECS > 0
        assert sh.NEVER_STARTED_CONFIRM_MARGIN_SECS >= 0

    def test_mid_run_quiescence_secs_present_and_non_negative(self):
        """MID_RUN_QUIESCENCE_SECS must be present and non-negative."""
        import agent.session_health as sh

        assert hasattr(sh, "MID_RUN_QUIESCENCE_SECS")
        assert sh.MID_RUN_QUIESCENCE_SECS >= 0


class TestPrimePtyAlive:
    """Tests for _prime_pty_alive (issue #1792 — D0 never-started PTY-liveness gate).

    POLARITY: _prime_pty_alive returns True=alive/defer, False=kill-eligible.
    This is the INVERSE of _pty_quiescent_long_enough (True=kill-eligible).
    """

    def test_alive_case_fresh_loop_and_fresh_activity(self):
        """Fresh last_pty_read_loop_at + fresh last_pty_activity_at → True (alive, defer)."""
        from agent.session_health import _prime_pty_alive

        now = datetime.now(UTC)
        session = _make_session(
            last_pty_read_loop_at=now - timedelta(seconds=5),
            last_pty_activity_at=now - timedelta(seconds=10),
        )
        assert _prime_pty_alive(session, now) is True

    def test_non_pty_escape_no_read_loop(self):
        """last_pty_read_loop_at=None → False (SDK session, no PTY deferral)."""
        from agent.session_health import _prime_pty_alive

        now = datetime.now(UTC)
        session = _make_session(
            last_pty_read_loop_at=None,
            last_pty_activity_at=now - timedelta(seconds=5),
        )
        assert _prime_pty_alive(session, now) is False

    def test_stale_read_loop_returns_false(self):
        """last_pty_read_loop_at older than HEARTBEAT_FRESHNESS_WINDOW → False (dead loop)."""
        from agent.session_health import HEARTBEAT_FRESHNESS_WINDOW, _prime_pty_alive

        now = datetime.now(UTC)
        session = _make_session(
            last_pty_read_loop_at=now - timedelta(seconds=HEARTBEAT_FRESHNESS_WINDOW + 5),
            last_pty_activity_at=now - timedelta(seconds=10),
        )
        assert _prime_pty_alive(session, now) is False

    def test_none_activity_returns_false(self):
        """last_pty_activity_at=None → False (no screen activity, can't prove alive)."""
        from agent.session_health import _prime_pty_alive

        now = datetime.now(UTC)
        session = _make_session(
            last_pty_read_loop_at=now - timedelta(seconds=5),
            last_pty_activity_at=None,
        )
        assert _prime_pty_alive(session, now) is False

    def test_kill_switch_zero_disables_deferral(self):
        """NEVER_STARTED_PTY_LIVENESS_SECS=0 → False for all sessions (kill-switch)."""
        import agent.session_stall_classifier as sc
        from agent.session_health import _prime_pty_alive

        now = datetime.now(UTC)
        session = _make_session(
            last_pty_read_loop_at=now - timedelta(seconds=5),
            last_pty_activity_at=now - timedelta(seconds=5),
        )
        original = sc.NEVER_STARTED_PTY_LIVENESS_SECS
        try:
            sc.NEVER_STARTED_PTY_LIVENESS_SECS = 0
            result = _prime_pty_alive(session, now)
        finally:
            sc.NEVER_STARTED_PTY_LIVENESS_SECS = original
        assert result is False

    def test_kill_switch_negative_disables_deferral(self):
        """NEVER_STARTED_PTY_LIVENESS_SECS=-1 → False (kill-switch with negative value)."""
        import agent.session_stall_classifier as sc
        from agent.session_health import _prime_pty_alive

        now = datetime.now(UTC)
        session = _make_session(
            last_pty_read_loop_at=now - timedelta(seconds=5),
            last_pty_activity_at=now - timedelta(seconds=5),
        )
        original = sc.NEVER_STARTED_PTY_LIVENESS_SECS
        try:
            sc.NEVER_STARTED_PTY_LIVENESS_SECS = -1
            result = _prime_pty_alive(session, now)
        finally:
            sc.NEVER_STARTED_PTY_LIVENESS_SECS = original
        assert result is False

    def test_malformed_activity_string_returns_false_no_exception(self):
        """String instead of datetime for last_pty_activity_at → False, no exception raised."""
        from agent.session_health import _prime_pty_alive

        now = datetime.now(UTC)
        session = _make_session(
            last_pty_read_loop_at=now - timedelta(seconds=5),
        )
        session.last_pty_activity_at = "not-a-datetime"
        result = _prime_pty_alive(session, now)
        assert result is False

    def test_malformed_activity_int_returns_false_no_exception(self):
        """Int instead of datetime for last_pty_activity_at → False, no exception raised."""
        from agent.session_health import _prime_pty_alive

        now = datetime.now(UTC)
        session = _make_session(
            last_pty_read_loop_at=now - timedelta(seconds=5),
        )
        session.last_pty_activity_at = 12345
        result = _prime_pty_alive(session, now)
        assert result is False

    def test_polarity_guard_fresh_activity_defers_stale_activity_kills(self):
        """POLARITY GUARD: _prime_pty_alive is the INVERSE of _pty_quiescent_long_enough.

        Given identical priming granite rows:
        - Fresh last_pty_activity_at → _prime_pty_alive returns True (alive, defer).
        - Stale last_pty_activity_at → _prime_pty_alive returns False (dead, recover).

        If a builder copies _pty_quiescent_long_enough's return values verbatim,
        BOTH assertions here fail loudly.
        """
        from agent.session_health import _prime_pty_alive
        from agent.session_stall_classifier import NEVER_STARTED_PTY_LIVENESS_SECS

        now = datetime.now(UTC)
        fresh_loop = now - timedelta(seconds=5)

        # Fresh activity (within NEVER_STARTED_PTY_LIVENESS_SECS) → must defer (True).
        session_fresh = _make_session(
            last_pty_read_loop_at=fresh_loop,
            last_pty_activity_at=now - timedelta(seconds=NEVER_STARTED_PTY_LIVENESS_SECS - 10),
        )
        assert _prime_pty_alive(session_fresh, now) is True, (
            "Fresh PTY activity must return True (alive — defer the kill). "
            "If this is False, the polarity was copied verbatim from _pty_quiescent_long_enough."
        )

        # Stale activity (beyond NEVER_STARTED_PTY_LIVENESS_SECS) → must kill (False).
        session_stale = _make_session(
            last_pty_read_loop_at=fresh_loop,
            last_pty_activity_at=now - timedelta(seconds=NEVER_STARTED_PTY_LIVENESS_SECS + 10),
        )
        assert _prime_pty_alive(session_stale, now) is False, (
            "Stale PTY activity must return False (dead — proceed with kill). "
            "If this is True, the polarity was copied verbatim from _pty_quiescent_long_enough."
        )


class TestPrimePtyAliveRecoveryPath:
    """Integration tests for the D0 branch gate: fresh PTY defers, stale PTY recovers."""

    def test_fresh_pty_defers_d0_kill(self):
        """D0 branch: fresh PTY activity must NOT call _apply_recovery_transition."""

        import agent.session_health as session_health
        from agent.session_stall_classifier import (
            NEVER_STARTED_CONFIRM_MARGIN_SECS,
            NEVER_STARTED_GRACE_SECS,
        )

        now = datetime.now(UTC)
        past_grace = now - timedelta(
            seconds=NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS + 10
        )

        # Priming session: no SDK output, past grace, but fresh PTY.
        session = _make_session(
            created_at=past_grace,
            last_tool_use_at=None,
            last_turn_at=None,
            last_pty_read_loop_at=now - timedelta(seconds=5),
            last_pty_activity_at=now - timedelta(seconds=10),
        )
        session.last_tool_use_at = None
        session.last_turn_at = None

        # _prime_pty_alive with this session must return True (alive).
        result = session_health._prime_pty_alive(session, now)
        assert result is True, "Fixture must model a live priming session"

    def test_stale_pty_proceeds_to_recover(self):
        """D0 branch: a priming session with stale PTY activity is kill-eligible."""
        import agent.session_health as session_health
        from agent.session_stall_classifier import (
            NEVER_STARTED_CONFIRM_MARGIN_SECS,
            NEVER_STARTED_GRACE_SECS,
            NEVER_STARTED_PTY_LIVENESS_SECS,
        )

        now = datetime.now(UTC)
        past_grace = now - timedelta(
            seconds=NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS + 10
        )

        # Priming session: no SDK output, past grace, stale PTY.
        session = _make_session(
            created_at=past_grace,
            last_pty_read_loop_at=now - timedelta(seconds=5),
            last_pty_activity_at=now - timedelta(seconds=NEVER_STARTED_PTY_LIVENESS_SECS + 20),
        )
        session.last_tool_use_at = None
        session.last_turn_at = None

        result = session_health._prime_pty_alive(session, now)
        assert result is False, "Stale PTY session must be kill-eligible"
