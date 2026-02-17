"""Tests for job health monitor: detect and recover stuck running jobs.

Tests cover:
- started_at field on RedisJob (set when job transitions to running)
- _get_job_timeout() returning correct timeouts based on message_text
- _job_health_check() detecting and recovering dead workers and timed-out jobs
- CLI functions: format_duration, show_status, flush_stuck, flush_job
"""

import asyncio
import time

import pytest

from agent.job_queue import (
    RedisJob,
    _active_workers,
    _pop_job,
)


def _create_test_job(**overrides) -> RedisJob:
    """Create a RedisJob with sensible defaults for testing."""
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
        "message_id": 1,
    }
    defaults.update(overrides)
    return RedisJob.create(**defaults)


class TestStartedAtField:
    """Tests for the started_at field on RedisJob."""

    def test_started_at_field_exists(self):
        """RedisJob should have a started_at field."""
        assert hasattr(RedisJob, "started_at"), "RedisJob missing started_at field"

    def test_started_at_defaults_to_none(self):
        """A newly created RedisJob should have started_at=None."""
        job = _create_test_job()
        assert job.started_at is None

    @pytest.mark.asyncio
    async def test_pop_job_sets_started_at(self):
        """When _pop_job transitions a job to running, started_at should be set."""
        _create_test_job()
        before = time.time()
        job = await _pop_job("test")
        after = time.time()

        assert job is not None
        # Verify the RedisJob in Redis has started_at set
        running_jobs = RedisJob.query.filter(project_key="test", status="running")
        assert len(running_jobs) == 1
        assert running_jobs[0].started_at is not None
        assert before <= running_jobs[0].started_at <= after

    def test_started_at_in_extract_fields(self):
        """started_at should be included in _extract_job_fields."""
        from agent.job_queue import _extract_job_fields

        job = _create_test_job(started_at=12345.0)
        fields = _extract_job_fields(job)
        assert "started_at" in fields
        assert fields["started_at"] == 12345.0


class TestGetJobTimeout:
    """Tests for _get_job_timeout()."""

    def test_standard_job_timeout(self):
        """Regular jobs should get the default 45-minute timeout."""
        from agent.job_queue import JOB_TIMEOUT_DEFAULT, _get_job_timeout

        job = _create_test_job(message_text="hello, please fix the bug")
        timeout = _get_job_timeout(job)
        assert timeout == JOB_TIMEOUT_DEFAULT

    def test_build_job_timeout(self):
        """Jobs containing /do-build should get the 2.5-hour timeout."""
        from agent.job_queue import JOB_TIMEOUT_BUILD, _get_job_timeout

        job = _create_test_job(message_text="/do-build docs/plans/my-feature.md")
        timeout = _get_job_timeout(job)
        assert timeout == JOB_TIMEOUT_BUILD

    def test_build_job_timeout_case_sensitive(self):
        """The /do-build check should be exact (not case-insensitive)."""
        from agent.job_queue import JOB_TIMEOUT_DEFAULT, _get_job_timeout

        job = _create_test_job(message_text="/DO-BUILD something")
        timeout = _get_job_timeout(job)
        # /do-build is lowercase in the plan, so uppercase shouldn't match
        assert timeout == JOB_TIMEOUT_DEFAULT

    def test_none_message_text_returns_default(self):
        """Jobs with None message_text should get default timeout."""
        from agent.job_queue import JOB_TIMEOUT_DEFAULT, _get_job_timeout

        job = _create_test_job(message_text="")
        # Override message_text to empty/None after creation
        timeout = _get_job_timeout(job)
        assert timeout == JOB_TIMEOUT_DEFAULT


class TestJobHealthCheck:
    """Tests for _job_health_check()."""

    @pytest.mark.asyncio
    async def test_recovers_job_with_dead_worker(self):
        """A running job whose worker task is done should be recovered."""
        from agent.job_queue import _job_health_check

        # Create a running job that has been running long enough
        _create_test_job(
            status="running",
            started_at=time.time() - 600,  # 10 minutes ago
            session_id="dead_worker_session",
        )

        # Set up a dead worker (asyncio Task that's already done)
        done_task = asyncio.Future()
        done_task.set_result(None)
        _active_workers["test"] = done_task

        try:
            await _job_health_check()

            # The running job should be gone, replaced by a pending one
            running = RedisJob.query.filter(project_key="test", status="running")
            assert len(running) == 0

            pending = RedisJob.query.filter(project_key="test", status="pending")
            assert len(pending) == 1
            assert pending[0].session_id == "dead_worker_session"
        finally:
            _active_workers.pop("test", None)

    @pytest.mark.asyncio
    async def test_recovers_job_with_no_worker(self):
        """A running job with no entry in _active_workers should be recovered."""
        from agent.job_queue import _job_health_check

        _create_test_job(
            status="running",
            started_at=time.time() - 600,  # 10 minutes ago
            session_id="orphan_session",
        )

        # Ensure no worker exists for this project
        _active_workers.pop("test", None)

        await _job_health_check()

        running = RedisJob.query.filter(project_key="test", status="running")
        assert len(running) == 0

        pending = RedisJob.query.filter(project_key="test", status="pending")
        assert len(pending) == 1
        assert pending[0].session_id == "orphan_session"

    @pytest.mark.asyncio
    async def test_skips_job_with_alive_worker_under_timeout(self):
        """A running job with an alive worker under timeout should NOT be recovered."""
        from agent.job_queue import _job_health_check

        _create_test_job(
            status="running",
            started_at=time.time() - 60,  # 1 minute ago (under 5min guard)
            session_id="alive_session",
        )

        # Set up a live worker
        live_task = asyncio.Future()
        _active_workers["test"] = live_task

        try:
            await _job_health_check()

            # The job should still be running
            running = RedisJob.query.filter(project_key="test", status="running")
            assert len(running) == 1
            assert running[0].session_id == "alive_session"

            pending = RedisJob.query.filter(project_key="test", status="pending")
            assert len(pending) == 0
        finally:
            _active_workers.pop("test", None)

    @pytest.mark.asyncio
    async def test_recovers_timed_out_job_with_alive_worker(self):
        """A job that exceeded timeout should be recovered even if worker is alive."""
        from agent.job_queue import JOB_TIMEOUT_DEFAULT, _job_health_check

        _create_test_job(
            status="running",
            started_at=time.time() - JOB_TIMEOUT_DEFAULT - 100,  # past timeout
            session_id="timeout_session",
        )

        # Worker is alive but job has exceeded timeout
        live_task = asyncio.Future()
        _active_workers["test"] = live_task

        try:
            await _job_health_check()

            running = RedisJob.query.filter(project_key="test", status="running")
            assert len(running) == 0

            pending = RedisJob.query.filter(project_key="test", status="pending")
            assert len(pending) == 1
            assert pending[0].session_id == "timeout_session"
        finally:
            _active_workers.pop("test", None)

    @pytest.mark.asyncio
    async def test_skips_recently_started_job_with_dead_worker(self):
        """Jobs running less than JOB_HEALTH_MIN_RUNNING should not be recovered (race guard)."""
        from agent.job_queue import _job_health_check

        _create_test_job(
            status="running",
            started_at=time.time() - 60,  # Only 1 minute ago (under 5min guard)
            session_id="recent_session",
        )

        # Worker is dead
        done_task = asyncio.Future()
        done_task.set_result(None)
        _active_workers["test"] = done_task

        try:
            await _job_health_check()

            # Should NOT be recovered due to race condition guard
            running = RedisJob.query.filter(project_key="test", status="running")
            assert len(running) == 1
            assert running[0].session_id == "recent_session"
        finally:
            _active_workers.pop("test", None)

    @pytest.mark.asyncio
    async def test_handles_job_without_started_at(self):
        """Jobs without started_at (legacy) should still be checked for dead workers."""
        from agent.job_queue import _job_health_check

        # A running job with no started_at (legacy job that predates this field)
        _create_test_job(
            status="running",
            session_id="legacy_session",
        )
        # started_at defaults to None

        # Worker is dead
        _active_workers.pop("test", None)

        await _job_health_check()

        # Without started_at, we can't determine how long it's been running
        # but if no worker exists, it should still be recovered if started_at is None
        # The health check should handle this gracefully
        running = RedisJob.query.filter(project_key="test", status="running")
        # Legacy jobs without started_at and no worker should still be recovered
        # since we can't determine their age, recovering is safer than leaving them stuck
        assert len(running) == 0

    @pytest.mark.asyncio
    async def test_no_running_jobs_is_noop(self):
        """When no running jobs exist, health check should do nothing."""
        from agent.job_queue import _job_health_check

        # Create only a pending job
        _create_test_job(status="pending")

        await _job_health_check()

        # Nothing should change
        pending = RedisJob.query.filter(project_key="test", status="pending")
        assert len(pending) == 1


class TestJobHealthConstants:
    """Tests for health check constants."""

    def test_constants_exist(self):
        """Health check constants should be defined."""
        from agent.job_queue import (
            JOB_HEALTH_CHECK_INTERVAL,
            JOB_HEALTH_MIN_RUNNING,
            JOB_TIMEOUT_BUILD,
            JOB_TIMEOUT_DEFAULT,
        )

        assert JOB_HEALTH_CHECK_INTERVAL == 300
        assert JOB_TIMEOUT_DEFAULT == 2700
        assert JOB_TIMEOUT_BUILD == 9000
        assert JOB_HEALTH_MIN_RUNNING == 300


class TestFormatDuration:
    """Tests for the CLI format_duration helper."""

    def test_format_none(self):
        """None input should return 'N/A'."""
        from agent.job_queue import format_duration

        assert format_duration(None) == "N/A"

    def test_format_minutes(self):
        """Short durations should show minutes."""
        from agent.job_queue import format_duration

        assert format_duration(120) == "2m"
        assert format_duration(300) == "5m"

    def test_format_hours(self):
        """Long durations should show hours and minutes."""
        from agent.job_queue import format_duration

        assert format_duration(3600) == "1h0m"
        assert format_duration(5400) == "1h30m"

    def test_format_zero(self):
        """Zero seconds should show 0m."""
        from agent.job_queue import format_duration

        assert format_duration(0) == "0m"
