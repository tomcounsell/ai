"""Tests for the never-started session recovery path (#1724).

Post-cutover (#1924): the PTY-liveness deferral (`_prime_pty_alive`) and the
mid-run PTY quiescence stage (`_eval_mid_run_pty_stage1`) are gone with the
substrate. What remains — and what these tests pin — is the age-based D0
never-started gate: a running session that has produced no SDK output past
``NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS`` is recovered,
with no deferral of any kind.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import agent.session_health as session_health


def _make_session(**kwargs):
    """Build a minimal mock AgentSession for testing."""
    session = MagicMock()
    session.agent_session_id = kwargs.get("agent_session_id", "test-session-id")
    session.project_key = kwargs.get("project_key", "test-project")
    session.status = kwargs.get("status", "running")
    session.last_tool_use_at = kwargs.get("last_tool_use_at", None)
    session.last_turn_at = kwargs.get("last_turn_at", None)
    # sdk_ever_output (agent.session_runner.liveness.derive_sdk_ever_output) now
    # OR's in last_stdout_at too — explicitly default it to None so MagicMock's
    # auto-attribute (truthy) doesn't silently mark every session as having
    # produced output.
    session.last_stdout_at = kwargs.get("last_stdout_at", None)
    session.last_heartbeat_at = kwargs.get("last_heartbeat_at", datetime.now(UTC))
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

    def test_returns_false_for_session_with_stdout_output(self):
        """Sessions that have ONLY last_stdout_at (toolless streaming turn, the
        headless-runner regression this plan fixes) must not be flagged —
        sdk_ever_output derives True via last_stdout_at alone."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            last_stdout_at=datetime.now(UTC) - timedelta(seconds=10),
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
        # 100s < 1230s (1200 + 30)
        assert _never_started_past_grace(session) is False

    def test_returns_true_past_grace_window(self):
        """Sessions past grace+margin window with no output must be flagged."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=1300),
        )
        # 1300s > 1230s (1200 + 30)
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
            started_at=datetime.now(UTC) - timedelta(seconds=1300),
            created_at=datetime.now(UTC) - timedelta(seconds=50),  # inside grace
        )
        # 1300s > threshold using started_at
        assert _never_started_past_grace(session) is True

    def test_returns_false_for_started_session_via_turn_count(self):
        """Issue #1962: turn_count>0 proves the session STARTED — never flag it.

        A bridge-originated remote session accrues turn_count without ever
        writing local log_path/claude_session_uuid or the ``*_at`` liveness
        fields, so ``sdk_ever_output`` stays False. Age past grace must NOT
        classify it as never-started.
        """
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=14400),  # 4h, far past grace
            turn_count=12,
            log_path=None,
            claude_session_uuid=None,
        )
        assert _never_started_past_grace(session) is False

    def test_returns_false_for_started_session_via_log_path(self):
        """Issue #1962: a non-empty log_path proves the session STARTED."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=14400),
            log_path="/tmp/logs/session.log",
        )
        assert _never_started_past_grace(session) is False

    def test_returns_false_for_started_session_via_claude_uuid(self):
        """Issue #1962: a claude_session_uuid proves the SDK authenticated."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=14400),
            claude_session_uuid="abc-123",
        )
        assert _never_started_past_grace(session) is False

    def test_still_flags_genuinely_never_started_session(self):
        """Issue #1962 guard: a session with NO own-progress evidence, past
        grace, and no SDK output IS still flagged (regression protection)."""
        from agent.session_health import _never_started_past_grace

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=1300),
            turn_count=0,
            log_path=None,
            claude_session_uuid=None,
        )
        assert _never_started_past_grace(session) is True


class TestFreshHeartbeatStartedSessionSurvives:
    """Issue #1962: a running session with a fresh heartbeat that has already
    STARTED (turn_count>0) must report progress, so the #944 orphan net leaves
    it alone — regardless of wall-clock age or missing local handle fields."""

    def test_fresh_heartbeat_started_session_has_progress(self):
        """The exact fixture from the failing integration test: 4h old,
        turn_count=12, fresh heartbeat, no log_path/uuid → _has_progress True."""
        from agent.session_health import _has_progress

        session = _make_session(
            started_at=datetime.now(UTC) - timedelta(hours=4),
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=30),
            turn_count=12,
            log_path=None,
            claude_session_uuid=None,
        )
        assert _has_progress(session) is True

    def test_stale_heartbeat_started_session_no_progress(self):
        """Counterpart: a started session whose heartbeat has gone stale past
        the no-output budget reports NO progress, so recovery still applies."""
        from agent.session_health import _has_progress

        session = _make_session(
            started_at=datetime.now(UTC) - timedelta(hours=4),
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=3600),  # >1800s budget
            turn_count=12,
            log_path=None,
            claude_session_uuid=None,
        )
        assert _has_progress(session) is False


class TestSubCheckBDeniedPastGrace:
    """Test that sub-check B is denied for never-started past grace (D0 gate)."""

    def test_fresh_heartbeat_denied_past_grace(self):
        """Fresh heartbeat must not return True for never-started-past-grace session."""
        from agent.session_health import _has_progress

        # Session with fresh heartbeat but no SDK output, past grace window
        session = _make_session(
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=5),
            created_at=datetime.now(UTC) - timedelta(seconds=1300),  # > 1230s threshold
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
            created_at=datetime.now(UTC) - timedelta(seconds=1300),
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
            created_at=datetime.now(UTC) - timedelta(seconds=1300),  # past grace
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

    def test_reprieve_not_suppressed_past_cap_with_fresh_stdout(self):
        """Second wedge route (critique BLOCKER): a toolless-streaming session
        past the reprieve cap but with fresh last_stdout_at must NOT be
        suppressed — sdk_ever_output derives True via last_stdout_at alone,
        so the reprieve-cap escalation guard does not fire."""
        from agent.session_health import MAX_NO_OUTPUT_REPRIEVES, _tier2_reprieve_signal

        session = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=50),  # within grace
            last_stdout_at=datetime.now(UTC) - timedelta(seconds=5),
            reprieve_count=MAX_NO_OUTPUT_REPRIEVES,
        )
        with patch("psutil.Process") as mock_proc:
            proc_instance = MagicMock()
            proc_instance.status.return_value = "running"
            proc_instance.children.return_value = []
            mock_proc.return_value = proc_instance

            handle = MagicMock()
            handle.pid = 12345

            result = _tier2_reprieve_signal(handle, session)
            assert result is not None  # NOT bypassed (has stdout output)


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

    def test_no_pty_liveness_symbols_resurface(self):
        """The PTY deferral machinery must stay deleted (#1924 one-way cutover).

        These names are checked AS STRINGS deliberately — if any of them
        reappears in session_health or the stall classifier, the teardown has
        been partially reverted.
        """
        import agent.session_health as sh
        import agent.session_stall_classifier as sc

        for gone in ("_prime_pty_alive", "_eval_mid_run_pty_stage1", "MID_RUN_QUIESCENCE_SECS"):
            assert not hasattr(sh, gone), f"session_health.{gone} resurfaced post-cutover"
        assert not hasattr(sc, "NEVER_STARTED_PTY_LIVENESS_SECS"), (
            "session_stall_classifier.NEVER_STARTED_PTY_LIVENESS_SECS resurfaced post-cutover"
        )


def _fake_d0_entry(
    *,
    sid: str = "d0-sess-1",
    project_key: str = "test-d0-loop",
    created_at_age_seconds: float = 1500,
):
    """Build a fake never-started-past-grace session row for the D0 loop
    (``_agent_session_tool_timeout_check``'s never-started branch, #1878 Part A).

    ``created_at_age_seconds`` defaults well past ``NEVER_STARTED_GRACE_SECS +
    NEVER_STARTED_CONFIRM_MARGIN_SECS`` (1230s) so ``_never_started_past_grace``
    fires.
    """
    now = datetime.now(tz=UTC)
    saves: list[list[str]] = []

    def _save(update_fields=None, **_kw):
        saves.append(list(update_fields) if update_fields else [])

    return SimpleNamespace(
        agent_session_id=sid,
        id=sid,
        session_id=f"sid-{sid}",
        status="running",
        project_key=project_key,
        current_tool_name=None,
        last_tool_use_at=None,
        last_turn_at=None,
        last_heartbeat_at=now,
        created_at=now - timedelta(seconds=created_at_age_seconds),
        started_at=None,
        worker_key="telegram-test-chat",
        recovery_attempts=0,
        reprieve_count=0,
        get_children=MagicMock(return_value=[]),
        save=_save,
        delete=lambda **_kw: None,
        _saves=saves,
    )


class TestD0KillLoopEndToEnd:
    """End-to-end proof that the D0 never-started branch inside
    ``_agent_session_tool_timeout_check`` recovers a session past grace and
    leaves a session within grace alone. Unlike the predicate tests above,
    these drive the real loop function and assert on
    ``_apply_recovery_transition`` invocation.
    """

    async def test_within_grace_session_not_killed(self):
        """A never-started session still inside the grace window must NOT be
        recovered by the D0 branch."""
        entry = _fake_d0_entry(created_at_age_seconds=50)  # < 150s threshold
        transition_mock = AsyncMock()

        with (
            patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
            patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
            patch.object(
                session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)
            ),
            patch.dict(
                "sys.modules", {"popoto.redis_db": SimpleNamespace(POPOTO_REDIS_DB=MagicMock())}
            ),
            patch.object(session_health, "_apply_recovery_transition", transition_mock),
        ):
            await session_health._agent_session_tool_timeout_check()

        transition_mock.assert_not_called()

    async def test_past_grace_session_killed(self):
        """A headless session past the never-started grace window must be
        recovered — there is no substrate-liveness deferral post-cutover."""
        entry = _fake_d0_entry(created_at_age_seconds=1500)
        transition_mock = AsyncMock()

        with (
            patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
            patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
            patch.object(
                session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)
            ),
            patch.dict(
                "sys.modules", {"popoto.redis_db": SimpleNamespace(POPOTO_REDIS_DB=MagicMock())}
            ),
            patch.object(session_health, "_apply_recovery_transition", transition_mock),
        ):
            await session_health._agent_session_tool_timeout_check()

        transition_mock.assert_called_once()
        assert transition_mock.call_args.kwargs.get("reason_kind") == "no_progress"


class TestTier2HangProbeWiring:
    """Wiring tests for the evidence-based subprocess-hang probe inside
    ``_tier2_reprieve_signal`` (#2069). The probe engine has its own unit tests
    in ``test_hang_probe.py``; these prove the reprieve gate actually acts on
    the verdict — the recovery decision, not just the classifier.
    """

    class _FakeProc:
        def __init__(self, cpu=2.0, children=None, conns=None, conns_raise=None):
            import psutil

            self._cpu = cpu
            self._children = children if children is not None else []
            self._conns = conns
            self._conns_raise = conns_raise
            self._psutil = psutil

        def status(self):
            return self._psutil.STATUS_RUNNING

        def cpu_times(self):
            return (self._cpu, 0.0, 0.0, 0.0)

        def children(self, recursive=False):
            return list(self._children)

        def net_connections(self, kind="inet"):
            if self._conns_raise is not None:
                raise self._conns_raise()
            return list(self._conns or [])

    @staticmethod
    def _clear():
        from agent.session_runner import liveness as lv

        lv._hang_samples.clear()

    def test_flat_cpu_confirmed_hang_recovers_never_started(self, monkeypatch):
        """A never-started session with a flat-CPU / no-children / no-API-socket
        subprocess is recovered (None) once the probe confirms a hang."""
        from agent.session_health import _tier2_reprieve_signal
        from agent.session_runner import liveness as lv

        self._clear()
        monkeypatch.setattr(lv, "HANG_CONFIRM_SAMPLES", 1)
        entry = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=200),  # within grace
            reprieve_count=0,
        )
        handle = MagicMock()
        handle.pid = 111
        proc = self._FakeProc(cpu=2.0, children=[], conns=[])  # readable, no :443
        with patch("psutil.Process", return_value=proc):
            first = _tier2_reprieve_signal(handle, entry)  # baseline
            second = _tier2_reprieve_signal(handle, entry)  # flat >= 1 → hung
        assert first is not None  # first poll reprieves (no premature hang)
        assert second is None  # confirmed hang → recover

    def test_first_flat_poll_reprieves_before_confirm(self, monkeypatch):
        """The detector must NOT declare a hang on the first inconclusive poll."""
        from agent.session_health import _tier2_reprieve_signal
        from agent.session_runner import liveness as lv

        self._clear()
        monkeypatch.setattr(lv, "HANG_CONFIRM_SAMPLES", 2)
        entry = _make_session(created_at=datetime.now(UTC) - timedelta(seconds=200))
        handle = MagicMock()
        handle.pid = 112
        proc = self._FakeProc(cpu=2.0, children=[], conns=[])
        with patch("psutil.Process", return_value=proc):
            assert _tier2_reprieve_signal(handle, entry) == "cpu_baseline"

    def test_progressing_reprieved_past_reprieve_cap(self):
        """Positive liveness evidence supersedes the #1226 count cap: a session
        past MAX_NO_OUTPUT_REPRIEVES with a live child is still reprieved."""
        from agent.session_health import MAX_NO_OUTPUT_REPRIEVES, _tier2_reprieve_signal

        self._clear()
        entry = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=200),  # within grace
            reprieve_count=MAX_NO_OUTPUT_REPRIEVES,
        )
        handle = MagicMock()
        handle.pid = 113
        proc = self._FakeProc(children=[MagicMock()])  # live child → progressing
        with patch("psutil.Process", return_value=proc):
            assert _tier2_reprieve_signal(handle, entry) == "children"

    def test_unknown_probe_still_recovers_no_output_past_cap(self):
        """When the probe is inconclusive (sockets unreadable while CPU flat),
        an un-probeable no-output session past the cap still recovers via the
        count-based fallback."""
        import psutil

        from agent.session_health import MAX_NO_OUTPUT_REPRIEVES, _tier2_reprieve_signal

        self._clear()
        entry = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=200),
            reprieve_count=MAX_NO_OUTPUT_REPRIEVES,
        )
        handle = MagicMock()
        handle.pid = 114
        proc = self._FakeProc(cpu=2.0, conns_raise=psutil.AccessDenied)
        with patch("psutil.Process", return_value=proc):
            _tier2_reprieve_signal(handle, entry)  # baseline
            assert _tier2_reprieve_signal(handle, entry) is None  # unknown → cap fallback

    def test_output_producing_hung_session_not_recovered(self, monkeypatch):
        """MEDIUM-2 regression (#2069 review): a session that HAS produced output
        must NOT be fast-recovered by the hang probe — it may be legitimately
        blocked on a non-443 endpoint. It falls through to the 'alive' reprieve."""
        from agent.session_health import _tier2_reprieve_signal
        from agent.session_runner import liveness as lv

        self._clear()
        monkeypatch.setattr(lv, "HANG_CONFIRM_SAMPLES", 1)
        entry = _make_session(
            created_at=datetime.now(UTC) - timedelta(seconds=200),
            last_turn_at=datetime.now(UTC) - timedelta(seconds=10),  # HAS output
        )
        handle = MagicMock()
        handle.pid = 115
        proc = self._FakeProc(cpu=2.0, children=[], conns=[])  # flat, no :443
        with patch("psutil.Process", return_value=proc):
            _tier2_reprieve_signal(handle, entry)  # baseline
            second = _tier2_reprieve_signal(handle, entry)  # would be "hung"
        assert second == "alive"  # reprieved, NOT recovered
