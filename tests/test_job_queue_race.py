"""Tests for job queue race condition fixes and KeyField index corruption prevention.

Validates the delete-and-recreate pattern used by _pop_job, _recover_interrupted_jobs,
_reset_running_jobs, and the drain guard in _worker_loop.

All tests use redis_test_db fixture (autouse=True in conftest.py) for isolation.
"""

import time

import pytest

from agent.job_queue import (
    RedisJob,
    _extract_job_fields,
    _pop_job,
    _recover_interrupted_jobs,
    _reset_running_jobs,
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


class TestExtractJobFields:
    """Tests for the _extract_job_fields helper."""

    def test_returns_complete_field_set(self):
        """All non-auto fields should be present in the extracted dict."""
        job = _create_test_job(
            sender_id=42,
            chat_title="Test Chat",
            revival_context="some context",
            workflow_id="wf123456",
            work_item_slug="my-feature",
            task_list_id="thread-123-456",
            has_media=True,
            media_type="photo",
            youtube_urls='[["url", "id"]]',
            non_youtube_urls='["https://example.com"]',
            reply_to_msg_id=99,
            chat_id_for_enrichment="enrichment_chat",
            classification_type="bug",
            auto_continue_count=2,
        )

        fields = _extract_job_fields(job)

        # job_id should NOT be in extracted fields (it's AutoKeyField)
        assert "job_id" not in fields

        # All other fields should be present
        assert fields["project_key"] == "test"
        assert fields["status"] == "pending"
        assert fields["priority"] == "high"
        assert fields["created_at"] is not None
        assert fields["session_id"] == "test_session"
        assert fields["working_dir"] == "/tmp/test"
        assert fields["message_text"] == "test message"
        assert fields["sender_name"] == "Test"
        assert fields["sender_id"] == 42
        assert fields["chat_id"] == "123"
        assert fields["message_id"] == 1
        assert fields["chat_title"] == "Test Chat"
        assert fields["revival_context"] == "some context"
        assert fields["workflow_id"] == "wf123456"
        assert fields["work_item_slug"] == "my-feature"
        assert fields["task_list_id"] == "thread-123-456"
        assert fields["has_media"] is True
        assert fields["media_type"] == "photo"
        assert fields["youtube_urls"] == '[["url", "id"]]'
        assert fields["non_youtube_urls"] == '["https://example.com"]'
        assert fields["reply_to_msg_id"] == 99
        assert fields["chat_id_for_enrichment"] == "enrichment_chat"
        assert fields["classification_type"] == "bug"
        assert fields["auto_continue_count"] == 2

    def test_extracted_fields_can_recreate_job(self):
        """Extracted fields should be usable for RedisJob.create()."""
        original = _create_test_job()
        fields = _extract_job_fields(original)

        # Should create successfully without errors
        new_job = RedisJob.create(**fields)
        assert new_job.job_id != original.job_id  # New auto-generated ID
        assert new_job.project_key == original.project_key
        assert new_job.message_text == original.message_text


class TestPopJobDeleteAndRecreate:
    """Tests for _pop_job using delete-and-recreate to prevent stale index entries."""

    @pytest.mark.asyncio
    async def test_pop_job_no_stale_pending(self):
        """After popping a job, it should NOT appear in the pending index."""
        _create_test_job()

        # Verify it's in pending
        pending_before = RedisJob.query.filter(project_key="test", status="pending")
        assert len(pending_before) == 1

        # Pop the job
        job = await _pop_job("test")
        assert job is not None

        # The old pending entry should be gone
        pending_after = RedisJob.query.filter(project_key="test", status="pending")
        assert len(pending_after) == 0, "Stale pending index entry found after _pop_job"

        # The job should be in the running index
        running = RedisJob.query.filter(project_key="test", status="running")
        assert len(running) == 1

    @pytest.mark.asyncio
    async def test_pop_job_returns_none_on_empty_queue(self):
        """_pop_job should return None when no pending jobs exist."""
        result = await _pop_job("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_pop_job_preserves_fields(self):
        """The popped job should retain all original field values."""
        original = _create_test_job(
            session_id="preserve_test",
            sender_name="FieldCheck",
            auto_continue_count=3,
        )

        job = await _pop_job("test")
        assert job is not None
        assert job.session_id == "preserve_test"
        assert job.sender_name == "FieldCheck"
        assert job.auto_continue_count == 3
        # job_id changes (new object), but all other fields preserved
        assert job.job_id != original.job_id

    @pytest.mark.asyncio
    async def test_pop_job_respects_priority_order(self):
        """High priority jobs should be popped before low priority."""
        _create_test_job(
            priority="low",
            message_text="low priority",
            created_at=time.time(),
        )
        _create_test_job(
            priority="high",
            message_text="high priority",
            created_at=time.time() + 1,
        )

        job = await _pop_job("test")
        assert job is not None
        assert job.message_text == "high priority"


class TestRecoverInterruptedJobs:
    """Tests for _recover_interrupted_jobs delete-and-recreate pattern."""

    def test_no_stale_running_after_recovery(self):
        """After recovery, no jobs should remain in the running index."""
        # Create a "running" job (simulating crash)
        _create_test_job(status="running", session_id="crashed_session")

        # Verify it's in running
        running_before = RedisJob.query.filter(project_key="test", status="running")
        assert len(running_before) == 1

        # Run recovery
        recovered = _recover_interrupted_jobs("test")
        assert recovered == 1

        # The running index should be empty
        running_after = RedisJob.query.filter(project_key="test", status="running")
        assert (
            len(running_after) == 0
        ), "Stale running index entry found after _recover_interrupted_jobs"

        # The job should now be in pending with high priority
        pending = RedisJob.query.filter(project_key="test", status="pending")
        assert len(pending) == 1
        assert pending[0].priority == "high"
        assert pending[0].session_id == "crashed_session"

    def test_recover_multiple_running_jobs(self):
        """Recovery should handle multiple running jobs."""
        _create_test_job(status="running", session_id="s1", message_text="msg1")
        _create_test_job(status="running", session_id="s2", message_text="msg2")

        recovered = _recover_interrupted_jobs("test")
        assert recovered == 2

        running = RedisJob.query.filter(project_key="test", status="running")
        assert len(running) == 0

        pending = RedisJob.query.filter(project_key="test", status="pending")
        assert len(pending) == 2

    def test_recover_returns_zero_when_nothing_to_recover(self):
        """Recovery should return 0 when no running jobs exist."""
        recovered = _recover_interrupted_jobs("test")
        assert recovered == 0


class TestResetRunningJobs:
    """Tests for _reset_running_jobs async delete-and-recreate pattern."""

    @pytest.mark.asyncio
    async def test_no_stale_running_after_reset(self):
        """After reset, no jobs should remain in the running index."""
        _create_test_job(status="running", session_id="in_flight")

        running_before = RedisJob.query.filter(project_key="test", status="running")
        assert len(running_before) == 1

        reset_count = await _reset_running_jobs("test")
        assert reset_count == 1

        running_after = RedisJob.query.filter(project_key="test", status="running")
        assert (
            len(running_after) == 0
        ), "Stale running index entry found after _reset_running_jobs"

        pending = RedisJob.query.filter(project_key="test", status="pending")
        assert len(pending) == 1
        assert pending[0].priority == "high"

    @pytest.mark.asyncio
    async def test_reset_returns_zero_when_empty(self):
        """Reset should return 0 when no running jobs exist."""
        result = await _reset_running_jobs("test")
        assert result == 0


class TestDrainGuard:
    """Tests for the worker drain guard logic."""

    @pytest.mark.asyncio
    async def test_drain_guard_double_check_finds_late_job(self):
        """Simulate the drain guard catching a job that appears between checks.

        This test verifies the core drain guard behavior: when _pop_job
        returns None on first try, the worker sleeps and retries. We test
        this by creating a job just before the second _pop_job call.
        """
        # First call: no jobs
        result1 = await _pop_job("test")
        assert result1 is None

        # Simulate a late-arriving job (created during the sleep window)
        _create_test_job(message_text="late arrival")

        # Second call: should find the job
        result2 = await _pop_job("test")
        assert result2 is not None
        assert result2.message_text == "late arrival"

    @pytest.mark.asyncio
    async def test_drain_guard_exits_when_truly_empty(self):
        """When queue is truly empty, both checks should return None."""
        result1 = await _pop_job("test")
        assert result1 is None

        result2 = await _pop_job("test")
        assert result2 is None
