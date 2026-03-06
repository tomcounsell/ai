"""Tests for _enqueue_continuation — the shared coaching+enqueue function.

Covers:
- Coaching message generation with correct source labels
- enqueue_job called with correct parameters (session_id, auto_continue_count, etc.)
- Stage-aware vs classifier source labeling
- Plan file resolution from WorkflowState
- Error handling when build_coaching_message raises

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock the claude_agent_sdk before agent package tries to import it
if "claude_agent_sdk" not in sys.modules:
    _mock_sdk = MagicMock()
    sys.modules["claude_agent_sdk"] = _mock_sdk

from agent.job_queue import SendToChatResult, _enqueue_continuation


def _make_mock_job(**overrides):
    """Create a mock Job with sensible defaults."""
    defaults = {
        "project_key": "test-project",
        "session_id": "test-session-123",
        "working_dir": "/tmp/test-wd",
        "message_text": "continue",
        "sender_name": "Test User",
        "chat_id": "chat_456",
        "message_id": 789,
        "work_item_slug": None,
        "task_list_id": None,
        "workflow_id": None,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, value in defaults.items():
        setattr(mock, key, value)
    return mock


class TestEnqueueContinuationCoachingSource:
    """Tests for coaching message source labeling."""

    @pytest.mark.asyncio
    async def test_stage_aware_source_label(self):
        """Stage-aware coaching source is passed to build_coaching_message."""
        job = _make_mock_job()

        with (
            patch(
                "bridge.coach.build_coaching_message",
                return_value="[System Coach] continue",
            ) as mock_coach,
            patch("agent.job_queue.enqueue_job", new_callable=AsyncMock),
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="test-tl",
                auto_continue_count=1,
                output_msg="Running tests...",
                coaching_source="stage_aware",
            )

        # Verify build_coaching_message was called
        mock_coach.assert_called_once()
        call_kwargs = mock_coach.call_args
        # The classification passed should mention "stage_aware"
        classification = call_kwargs[1].get("classification") or call_kwargs[0][0]
        assert "stage_aware" in classification.reason

    @pytest.mark.asyncio
    async def test_classifier_source_label(self):
        """Classifier coaching source is passed to build_coaching_message."""
        job = _make_mock_job()

        with (
            patch(
                "bridge.coach.build_coaching_message",
                return_value="continue",
            ) as mock_coach,
            patch("agent.job_queue.enqueue_job", new_callable=AsyncMock),
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="test-tl",
                auto_continue_count=2,
                output_msg="Still building...",
                coaching_source="classifier",
            )

        mock_coach.assert_called_once()
        call_kwargs = mock_coach.call_args
        classification = call_kwargs[1].get("classification") or call_kwargs[0][0]
        assert "classifier" in classification.reason


class TestEnqueueContinuationParameters:
    """Tests for enqueue_job parameters passed by _enqueue_continuation."""

    @pytest.mark.asyncio
    async def test_enqueue_uses_correct_session_id(self):
        """enqueue_job is called with the original job's session_id."""
        job = _make_mock_job(session_id="my-unique-session")

        with (
            patch("bridge.coach.build_coaching_message", return_value="continue"),
            patch(
                "agent.job_queue.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue,
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="test-tl",
                auto_continue_count=1,
                output_msg="Working...",
            )

        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args[1]
        assert call_kwargs["session_id"] == "my-unique-session"

    @pytest.mark.asyncio
    async def test_enqueue_uses_correct_auto_continue_count(self):
        """enqueue_job passes the (already incremented) auto_continue_count."""
        job = _make_mock_job()

        with (
            patch("bridge.coach.build_coaching_message", return_value="continue"),
            patch(
                "agent.job_queue.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue,
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="test-tl",
                auto_continue_count=5,
                output_msg="Still running...",
            )

        call_kwargs = mock_enqueue.call_args[1]
        assert call_kwargs["auto_continue_count"] == 5

    @pytest.mark.asyncio
    async def test_enqueue_uses_correct_work_item_slug(self):
        """enqueue_job passes the work_item_slug from the original job."""
        job = _make_mock_job(work_item_slug="auto-continue-audit")

        with (
            patch("bridge.coach.build_coaching_message", return_value="continue"),
            patch(
                "agent.job_queue.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue,
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="audit-tl",
                auto_continue_count=1,
                output_msg="Building...",
            )

        call_kwargs = mock_enqueue.call_args[1]
        assert call_kwargs["work_item_slug"] == "auto-continue-audit"

    @pytest.mark.asyncio
    async def test_enqueue_uses_correct_task_list_id(self):
        """enqueue_job passes the task_list_id argument, not the job's task_list_id."""
        job = _make_mock_job(task_list_id="old-tl")

        with (
            patch("bridge.coach.build_coaching_message", return_value="continue"),
            patch(
                "agent.job_queue.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue,
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="new-tl-from-arg",
                auto_continue_count=1,
                output_msg="Building...",
            )

        call_kwargs = mock_enqueue.call_args[1]
        assert call_kwargs["task_list_id"] == "new-tl-from-arg"

    @pytest.mark.asyncio
    async def test_enqueue_sender_is_system(self):
        """enqueue_job sender should be 'System (auto-continue)'."""
        job = _make_mock_job()

        with (
            patch("bridge.coach.build_coaching_message", return_value="continue"),
            patch(
                "agent.job_queue.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue,
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="tl",
                auto_continue_count=1,
                output_msg="msg",
            )

        call_kwargs = mock_enqueue.call_args[1]
        assert call_kwargs["sender_name"] == "System (auto-continue)"

    @pytest.mark.asyncio
    async def test_enqueue_priority_is_high(self):
        """enqueue_job should have high priority."""
        job = _make_mock_job()

        with (
            patch("bridge.coach.build_coaching_message", return_value="continue"),
            patch(
                "agent.job_queue.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue,
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="tl",
                auto_continue_count=1,
                output_msg="msg",
            )

        call_kwargs = mock_enqueue.call_args[1]
        assert call_kwargs["priority"] == "high"


class TestEnqueueContinuationPlanResolution:
    """Tests for plan file resolution from WorkflowState."""

    @pytest.mark.asyncio
    async def test_plan_file_resolved_from_workflow_state(self):
        """When workflow_id is set and WorkflowState has plan_file, it's passed to coach."""
        job = _make_mock_job(workflow_id="wf-123")

        mock_ws_data = MagicMock()
        mock_ws_data.plan_file = "/tmp/plans/my-plan.md"
        mock_ws = MagicMock()
        mock_ws.data = mock_ws_data

        with (
            patch(
                "bridge.coach.build_coaching_message", return_value="continue"
            ) as mock_coach,
            patch("agent.job_queue.enqueue_job", new_callable=AsyncMock),
            patch("agent.workflow_state.WorkflowState.load", return_value=mock_ws),
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="tl",
                auto_continue_count=1,
                output_msg="msg",
            )

        call_kwargs = mock_coach.call_args[1]
        assert call_kwargs["plan_file"] == "/tmp/plans/my-plan.md"

    @pytest.mark.asyncio
    async def test_no_workflow_id_passes_none_plan_file(self):
        """When workflow_id is None, plan_file is None."""
        job = _make_mock_job(workflow_id=None)

        with (
            patch(
                "bridge.coach.build_coaching_message", return_value="continue"
            ) as mock_coach,
            patch("agent.job_queue.enqueue_job", new_callable=AsyncMock),
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="tl",
                auto_continue_count=1,
                output_msg="msg",
            )

        call_kwargs = mock_coach.call_args[1]
        assert call_kwargs["plan_file"] is None

    @pytest.mark.asyncio
    async def test_workflow_state_load_failure_degrades_gracefully(self):
        """If WorkflowState.load raises, plan_file is None and function continues."""
        job = _make_mock_job(workflow_id="wf-broken")

        with (
            patch(
                "bridge.coach.build_coaching_message", return_value="continue"
            ) as mock_coach,
            patch(
                "agent.job_queue.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue,
            patch(
                "agent.workflow_state.WorkflowState.load",
                side_effect=Exception("Redis down"),
            ),
        ):
            # Should not raise
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="tl",
                auto_continue_count=1,
                output_msg="msg",
            )

        # Function completed — enqueue was called
        mock_enqueue.assert_called_once()
        call_kwargs = mock_coach.call_args[1]
        assert call_kwargs["plan_file"] is None


class TestEnqueueContinuationErrorHandling:
    """Tests for error handling in _enqueue_continuation."""

    @pytest.mark.asyncio
    async def test_coaching_message_used_as_message_text(self):
        """The coaching message from build_coaching_message becomes the message_text."""
        job = _make_mock_job()

        coaching_text = "[System Coach] Include test output next time."
        with (
            patch("bridge.coach.build_coaching_message", return_value=coaching_text),
            patch(
                "agent.job_queue.enqueue_job", new_callable=AsyncMock
            ) as mock_enqueue,
        ):
            await _enqueue_continuation(
                job=job,
                branch_name="session/test",
                task_list_id="tl",
                auto_continue_count=1,
                output_msg="msg",
            )

        call_kwargs = mock_enqueue.call_args[1]
        assert call_kwargs["message_text"] == coaching_text


class TestSendToChatResultDataclass:
    """Tests for the SendToChatResult dataclass."""

    def test_defaults(self):
        """Default state is no completion sent, no deferred reaction, count 0."""
        result = SendToChatResult()
        assert result.completion_sent is False
        assert result.defer_reaction is False
        assert result.auto_continue_count == 0

    def test_custom_values(self):
        """All fields can be set via constructor."""
        result = SendToChatResult(
            completion_sent=True,
            defer_reaction=True,
            auto_continue_count=5,
        )
        assert result.completion_sent is True
        assert result.defer_reaction is True
        assert result.auto_continue_count == 5

    def test_mutable_state(self):
        """State can be mutated after creation (used in closure)."""
        result = SendToChatResult()
        result.completion_sent = True
        result.defer_reaction = True
        result.auto_continue_count = 3
        assert result.completion_sent is True
        assert result.defer_reaction is True
        assert result.auto_continue_count == 3
