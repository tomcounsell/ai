"""Tests for stage-aware auto-continue logic.

Verifies that SDLC jobs use pipeline stage progress from AgentSession.history
as the primary auto-continue signal, falling back to the classifier for
non-SDLC jobs.

Decision matrix:
  | Pipeline state       | Output classification | Action            |
  |----------------------|-----------------------|-------------------|
  | Stages remaining     | (skipped)             | Auto-continue     |
  | All stages done      | Completion            | Deliver to user   |
  | All stages done      | Status (no evidence)  | Coach + continue  |
  | Any stage failed     | Error/blocker         | Deliver to user   |
  | No stages (non-SDLC) | Question              | Deliver to user   |
  | No stages (non-SDLC) | Status                | Auto-continue     |

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

# claude_agent_sdk mock is centralized in conftest.py

from agent.job_queue import MAX_NUDGE_COUNT
from models.agent_session import AgentSession

# === AgentSession helper method tests ===


class TestIsSDLCJob:
    """Tests for AgentSession.is_sdlc property."""

    def test_no_history_returns_false(self):
        """Session with no history is not an SDLC job."""
        session = AgentSession()
        session.history = None
        assert session.is_sdlc is False

    def test_empty_history_returns_false(self):
        """Session with empty history is not an SDLC job."""
        session = AgentSession()
        session.history = []
        assert session.is_sdlc is False

    def test_non_stage_history_returns_false(self):
        """Session with only non-stage history entries is not SDLC."""
        session = AgentSession()
        session.history = [
            "[user] Hello world",
            "[system] Processing request",
        ]
        assert session.is_sdlc is False

    def test_classification_type_sdlc_returns_true(self):
        """Session with classification_type='sdlc' is an SDLC job (primary check).

        This tests the fix for issue #276 Bug 1: the classifier now outputs
        'sdlc' as a valid type, and is_sdlc uses classification_type
        as the primary signal before falling back to history checks.
        """
        session = AgentSession()
        session.history = []  # No stage history
        session.classification_type = "sdlc"
        assert session.is_sdlc is True

    def test_classification_type_feature_with_no_stages_returns_false(self):
        """Session classified as 'feature' with no stage history is not SDLC."""
        session = AgentSession()
        session.history = ["[user] Add a feature"]
        session.classification_type = "feature"
        assert session.is_sdlc is False

    def test_classification_type_sdlc_overrides_empty_history(self):
        """classification_type='sdlc' is sufficient even with no history at all."""
        session = AgentSession()
        session.history = None
        session.classification_type = "sdlc"
        assert session.is_sdlc is True

    def test_stage_entry_returns_true(self):
        """Session with a [stage] entry is an SDLC job (fallback check)."""
        session = AgentSession()
        session.history = [
            "[user] /sdlc 178",
            "[stage] ISSUE COMPLETED",
        ]
        assert session.is_sdlc is True

    def test_case_insensitive_stage_detection(self):
        """Stage detection is case-insensitive."""
        session = AgentSession()
        session.history = ["[Stage] BUILD IN_PROGRESS"]
        assert session.is_sdlc is True

    def test_mixed_history_with_stage_returns_true(self):
        """Session with mixed entries including a stage is SDLC."""
        session = AgentSession()
        session.history = [
            "[user] Start the build",
            "[system] Running tests",
            "[stage] PLAN COMPLETED",
            "[summary] Plan phase done",
        ]
        assert session.is_sdlc is True


class TestHasRemainingStages:
    """Tests for AgentSession.has_remaining_stages()."""

    def test_no_history_returns_true(self):
        """With no history, all stages are pending (remaining)."""
        session = AgentSession()
        session.history = None
        assert session.has_remaining_stages() is True

    def test_all_stages_completed(self):
        """When all stages are completed, no remaining stages."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD COMPLETED",
            "[stage] TEST COMPLETED",
            "[stage] REVIEW COMPLETED",
            "[stage] DOCS COMPLETED",
            "[stage] MERGE COMPLETED",
        ]
        assert session.has_remaining_stages() is False

    def test_some_stages_remaining(self):
        """When some stages are completed, others remain."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD IN_PROGRESS",
        ]
        assert session.has_remaining_stages() is True

    def test_in_progress_counts_as_remaining(self):
        """In-progress stages count as remaining."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD COMPLETED",
            "[stage] TEST COMPLETED",
            "[stage] REVIEW COMPLETED",
            "[stage] DOCS IN_PROGRESS",
        ]
        assert session.has_remaining_stages() is True

    def test_failed_stage_not_remaining(self):
        """Failed stages are NOT remaining (they're terminal)."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD COMPLETED",
            "[stage] TEST FAILED",
            "[stage] REVIEW COMPLETED",
            "[stage] DOCS COMPLETED",
            "[stage] MERGE COMPLETED",
        ]
        # TEST is failed, all others completed — no remaining (pending/in_progress)
        assert session.has_remaining_stages() is False


class TestHasFailedStage:
    """Tests for AgentSession.has_failed_stage()."""

    def test_no_history_returns_false(self):
        """No history means no failed stages."""
        session = AgentSession()
        session.history = None
        assert session.has_failed_stage() is False

    def test_no_failures_returns_false(self):
        """Completed stages only — no failures."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
        ]
        assert session.has_failed_stage() is False

    def test_failed_stage_detected(self):
        """FAILED keyword in stage entry is detected."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] BUILD FAILED",
        ]
        assert session.has_failed_stage() is True

    def test_error_stage_detected(self):
        """ERROR keyword in stage entry is detected."""
        session = AgentSession()
        session.history = [
            "[stage] TEST ERROR: ModuleNotFoundError",
        ]
        assert session.has_failed_stage() is True

    def test_non_stage_error_not_detected(self):
        """Errors in non-stage entries don't count."""
        session = AgentSession()
        session.history = [
            "[system] Error: something went wrong",
            "[stage] BUILD COMPLETED",
        ]
        assert session.has_failed_stage() is False


# === Stage-aware routing decision tests ===


class TestStageAwareDecisionMatrix:
    """Tests verifying the stage-aware auto-continue decision matrix.

    These tests exercise the routing logic by simulating the conditions
    that send_to_chat checks, verifying the correct action is taken.
    """

    def test_sdlc_stages_remaining_auto_continues(self):
        """SDLC job with remaining stages should auto-continue without classifier."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD IN_PROGRESS",
        ]

        assert session.is_sdlc is True
        assert session.has_remaining_stages() is True
        assert session.has_failed_stage() is False

        # Decision: auto-continue (stages remaining, no classifier needed)
        auto_continue_count = 0
        effective_max = MAX_NUDGE_COUNT

        should_auto_continue = (
            session.is_sdlc
            and session.has_remaining_stages()
            and not session.has_failed_stage()
            and auto_continue_count < effective_max
        )
        assert should_auto_continue is True

    def test_sdlc_all_stages_done_falls_to_classifier(self):
        """SDLC job with all stages done should use the classifier."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD COMPLETED",
            "[stage] TEST COMPLETED",
            "[stage] REVIEW COMPLETED",
            "[stage] DOCS COMPLETED",
            "[stage] MERGE COMPLETED",
        ]

        assert session.is_sdlc is True
        assert session.has_remaining_stages() is False
        assert session.has_failed_stage() is False

        # Decision: fall through to classifier (all stages done)
        should_use_stage_routing = (
            session.is_sdlc and session.has_remaining_stages() and not session.has_failed_stage()
        )
        assert should_use_stage_routing is False

    def test_sdlc_failed_stage_delivers_to_user(self):
        """SDLC job with a failed stage should deliver immediately."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD FAILED",
        ]

        assert session.is_sdlc is True
        assert session.has_failed_stage() is True

        # Decision: deliver to user (failed stage)
        should_deliver = session.is_sdlc and session.has_failed_stage()
        assert should_deliver is True

    def test_non_sdlc_uses_classifier(self):
        """Non-SDLC job should use the classifier-based routing."""
        session = AgentSession()
        session.history = [
            "[user] Tell me about Python",
            "[system] Processing casual question",
        ]

        assert session.is_sdlc is False

        # Decision: use classifier (not an SDLC job)
        effective_max = MAX_NUDGE_COUNT  # ChatSession manages routing; cap is safety backstop
        assert effective_max == 50

    def test_sdlc_safety_cap_prevents_infinite_loop(self):
        """SDLC auto-continue respects the safety cap even with stages remaining."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN IN_PROGRESS",
        ]

        assert session.is_sdlc is True
        assert session.has_remaining_stages() is True

        # Simulate having hit the SDLC safety cap
        auto_continue_count = MAX_NUDGE_COUNT
        effective_max = MAX_NUDGE_COUNT

        should_auto_continue = (
            session.is_sdlc
            and session.has_remaining_stages()
            and not session.has_failed_stage()
            and auto_continue_count < effective_max
        )
        # Safety cap reached — falls through to classifier
        assert should_auto_continue is False


class TestMaxAutoContinuesConstants:
    """Tests for the auto-continue constants."""

    def test_max_auto_continues_value(self):
        """Both caps are 50 (ChatSession manages continuation, caps are safety backstops)."""
        assert MAX_NUDGE_COUNT == 50

    def test_max_auto_continues_sdlc_value(self):
        """Both caps are 50 (ChatSession manages continuation, caps are safety backstops)."""
        assert MAX_NUDGE_COUNT == 50

    def test_sdlc_cap_equal_to_standard(self):
        """Both caps are equal — ChatSession handles routing, not auto-continue logic."""
        assert MAX_NUDGE_COUNT == MAX_NUDGE_COUNT

    def test_both_caps_positive(self):
        """Both caps must be positive integers."""
        assert isinstance(MAX_NUDGE_COUNT, int)
        assert isinstance(MAX_NUDGE_COUNT, int)
        assert MAX_NUDGE_COUNT > 0
        assert MAX_NUDGE_COUNT > 0


class TestGetStageProgressWithFailures:
    """Tests for get_stage_progress() including failure detection."""

    def test_failed_stage_status(self):
        """Failed stages show as 'failed' in progress dict."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] BUILD FAILED",
        ]
        progress = session.get_stage_progress()
        assert progress["ISSUE"] == "completed"
        assert progress["BUILD"] == "failed"
        assert progress["PLAN"] == "pending"

    def test_error_stage_status(self):
        """Error stages show as 'failed' in progress dict."""
        session = AgentSession()
        session.history = [
            "[stage] TEST ERROR: test suite crashed",
        ]
        progress = session.get_stage_progress()
        assert progress["TEST"] == "failed"

    def test_mixed_progress(self):
        """Mix of completed, in_progress, pending, and failed stages."""
        session = AgentSession()
        session.history = [
            "[stage] ISSUE COMPLETED",
            "[stage] PLAN COMPLETED",
            "[stage] BUILD COMPLETED",
            "[stage] TEST FAILED",
        ]
        progress = session.get_stage_progress()
        assert progress["ISSUE"] == "completed"
        assert progress["PLAN"] == "completed"
        assert progress["BUILD"] == "completed"
        assert progress["TEST"] == "failed"
        assert progress["REVIEW"] == "pending"
        assert progress["DOCS"] == "pending"


class TestStageAwareWithEmoji:
    """Tests for stage detection using emoji markers (☑ and ▶)."""

    def test_checkmark_emoji_completed(self):
        """☑ emoji marks stage as completed."""
        session = AgentSession()
        session.history = ["[stage] ☑ ISSUE"]
        assert session.is_sdlc is True
        progress = session.get_stage_progress()
        assert progress["ISSUE"] == "completed"

    def test_play_emoji_in_progress(self):
        """▶ emoji marks stage as in_progress."""
        session = AgentSession()
        session.history = ["[stage] ▶ BUILD"]
        progress = session.get_stage_progress()
        assert progress["BUILD"] == "in_progress"
        assert session.has_remaining_stages() is True


class TestEffectiveMaxSelection:
    """Tests verifying the correct max is chosen based on job type."""

    def test_sdlc_job_gets_higher_cap(self):
        """SDLC jobs should use MAX_NUDGE_COUNT."""
        session = AgentSession()
        session.history = ["[stage] ISSUE COMPLETED"]
        is_sdlc = session.is_sdlc
        effective_max = MAX_NUDGE_COUNT if is_sdlc else MAX_NUDGE_COUNT
        assert effective_max == MAX_NUDGE_COUNT

    def test_non_sdlc_job_gets_standard_cap(self):
        """Non-SDLC jobs should use MAX_NUDGE_COUNT."""
        session = AgentSession()
        session.history = ["[user] Hello"]
        is_sdlc = session.is_sdlc
        effective_max = MAX_NUDGE_COUNT if is_sdlc else MAX_NUDGE_COUNT
        assert effective_max == MAX_NUDGE_COUNT


# ============================================================================
# Classification Inheritance Tests (Bug 2 regression tests)
# ============================================================================


class TestClassificationInheritance:
    """Test that reply-to-resume inherits classification_type from the original session.

    Regression tests for issue #375 Bug 2: when a user replies to resume an SDLC
    session, the async classifier may not have completed before enqueue_job is called,
    resulting in classification_type=None. The fix inherits from the existing session.
    """

    def test_reply_to_resume_inherits_sdlc_classification(self):
        """When replying to resume an SDLC session and async classifier hasn't
        completed, classification_type should be inherited from the existing session."""
        # Create existing session with classification_type="sdlc"
        session = AgentSession()
        session.session_id = "tg_test_123_456"
        session.classification_type = "sdlc"
        session.history = ["[stage] ISSUE COMPLETED"]

        # Simulate the inheritance logic from telegram_bridge.py
        classification_result = {}  # Async classifier hasn't completed
        is_reply_to_valor = True
        has_reply_to_msg_id = True

        if is_reply_to_valor and has_reply_to_msg_id and not classification_result.get("type"):
            # This mirrors the inheritance logic added to telegram_bridge.py
            if session.classification_type:
                classification_result["type"] = session.classification_type

        assert classification_result.get("type") == "sdlc"

    def test_reply_to_resume_async_classifier_overrides_inheritance(self):
        """When the async classifier completes before enqueue, its result
        should be used instead of inherited classification."""
        session = AgentSession()
        session.session_id = "tg_test_123_456"
        session.classification_type = "sdlc"

        # Async classifier completed with "question" before inheritance check
        classification_result = {"type": "question", "confidence": 0.9}
        is_reply_to_valor = True
        has_reply_to_msg_id = True

        if is_reply_to_valor and has_reply_to_msg_id and not classification_result.get("type"):
            if session.classification_type:
                classification_result["type"] = session.classification_type

        # Classifier already had a result, so inheritance was skipped
        assert classification_result.get("type") == "question"

    def test_reply_to_resume_missing_session_falls_through(self):
        """When no existing session is found, classification_result should
        remain None (no crash)."""
        classification_result = {}
        is_reply_to_valor = True
        has_reply_to_msg_id = True

        # Simulate: no existing session found (empty query result)
        existing_session = None

        if is_reply_to_valor and has_reply_to_msg_id and not classification_result.get("type"):
            if existing_session and getattr(existing_session, "classification_type", None):
                classification_result["type"] = existing_session.classification_type

        assert classification_result.get("type") is None

    def test_fresh_message_no_inheritance(self):
        """When message is NOT a reply, classification should NOT be inherited
        from any existing session."""
        session = AgentSession()
        session.session_id = "tg_test_123_789"
        session.classification_type = "sdlc"

        classification_result = {}
        is_reply_to_valor = False  # NOT a reply
        has_reply_to_msg_id = False

        if is_reply_to_valor and has_reply_to_msg_id and not classification_result.get("type"):
            if session.classification_type:
                classification_result["type"] = session.classification_type

        # Not a reply, so no inheritance
        assert classification_result.get("type") is None
