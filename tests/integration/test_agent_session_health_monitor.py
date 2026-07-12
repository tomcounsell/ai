"""Tests for session health monitor: detect and recover stuck running sessions.

Tests cover:
- started_at field on AgentSession (set when session transitions to running)
- _agent_session_health_check() detecting and recovering dead workers
- CLI functions: format_duration, show_status, flush_stuck, flush_session

The previous wall-clock per-session timeout (``_get_agent_session_timeout`` +
``AGENT_SESSION_TIMEOUT_DEFAULT``/``BUILD``) was retired by issue #1172 — the
detector no longer kills on inferred staleness. Cost monitoring is the
long-run backstop for genuinely runaway sessions.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from agent.agent_session_queue import (
    _active_workers,
    _pop_agent_session,
    _pop_agent_session_with_fallback,
)
from models.agent_session import AgentSession


def _create_test_session(**overrides) -> AgentSession:
    """Create an AgentSession with sensible defaults for testing."""
    defaults = {
        "project_key": "test",
        "status": "pending",
        "priority": "high",
        "created_at": time.time(),
        "session_id": "test_session",
        "working_dir": "/tmp/test",
        "message_text": "test message",
        "sender_name": "Test",
        "chat_id": "123",
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


class TestStartedAtField:
    """Tests for the started_at field on AgentSession."""

    def test_started_at_field_exists(self):
        """AgentSession should have a started_at field."""
        assert hasattr(AgentSession, "started_at"), "AgentSession missing started_at field"

    def test_started_at_defaults_to_none(self):
        """A newly created AgentSession should have started_at=None."""
        session = _create_test_session()
        assert session.started_at is None

    @pytest.mark.asyncio
    async def test_pop_agent_session_sets_started_at(self):
        """When _pop_agent_session transitions a session to running, started_at should be set."""
        _create_test_session()
        before = datetime.now(tz=UTC)
        session = await _pop_agent_session_with_fallback("123")
        after = datetime.now(tz=UTC)

        assert session is not None
        # Verify the AgentSession in Redis has started_at set
        running_jobs = AgentSession.query.filter(project_key="test", status="running")
        assert len(running_jobs) == 1
        assert running_jobs[0].started_at is not None
        started = running_jobs[0].started_at
        if isinstance(started, (int, float)):
            started = datetime.fromtimestamp(started, tz=UTC)
        elif isinstance(started, datetime) and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        assert before <= started <= after

    def test_started_at_in_extract_fields(self):
        """started_at should be included in _extract_agent_session_fields."""
        from agent.agent_session_queue import _extract_agent_session_fields

        session = _create_test_session(started_at=datetime.now(tz=UTC))
        fields = _extract_agent_session_fields(session)
        assert "started_at" in fields
        assert fields["started_at"] is not None


class TestPopAgentSessionLedgerGuard:
    """_pop_agent_session's candidate loop skips is_ledger=True anchor rows (#2042).

    Ledger anchors are CLI-created ``sdlc-local-*`` rows with no subprocess to
    execute -- they must never be popped off the pending queue. Both the
    primary async candidate loop (``_pop_agent_session``) and the sync
    fallback candidate loop (inside ``_pop_agent_session_with_fallback``)
    guard on ``is_ledger``. Most tests here call ``_pop_agent_session``
    directly to exercise the async guard in isolation; a dedicated test below
    drives ``_pop_agent_session_with_fallback`` against an all-ledger queue to
    prove the sync fallback path is guarded too.
    """

    @pytest.fixture(autouse=True)
    def _cleanup_workers(self):
        """Clean up _active_workers after each test to prevent cross-test pollution."""
        yield
        for key in list(_active_workers.keys()):
            task = _active_workers.pop(key, None)
            if task and not task.done():
                try:
                    task.cancel()
                except RuntimeError:
                    pass

    @pytest.mark.asyncio
    async def test_ledger_candidate_skipped_next_eligible_popped(self):
        """A higher-priority ledger anchor is skipped; pop returns the next
        eligible (lower-priority) real candidate instead."""
        _create_test_session(
            status="pending",
            session_id="sdlc-local-9300",
            chat_id="ledger-pickup-chat",
            priority="urgent",
            is_ledger=True,
        )
        _create_test_session(
            status="pending",
            session_id="real_session_9300",
            chat_id="ledger-pickup-chat",
            priority="normal",
        )

        popped = await _pop_agent_session("ledger-pickup-chat")

        assert popped is not None
        assert popped.session_id == "real_session_9300"

        ledger_reloaded = AgentSession.query.filter(session_id="sdlc-local-9300")
        assert len(ledger_reloaded) == 1
        assert ledger_reloaded[0].status == "pending", "ledger row must remain pending, unpicked"

    @pytest.mark.asyncio
    async def test_only_ledger_candidate_returns_none(self):
        """If the only pending candidate is a ledger anchor, pop returns None
        rather than picking it up."""
        _create_test_session(
            status="pending",
            session_id="sdlc-local-9301",
            chat_id="ledger-only-chat",
            is_ledger=True,
        )

        popped = await _pop_agent_session("ledger-only-chat")

        assert popped is None

        ledger_reloaded = AgentSession.query.filter(session_id="sdlc-local-9301")
        assert len(ledger_reloaded) == 1
        assert ledger_reloaded[0].status == "pending"

    @pytest.mark.asyncio
    async def test_sync_fallback_skips_ledger_candidate(self):
        """_pop_agent_session_with_fallback must also skip is_ledger candidates.

        The wrapper tries the async path first; on an all-ledger queue that
        returns None, and the wrapper falls through to the sync fallback
        loop. This drives that fallback loop directly (not
        ``_pop_agent_session``) to prove its own ``is_ledger`` guard --
        without it, the sync path would re-query the same pending anchor and
        pop it, spawning a duplicate ``claude -p`` driver (#2042)."""
        _create_test_session(
            status="pending",
            session_id="sdlc-local-9303",
            chat_id="ledger-fallback-chat",
            is_ledger=True,
        )

        popped = await _pop_agent_session_with_fallback("ledger-fallback-chat")

        assert popped is None

        ledger_reloaded = AgentSession.query.filter(session_id="sdlc-local-9303")
        assert len(ledger_reloaded) == 1
        assert ledger_reloaded[0].status == "pending", "ledger row must remain pending, unpicked"

    @pytest.mark.asyncio
    async def test_duplicate_ledger_rows_both_skipped_inert(self):
        """Two real AgentSession rows sharing the same session_id (a
        concurrent-creation duplicate -- e.g. two racing ``sdlc-tool
        session-ensure`` invocations) that both carry is_ledger=True are both
        independently skipped. Duplicates are an accepted, inert outcome per
        the #2042 plan decision -- this test does NOT assert "exactly one
        row exists"; it asserts neither duplicate is ever popped."""
        from agent.session_pickup import _truthy

        dup_session_id = "sdlc-local-9302"
        _create_test_session(
            status="pending",
            session_id=dup_session_id,
            chat_id="ledger-dup-chat",
            is_ledger=True,
        )
        _create_test_session(
            status="pending",
            session_id=dup_session_id,
            chat_id="ledger-dup-chat",
            is_ledger=True,
        )

        dup_rows = AgentSession.query.filter(session_id=dup_session_id)
        assert len(dup_rows) == 2, "setup sanity: two distinct rows share session_id"

        popped = await _pop_agent_session("ledger-dup-chat")

        assert popped is None, "neither ledger duplicate may ever be popped"

        dup_rows_after = AgentSession.query.filter(session_id=dup_session_id)
        assert len(dup_rows_after) == 2
        for row in dup_rows_after:
            assert row.status == "pending"
            assert _truthy(row.is_ledger)


class TestJobHealthCheck:
    """Tests for _agent_session_health_check().

    Note: _agent_session_health_check uses `session.chat_id or project_key` as the worker key.
    Default chat_id in _create_test_session is "123", so workers must be keyed by "123".
    Recovery calls _ensure_worker which spawns real asyncio tasks, so cleanup must
    cancel all workers after each test.
    """

    # The default chat_id used by _create_test_session
    WORKER_KEY = "123"

    @pytest.fixture(autouse=True)
    def _cleanup_workers(self):
        """Clean up _active_workers after each test to prevent cross-test pollution."""
        yield
        # Cancel any worker tasks spawned by _ensure_worker during recovery.
        # The event loop may already be closed by the time teardown runs,
        # so silently ignore RuntimeError from cancel().
        for key in list(_active_workers.keys()):
            task = _active_workers.pop(key, None)
            if task and not task.done():
                try:
                    task.cancel()
                except RuntimeError:
                    pass

    @pytest.mark.asyncio
    async def test_recovers_job_with_dead_worker(self):
        """A running session whose worker task is done should be recovered."""
        from agent.agent_session_queue import _agent_session_health_check

        # Create a running session that has been running long enough
        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC) - timedelta(seconds=600),  # 10 minutes ago
            session_id="dead_worker_session",
        )

        # Set up a dead worker (asyncio Task that's already done)
        # Workers are keyed by chat_id (default "123"), not project_key
        done_task = asyncio.Future()
        done_task.set_result(None)
        _active_workers[self.WORKER_KEY] = done_task

        await _agent_session_health_check()

        # The running session should be gone, replaced by a pending one
        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 0

        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 1
        assert pending[0].session_id == "dead_worker_session"

    @pytest.mark.asyncio
    async def test_recovers_job_with_no_worker(self):
        """A running session with no entry in _active_workers should be recovered."""
        from agent.agent_session_queue import _agent_session_health_check

        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC) - timedelta(seconds=600),  # 10 minutes ago
            session_id="orphan_session",
        )

        # Ensure no worker exists for this chat_id
        _active_workers.pop(self.WORKER_KEY, None)

        await _agent_session_health_check()

        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 0

        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 1
        assert pending[0].session_id == "orphan_session"

    @pytest.mark.asyncio
    async def test_skips_job_with_alive_worker_under_timeout(self):
        """A running session with an alive worker under timeout should NOT be recovered."""
        from agent.agent_session_queue import _agent_session_health_check

        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC)
            - timedelta(seconds=60),  # 1 minute ago (under 5min guard)
            session_id="alive_session",
        )

        # Set up a live worker keyed by chat_id
        live_task = asyncio.Future()
        _active_workers[self.WORKER_KEY] = live_task

        await _agent_session_health_check()

        # The session should still be running
        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 1
        assert running[0].session_id == "alive_session"

        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_long_running_session_with_fresh_heartbeat_survives(self):
        """Issue #1172: a session with a fresh heartbeat is NOT killed regardless
        of wall-clock duration. The previous wall-clock cap is gone."""
        from agent.agent_session_queue import _agent_session_health_check

        # Simulate a 4-hour-old session — far beyond any prior wall-clock cap.
        s = _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC) - timedelta(hours=4),
            last_heartbeat_at=datetime.now(tz=UTC) - timedelta(seconds=30),
            turn_count=12,
            session_id="long_running_session",
        )

        # Worker is alive — fresh heartbeat is the dispositive evidence.
        # Worker is keyed by the session's worker_key (project_key for a
        # default test session, since session_type is unset).
        live_task = asyncio.Future()
        _active_workers[s.worker_key] = live_task

        try:
            await _agent_session_health_check()

            # Must remain running — no timeout-based recovery any more.
            running = AgentSession.query.filter(project_key="test", status="running")
            assert len(running) == 1
            assert running[0].session_id == "long_running_session"
        finally:
            _active_workers.pop(s.worker_key, None)

    @pytest.mark.asyncio
    async def test_skips_recently_started_job_with_dead_worker(self):
        """Sessions running < AGENT_SESSION_HEALTH_MIN_RUNNING not recovered (race guard)."""
        from agent.agent_session_queue import _agent_session_health_check

        _create_test_session(
            status="running",
            started_at=datetime.now(tz=UTC)
            - timedelta(seconds=60),  # Only 1 minute ago (under 5min guard)
            session_id="recent_session",
        )

        # Worker is dead, keyed by chat_id
        done_task = asyncio.Future()
        done_task.set_result(None)
        _active_workers[self.WORKER_KEY] = done_task

        await _agent_session_health_check()

        # Should NOT be recovered due to race condition guard
        running = AgentSession.query.filter(project_key="test", status="running")
        assert len(running) == 1
        assert running[0].session_id == "recent_session"

    @pytest.mark.asyncio
    async def test_handles_job_without_started_at(self):
        """Jobs without started_at (legacy) should still be checked for dead workers."""
        from agent.agent_session_queue import _agent_session_health_check

        # A running session with no started_at (legacy session that predates this field)
        _create_test_session(
            status="running",
            session_id="legacy_session",
        )
        # started_at defaults to None

        # Worker is dead — ensure no worker at chat_id key
        _active_workers.pop(self.WORKER_KEY, None)

        await _agent_session_health_check()

        # Without started_at, we can't determine how long it's been running
        # but if no worker exists, it should still be recovered if started_at is None
        # The health check should handle this gracefully
        running = AgentSession.query.filter(project_key="test", status="running")
        # Legacy jobs without started_at and no worker should still be recovered
        # since we can't determine their age, recovering is safer than leaving them stuck
        assert len(running) == 0

    @pytest.mark.asyncio
    async def test_no_running_jobs_is_noop(self):
        """When no running sessions exist, health check should do nothing."""
        from agent.agent_session_queue import _agent_session_health_check

        # Create only a pending session
        _create_test_session(status="pending")

        await _agent_session_health_check()

        # Nothing should change
        pending = AgentSession.query.filter(project_key="test", status="pending")
        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_recovers_orphan_pending_with_no_running_sessions(self, monkeypatch, caplog):
        """Regression for #1124/#1126: orphan-PENDING recovery must reach _ensure_worker.

        Topology: zero RUNNING sessions + one orphan PENDING session older than
        AGENT_SESSION_HEALTH_MIN_RUNNING with a non-local worker_key.

        Pre-fix, agent/session_health.py's orphan-PENDING branch raised
        UnboundLocalError on the `from agent.agent_session_queue import _ensure_worker`
        line at what is now line 1019 — Python treats the name as function-local due
        to the RUNNING-branch import at line 948, so the import statement itself
        raised before the call at line 1021 could execute. The per-entry
        `except Exception: logger.exception(...)` at line 1023 caught the error and
        logged it, so the function did not propagate — instead, _ensure_worker was
        silently never invoked.

        This test guards the fix by asserting the spy WAS called exactly once with
        the seeded session's worker_key and is_project_keyed, AND that no log
        record mentions UnboundLocalError.
        """
        import logging

        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_MIN_RUNNING,
            _active_workers,
            _agent_session_health_check,
        )

        # Seed one PENDING session with a non-local worker_key (chat_id="789")
        # and a created_at past the 5-minute age threshold.
        seeded_session = _create_test_session(
            status="pending",
            chat_id="789",
            session_id="orphan_pending_session",
            created_at=time.time() - (AGENT_SESSION_HEALTH_MIN_RUNNING + 60),
        )

        # Pre-assertion: topology — zero RUNNING sessions.
        running_pre = AgentSession.query.filter(project_key="test", status="running")
        assert len(running_pre) == 0, (
            f"topology drift: expected zero RUNNING sessions, got {len(running_pre)}"
        )

        # Pre-assertion: worker_key is non-local. If helper defaults ever change to
        # produce a "local"-prefixed key, this test would exercise the abandoned-local
        # branch at agent/session_health.py:994 instead of the orphan-PENDING-with-
        # _ensure_worker branch, and the spy would silently never be called.
        assert not seeded_session.worker_key.startswith("local"), (
            f"topology drift: worker_key={seeded_session.worker_key!r} — this test "
            "exercises the non-local orphan-PENDING branch"
        )

        # Pre-flight cleanup of _active_workers. Mirrors the pattern at
        # test_recovers_job_with_no_worker (line 200). A leaked live worker for the
        # same worker_key would set worker_alive=True at agent/session_health.py:977
        # and cause the health check to skip the orphan-PENDING branch entirely.
        _active_workers.pop(seeded_session.worker_key, None)

        # Spy on _ensure_worker.
        # Patch on the source module — session_health re-imports _ensure_worker
        # locally on each call (agent/session_health.py:1019).
        spy_calls: list[tuple[str, bool]] = []

        def spy(worker_key: str, is_project_keyed: bool = False) -> None:
            spy_calls.append((worker_key, is_project_keyed))

        monkeypatch.setattr("agent.agent_session_queue._ensure_worker", spy)

        # Capture WARNING/ERROR-level logs for the belt-and-braces check below.
        caplog.set_level(logging.WARNING)

        await _agent_session_health_check()

        # Primary assertion: the spy was called exactly once with the derived
        # (worker_key, is_project_keyed) pair from the seeded session. On the
        # pre-fix tree, the UnboundLocalError at line 1019 prevented the call
        # site from ever being reached, so spy_calls would be empty.
        assert spy_calls == [(seeded_session.worker_key, seeded_session.is_project_keyed)], (
            f"spy calls: {spy_calls!r}"
        )

        # Belt-and-braces: no log record should mention UnboundLocalError. On the
        # pre-fix tree, the per-entry `except Exception: logger.exception(...)` at
        # agent/session_health.py:1023 would write this string to the log.
        for record in caplog.records:
            message = record.getMessage()
            assert "UnboundLocalError" not in message, (
                f"pre-fix bug regression detected in log: {message!r}"
            )
            assert "cannot access local variable '_ensure_worker'" not in message, (
                f"pre-fix bug regression detected in log: {message!r}"
            )

    @pytest.mark.asyncio
    async def test_ledger_running_session_with_delivered_response_not_finalized(self):
        """A non-executable ledger anchor (#2042) that ALSO carries
        response_delivered_at (which would otherwise trip
        _delivery_belongs_to_current_run's finalize-to-completed exit) and has
        no live worker (which would otherwise trip the worker_dead recovery
        branch) must be skipped by the is_ledger guard BEFORE either of those
        paths is reached.

        This is the most safety-critical of the five guard sites: it asserts
        the guard's PLACEMENT (before the finalize exit), not merely its
        existence. If the guard were placed after
        _delivery_belongs_to_current_run instead of before it, this test
        would fail with the session incorrectly finalized to "completed".
        """
        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_MIN_RUNNING,
            _agent_session_health_check,
        )

        ledger_project_key = "ledger-running-test"
        started = datetime.now(tz=UTC) - timedelta(seconds=AGENT_SESSION_HEALTH_MIN_RUNNING + 600)
        _create_test_session(
            project_key=ledger_project_key,
            status="running",
            started_at=started,
            response_delivered_at=started + timedelta(seconds=30),
            session_id="sdlc-local-9100",
            is_ledger=True,
        )

        # No live worker registered for this project_key -- absence means
        # worker_alive=False, which would trip the worker_dead recovery branch
        # if the delivery-finalize exit somehow didn't fire first.
        _active_workers.pop(ledger_project_key, None)

        await _agent_session_health_check()

        from agent.session_pickup import _truthy

        running = AgentSession.query.filter(project_key=ledger_project_key, status="running")
        assert len(running) == 1, "ledger anchor must remain running, untouched by either path"
        assert running[0].session_id == "sdlc-local-9100"
        # Popoto round-trips Field(default=False) through Redis as the string
        # "True"/"False" -- use the same _truthy() coercion the guard itself uses.
        assert _truthy(running[0].is_ledger)

        completed = AgentSession.query.filter(project_key=ledger_project_key, status="completed")
        assert len(completed) == 0, "must NOT be finalized to completed via the delivery guard"
        pending = AgentSession.query.filter(project_key=ledger_project_key, status="pending")
        assert len(pending) == 0, "must NOT be recovered to pending via the worker_dead branch"

    @pytest.mark.asyncio
    async def test_ledger_pending_session_not_abandoned(self):
        """A non-executable ledger anchor (#2042) sitting at status=pending,
        aged past the orphan threshold with a 'local'-prefixed worker_key (so
        it WOULD hit the orphaned-local-pending abandon branch), must NOT be
        abandoned by the health check's PENDING loop."""
        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_MIN_RUNNING,
            _agent_session_health_check,
        )

        # Slugless session with no session_type set falls back to
        # worker_key == project_key (models/agent_session.py::worker_key).
        # Prefixing with "local" is what routes into the orphaned-local-pending
        # abandon branch instead of the _ensure_worker nudge branch.
        ledger_project_key = "local-ledger-pending-test"
        _create_test_session(
            project_key=ledger_project_key,
            status="pending",
            session_id="sdlc-local-9200",
            created_at=time.time() - (AGENT_SESSION_HEALTH_MIN_RUNNING + 60),
            is_ledger=True,
        )

        _active_workers.pop(ledger_project_key, None)

        await _agent_session_health_check()

        pending = AgentSession.query.filter(project_key=ledger_project_key, status="pending")
        assert len(pending) == 1, "ledger anchor must remain pending, untouched"
        assert pending[0].session_id == "sdlc-local-9200"

        abandoned = AgentSession.query.filter(project_key=ledger_project_key, status="abandoned")
        assert len(abandoned) == 0, "must NOT be abandoned by the orphaned-local-pending sweep"


class TestJobHealthConstants:
    """Tests for health check constants."""

    def test_constants_exist(self):
        """Health check constants that survived the #1172 simplification."""
        from agent.agent_session_queue import (
            AGENT_SESSION_HEALTH_CHECK_INTERVAL,
            AGENT_SESSION_HEALTH_MIN_RUNNING,
        )

        assert AGENT_SESSION_HEALTH_CHECK_INTERVAL == 300
        assert AGENT_SESSION_HEALTH_MIN_RUNNING == 300


class TestFormatDuration:
    """Tests for the CLI format_duration helper."""

    def test_format_none(self):
        """None input should return 'N/A'."""
        from agent.agent_session_queue import format_duration

        assert format_duration(None) == "N/A"

    def test_format_minutes(self):
        """Short durations should show minutes."""
        from agent.agent_session_queue import format_duration

        assert format_duration(120) == "2m"
        assert format_duration(300) == "5m"

    def test_format_hours(self):
        """Long durations should show hours and minutes."""
        from agent.agent_session_queue import format_duration

        assert format_duration(3600) == "1h0m"
        assert format_duration(5400) == "1h30m"

    def test_format_zero(self):
        """Zero seconds should show 0m."""
        from agent.agent_session_queue import format_duration

        assert format_duration(0) == "0m"
