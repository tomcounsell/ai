"""Tests for the Observer Agent and Stage Detector.

Tests the deterministic stage detector (pure function, no mocks needed)
and the Observer's routing decision framework.
"""

import json

import pytest

from bridge.observer import Observer
from bridge.stage_detector import STAGE_ORDER, apply_transitions, detect_stages
from models.agent_session import SDLC_STAGES

# ============================================================================
# Stage Detector Tests (pure function — no Redis, no API)
# ============================================================================


class TestDetectStages:
    """Test the deterministic stage detector."""

    def test_empty_transcript(self):
        """Empty transcript returns no transitions."""
        assert detect_stages("") == []

    def test_none_transcript(self):
        """None-like empty input returns no transitions."""
        assert detect_stages("") == []

    def test_single_skill_invocation(self):
        """Detect a single /do-build invocation."""
        transcript = "Running /do-build docs/plans/my-feature.md"
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "BUILD" and t["status"] == "in_progress" for t in transitions)

    def test_do_plan_invocation(self):
        """Detect /do-plan skill invocation."""
        transcript = "I'll invoke /do-plan observer-agent to create the plan."
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "PLAN" and t["status"] == "in_progress" for t in transitions)

    def test_do_test_invocation(self):
        """Detect /do-test skill invocation."""
        transcript = "Now running /do-test to validate the implementation."
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "TEST" and t["status"] == "in_progress" for t in transitions)

    def test_do_pr_review_invocation(self):
        """Detect /do-pr-review skill invocation."""
        transcript = "Executing /do-pr-review 42 for the pull request."
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "REVIEW" and t["status"] == "in_progress" for t in transitions)

    def test_do_docs_invocation(self):
        """Detect /do-docs skill invocation."""
        transcript = "Running /do-docs 42 for documentation cascade."
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "DOCS" and t["status"] == "in_progress" for t in transitions)

    def test_implicit_completion_of_earlier_stages(self):
        """When a later stage starts, earlier stages are implicitly completed."""
        transcript = "Running /do-build docs/plans/feature.md"
        transitions = detect_stages(transcript)

        # ISSUE and PLAN should be implicitly completed
        issue_completed = any(
            t["stage"] == "ISSUE" and t["status"] == "completed" for t in transitions
        )
        plan_completed = any(
            t["stage"] == "PLAN" and t["status"] == "completed" for t in transitions
        )
        assert issue_completed, "ISSUE should be implicitly completed when BUILD starts"
        assert plan_completed, "PLAN should be implicitly completed when BUILD starts"

    def test_multiple_skills_in_transcript(self):
        """Detect multiple skill invocations in a single transcript."""
        transcript = (
            "First, I invoked /do-plan to create the plan.\n"
            "Then I ran /do-build to implement it.\n"
            "Finally, /do-test to validate."
        )
        transitions = detect_stages(transcript)
        stages_found = {t["stage"] for t in transitions}
        assert "PLAN" in stages_found
        assert "BUILD" in stages_found
        assert "TEST" in stages_found

    def test_completion_marker_test_results(self):
        """Detect test completion from test result patterns."""
        transcript = "All 42 tests passed, 0 failed."
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "TEST" and t["status"] == "completed" for t in transitions)

    def test_completion_marker_pr_created(self):
        """Detect BUILD completion from PR creation patterns."""
        transcript = "PR created: https://github.com/foo/bar/pull/123"
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "BUILD" and t["status"] == "completed" for t in transitions)

    def test_completion_marker_issue_created(self):
        """Detect ISSUE completion from issue creation patterns."""
        transcript = "Issue created: https://github.com/foo/bar/issues/456"
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "ISSUE" and t["status"] == "completed" for t in transitions)

    def test_completion_marker_docs_created(self):
        """Detect DOCS completion from documentation patterns."""
        transcript = "Documentation created at docs/features/observer-agent.md"
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "DOCS" and t["status"] == "completed" for t in transitions)

    def test_no_false_positives_on_normal_text(self):
        """Normal conversation text should not trigger stage detection."""
        transcript = "I analyzed the codebase and found that the function works correctly."
        transitions = detect_stages(transcript)
        assert len(transitions) == 0

    def test_skill_in_quoted_text_still_detected(self):
        """Skills in transcript output are detected."""
        transcript = 'The agent ran /do-build "docs/plans/feature.md"'
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "BUILD" for t in transitions)

    def test_transitions_sorted_by_pipeline_order(self):
        """Transitions should be sorted by pipeline stage order."""
        transcript = (
            "First /do-test for testing.\nThen /do-build for implementation.\n"  # Out of order
        )
        transitions = detect_stages(transcript)
        stage_indices = []
        for t in transitions:
            if t["stage"] in STAGE_ORDER:
                stage_indices.append(STAGE_ORDER.index(t["stage"]))
        # Should be sorted ascending
        assert stage_indices == sorted(stage_indices)

    def test_no_duplicate_stages(self):
        """Each stage should appear at most once in transitions."""
        transcript = (
            "Running /do-build docs/plans/a.md\n"
            "Also running /do-build docs/plans/b.md\n"  # Duplicate
        )
        transitions = detect_stages(transcript)
        stages = [t["stage"] for t in transitions]
        # BUILD should only appear once (in_progress), plus implicit completions
        build_count = stages.count("BUILD")
        assert build_count == 1


class TestDetectStagesEdgeCases:
    """Edge cases for the stage detector."""

    def test_whitespace_only_transcript(self):
        """Whitespace-only transcript returns no transitions."""
        assert detect_stages("   \n\n  ") == []

    def test_very_long_transcript(self):
        """Stage detector handles very long transcripts."""
        long_text = "Normal text. " * 10000
        long_text += "\nRunning /do-build docs/plans/feature.md\n"
        transitions = detect_stages(long_text)
        assert any(t["stage"] == "BUILD" for t in transitions)

    def test_skill_at_start_of_transcript(self):
        """Skill invocation at the very start of transcript."""
        transcript = "/do-plan observer-agent"
        transitions = detect_stages(transcript)
        assert any(t["stage"] == "PLAN" for t in transitions)


# ============================================================================
# Observer Decision Tests (unit tests with mocked session)
# ============================================================================


class TestObserverToolHandlers:
    """Test Observer tool handler methods directly."""

    def _make_mock_session(self, **kwargs):
        """Create a minimal mock session for testing Observer tools."""

        class MockSession:
            session_id = kwargs.get("session_id", "test-session-123")
            classification_type = kwargs.get("classification_type", "sdlc")
            context_summary = kwargs.get("context_summary", None)
            expectations = kwargs.get("expectations", None)
            issue_url = kwargs.get("issue_url", None)
            plan_url = kwargs.get("plan_url", None)
            pr_url = kwargs.get("pr_url", None)
            queued_steering_messages = kwargs.get("queued_steering_messages", [])
            history = kwargs.get("history", [])

            def get_stage_progress(self):
                return kwargs.get(
                    "stage_progress",
                    {
                        "ISSUE": "completed",
                        "PLAN": "completed",
                        "BUILD": "in_progress",
                        "TEST": "pending",
                        "REVIEW": "pending",
                        "DOCS": "pending",
                    },
                )

            def get_links(self):
                links = {}
                if self.issue_url:
                    links["issue"] = self.issue_url
                if self.plan_url:
                    links["plan"] = self.plan_url
                if self.pr_url:
                    links["pr"] = self.pr_url
                return links

            def get_history_list(self):
                return self.history if isinstance(self.history, list) else []

            _get_history_list = get_history_list

            def is_sdlc_job(self):
                return self.classification_type == "sdlc"

            def has_remaining_stages(self):
                progress = self.get_stage_progress()
                return any(s in ("pending", "in_progress") for s in progress.values())

            def has_failed_stage(self):
                progress = self.get_stage_progress()
                return any(s == "failed" for s in progress.values())

            def set_link(self, kind, url):
                if kind == "issue":
                    self.issue_url = url
                elif kind == "pr":
                    self.pr_url = url

            def save(self):
                pass

        return MockSession()

    def test_read_session_returns_state(self):
        """read_session tool returns comprehensive session state."""
        session = self._make_mock_session(
            issue_url="https://github.com/foo/bar/issues/1",
            history=["[stage] ISSUE COMPLETED", "[stage] PLAN COMPLETED"],
        )
        observer = Observer(
            session=session,
            worker_output="Building the feature...",
            auto_continue_count=2,
            send_cb=None,
            enqueue_fn=None,
        )
        result = observer._handle_read_session()

        assert result["session_id"] == "test-session-123"
        assert result["is_sdlc"] is True
        assert result["auto_continue_count"] == 2
        assert result["has_remaining_stages"] is True
        assert "BUILD" in result["stage_progress"]

    def test_read_session_with_queued_messages(self):
        """read_session includes queued steering messages."""
        session = self._make_mock_session(
            queued_steering_messages=["Approve the plan", "Continue with tests"]
        )
        observer = Observer(
            session=session,
            worker_output="Working...",
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )
        result = observer._handle_read_session()
        assert result["queued_steering_messages"] == ["Approve the plan", "Continue with tests"]

    def test_update_session_sets_fields(self):
        """update_session persists context_summary and expectations."""
        session = self._make_mock_session()
        observer = Observer(
            session=session,
            worker_output="Done",
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )
        result = observer._handle_update_session(
            context_summary="Building observer agent feature",
            expectations="Waiting for test results",
        )
        assert result["status"] == "ok"
        assert "context_summary" in result["updated_fields"]
        assert "expectations" in result["updated_fields"]
        assert session.context_summary == "Building observer agent feature"

    def test_dispatch_enqueue_continuation(self):
        """enqueue_continuation tool sets decision state."""
        session = self._make_mock_session()
        observer = Observer(
            session=session,
            worker_output="Status update",
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )
        result = observer._dispatch_tool(
            "enqueue_continuation",
            {"coaching_message": "Invoke /do-test next"},
        )
        data = json.loads(result)
        assert data["action"] == "enqueue_continuation"
        assert observer._decision_made is True
        assert observer._action_taken == "steer"

    def test_dispatch_deliver_to_telegram(self):
        """deliver_to_telegram tool sets decision state."""
        session = self._make_mock_session()
        observer = Observer(
            session=session,
            worker_output="All done!",
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )
        result = observer._dispatch_tool(
            "deliver_to_telegram",
            {"reason": "All stages complete"},
        )
        data = json.loads(result)
        assert data["action"] == "deliver_to_telegram"
        assert observer._decision_made is True
        assert observer._action_taken == "deliver"


class TestStageDetectorApplyTransitions:
    """Test applying stage transitions to a mock session."""

    def _make_mock_session(self, history=None):
        """Create a mock session that tracks history appends."""

        class MockSession:
            def __init__(self):
                self.history = history or []
                self._appended = []

            def get_stage_progress(self):
                # Parse history entries like the real implementation
                progress = {stage: "pending" for stage in SDLC_STAGES}
                for entry in self.history:
                    if not isinstance(entry, str) or "[stage]" not in entry.lower():
                        continue
                    entry_upper = entry.upper()
                    for stage in SDLC_STAGES:
                        if stage in entry_upper:
                            if "COMPLETED" in entry_upper:
                                progress[stage] = "completed"
                            elif "IN_PROGRESS" in entry_upper:
                                progress[stage] = "in_progress"
                return progress

            def append_history(self, role, text):
                entry = f"[{role}] {text}"
                self.history.append(entry)
                self._appended.append((role, text))

        return MockSession()

    def test_apply_transitions_to_clean_session(self):
        """Apply transitions to a session with no prior stage data."""
        session = self._make_mock_session()
        transitions = [
            {"stage": "BUILD", "status": "in_progress", "reason": "Skill /do-build invoked"},
            {"stage": "ISSUE", "status": "completed", "reason": "Implicitly completed"},
            {"stage": "PLAN", "status": "completed", "reason": "Implicitly completed"},
        ]
        applied = apply_transitions(session, transitions)
        assert applied == 3
        assert len(session._appended) == 3

    def test_skip_already_completed_stages(self):
        """Don't re-apply transitions for already completed stages."""
        session = self._make_mock_session(
            history=["[stage] PLAN COMPLETED", "[stage] ISSUE COMPLETED"]
        )
        transitions = [
            {"stage": "PLAN", "status": "completed", "reason": "Already done"},
            {"stage": "BUILD", "status": "in_progress", "reason": "New"},
        ]
        applied = apply_transitions(session, transitions)
        assert applied == 1  # Only BUILD should be applied

    def test_empty_transitions(self):
        """Applying empty transitions is a no-op."""
        session = self._make_mock_session()
        applied = apply_transitions(session, [])
        assert applied == 0

    def test_none_session(self):
        """Applying transitions to None session returns 0."""
        from bridge.stage_detector import apply_transitions

        transition = {"stage": "BUILD", "status": "in_progress", "reason": "test"}
        applied = apply_transitions(None, [transition])
        assert applied == 0


# ============================================================================
# AgentSession queued_steering_messages tests
# ============================================================================


class TestQueuedSteeringMessages:
    """Test the queued_steering_messages field on AgentSession."""

    def test_push_and_pop(self):
        """Push messages and pop them all."""

        # Use a mock to avoid Redis dependency
        class MockSession:
            queued_steering_messages = None

            def save(self):
                pass

        session = MockSession()

        # Simulate push
        current = session.queued_steering_messages
        if not isinstance(current, list):
            current = []
        current.append("First message")
        session.queued_steering_messages = current

        current = session.queued_steering_messages
        if not isinstance(current, list):
            current = []
        current.append("Second message")
        session.queued_steering_messages = current

        # Simulate pop
        queued = session.queued_steering_messages
        messages = list(queued) if queued else []
        session.queued_steering_messages = []

        assert messages == ["First message", "Second message"]
        assert session.queued_steering_messages == []

    def test_pop_empty_returns_empty_list(self):
        """Popping from empty queue returns empty list."""

        class MockSession:
            queued_steering_messages = []

        session = MockSession()
        current = session.queued_steering_messages
        if not isinstance(current, list) or not current:
            messages = []
        else:
            messages = list(current)
        assert messages == []

    def test_pop_none_returns_empty_list(self):
        """Popping from None queue returns empty list."""

        class MockSession:
            queued_steering_messages = None

        session = MockSession()
        current = session.queued_steering_messages
        if not isinstance(current, list) or not current:
            messages = []
        else:
            messages = list(current)
        assert messages == []


# ============================================================================
# Observer fallback behavior tests
# ============================================================================


class TestObserverFallback:
    """Test Observer fallback behavior when errors occur."""

    @pytest.mark.asyncio
    async def test_observer_error_returns_deliver(self):
        """When Observer errors, it returns a deliver decision."""

        class MockSession:
            session_id = "test-fallback"
            classification_type = "sdlc"
            context_summary = None
            expectations = None
            queued_steering_messages = []
            history = []

            def get_stage_progress(self):
                return {
                    "ISSUE": "pending",
                    "PLAN": "pending",
                    "BUILD": "pending",
                    "TEST": "pending",
                    "REVIEW": "pending",
                    "DOCS": "pending",
                }

            def get_history_list(self):
                return []

            _get_history_list = get_history_list

            def is_sdlc_job(self):
                return True

            def has_remaining_stages(self):
                return True

            def has_failed_stage(self):
                return False

            def append_history(self, role, text):
                pass

        # Create Observer with no valid API key path — it will fail on API call
        observer = Observer(
            session=MockSession(),
            worker_output="Test output",
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )

        # Monkeypatch to simulate API failure
        original_fn = None
        try:
            # Save and replace the API key getter
            import utils.api_keys

            original_fn = utils.api_keys.get_anthropic_api_key
            utils.api_keys.get_anthropic_api_key = lambda: None

            decision = await observer.run()
            assert decision["action"] == "deliver"
            assert "No API key" in decision.get("reason", "")
        finally:
            if original_fn:
                utils.api_keys.get_anthropic_api_key = original_fn


# ============================================================================
# Observer Steering Integration Tests (real API calls)
#
# These tests validate that the Observer correctly steers the worker when
# it pauses with a status update mid-pipeline, rather than delivering to
# the human. Each test simulates a realistic "I'm going to do X" output
# at a specific SDLC stage.
#
# The real-life failure mode: the agent explains its plan, stops, and the
# old classifier labeled it a status update. The Observer must recognize
# this as incomplete work and steer the agent back.
#
# Exception: if the output contains genuine Open Questions for the architect
# or project manager, the Observer should deliver so a human can decide.
# ============================================================================


class _MockSessionForSteering:
    """Reusable mock session for steering integration tests."""

    def __init__(self, stage_progress, history=None, classification_type="sdlc"):
        self.session_id = "test-steering-integration"
        self.classification_type = classification_type
        self.context_summary = None
        self.expectations = None
        self.issue_url = None
        self.plan_url = None
        self.pr_url = None
        self.queued_steering_messages = []
        self.history = history or []
        self._stage_progress = stage_progress
        self._history_appended = []

    def get_stage_progress(self):
        return dict(self._stage_progress)

    def get_links(self):
        links = {}
        if self.issue_url:
            links["issue"] = self.issue_url
        if self.plan_url:
            links["plan"] = self.plan_url
        if self.pr_url:
            links["pr"] = self.pr_url
        return links

    def get_history_list(self):
        return self.history if isinstance(self.history, list) else []

    _get_history_list = get_history_list

    def is_sdlc_job(self):
        return self.classification_type == "sdlc"

    def has_remaining_stages(self):
        return any(s in ("pending", "in_progress") for s in self._stage_progress.values())

    def has_failed_stage(self):
        return any(s == "failed" for s in self._stage_progress.values())

    def set_link(self, kind, url):
        if kind == "issue":
            self.issue_url = url
        elif kind == "pr":
            self.pr_url = url

    def pop_steering_messages(self):
        msgs = list(self.queued_steering_messages)
        self.queued_steering_messages = []
        return msgs

    def append_history(self, role, text):
        entry = f"[{role}] {text}"
        self.history.append(entry)
        self._history_appended.append((role, text))

    def save(self):
        pass


# Status-update outputs that mimic real agent behavior: the agent explains
# what it's about to do, then stops. These are drawn from real production logs.
_STATUS_UPDATE_OUTPUTS = {
    "PLAN": (
        "✅\n"
        "• Analyzing the codebase structure and existing patterns\n"
        "• Will create a comprehensive plan document at docs/plans/observer-agent.md\n"
        "• Will define the migration path from classifier to Observer Agent\n"
        "• Need to review the existing summarizer and coaching message code first"
    ),
    "BUILD": (
        "✅\n"
        "• Reviewing PR #286 post-merge recommendations from Claude\n"
        "• Will address model naming inconsistency in get_gpt_4_mini_model()\n"
        "• Will add data retention documentation for AICompletion records"
    ),
    "PATCH": (
        "✅\n"
        "• Identified 3 failing tests in test_observer.py\n"
        "• Root cause: mock session missing pop_steering_messages method\n"
        "• Will fix the mock and re-run the test suite"
    ),
    "TEST": (
        "✅\n"
        "• Setting up the test environment\n"
        "• Will run the full test suite with pytest\n"
        "• Will focus on integration tests for the new Observer module\n"
        "• Plan to verify all edge cases around stage detection"
    ),
    "REVIEW": (
        "✅\n"
        "• Checking out PR #321 for review\n"
        "• Will analyze code changes against the plan requirements\n"
        "• Will take screenshots of any UI changes for validation\n"
        "• Will verify test coverage meets quality gates"
    ),
    "DOCS": (
        "✅\n"
        "• Reviewing the code changes from PR #321\n"
        "• Will create docs/features/observer-agent.md\n"
        "• Will update docs/features/README.md index table\n"
        "• Will cascade updates to any affected existing docs"
    ),
}

# Stage progress states: what the session looks like when each stage is active
_STAGE_PROGRESS_FOR = {
    "PLAN": {
        "ISSUE": "completed",
        "PLAN": "in_progress",
        "BUILD": "pending",
        "TEST": "pending",
        "REVIEW": "pending",
        "DOCS": "pending",
    },
    "BUILD": {
        "ISSUE": "completed",
        "PLAN": "completed",
        "BUILD": "in_progress",
        "TEST": "pending",
        "REVIEW": "pending",
        "DOCS": "pending",
    },
    "PATCH": {  # Patch happens during BUILD or TEST — stages still remain
        "ISSUE": "completed",
        "PLAN": "completed",
        "BUILD": "in_progress",
        "TEST": "pending",
        "REVIEW": "pending",
        "DOCS": "pending",
    },
    "TEST": {
        "ISSUE": "completed",
        "PLAN": "completed",
        "BUILD": "completed",
        "TEST": "in_progress",
        "REVIEW": "pending",
        "DOCS": "pending",
    },
    "REVIEW": {
        "ISSUE": "completed",
        "PLAN": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "in_progress",
        "DOCS": "pending",
    },
    "DOCS": {
        "ISSUE": "completed",
        "PLAN": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": "in_progress",
    },
}


class TestObserverSteersStatusUpdates:
    """Observer must STEER (not deliver) when the worker pauses with a
    status update mid-pipeline. Each test simulates a real-world scenario
    where the agent explains its plan and stops before executing.

    Uses real Anthropic API calls — no mocked LLM responses.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stage", ["PLAN", "BUILD", "PATCH", "TEST", "REVIEW", "DOCS"])
    async def test_status_update_steered_not_delivered(self, stage):
        """Status update at each SDLC stage should be steered, not delivered."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR[stage],
            history=[f"[stage] {s} COMPLETED" for s in ["ISSUE", "PLAN"] if
                     _STAGE_PROGRESS_FOR[stage].get(s) == "completed"],
        )
        observer = Observer(
            session=session,
            worker_output=_STATUS_UPDATE_OUTPUTS[stage],
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )

        decision = await observer.run()

        assert decision["action"] == "steer", (
            f"Observer should STEER at {stage} stage with status update, "
            f"but got {decision['action']}: {decision.get('reason', decision.get('coaching_message', ''))}"
        )
        # Coaching message should exist and be substantive
        coaching = decision.get("coaching_message", "")
        assert len(coaching) > 10, (
            f"Coaching message too short at {stage} stage: {coaching!r}"
        )

    @pytest.mark.asyncio
    async def test_status_update_with_open_questions_delivered(self):
        """When the output contains genuine open questions for the architect
        or project manager, the Observer should DELIVER so a human can decide."""
        output_with_questions = (
            "✅\n"
            "• Analyzed the codebase and identified the migration path\n"
            "• Created initial plan structure at docs/plans/observer-agent.md\n\n"
            "## Open Questions\n\n"
            "1. **Architecture decision needed**: Should the Observer Agent run "
            "as a separate process or inline within send_to_chat()? Running "
            "inline is simpler but adds latency to every message delivery.\n\n"
            "2. **Scope question for Tom**: Should we deprecate the old classifier "
            "immediately or run both systems in shadow mode for a week to compare "
            "routing decisions?\n\n"
            "3. **Resource concern**: The Observer makes an additional API call per "
            "message. At current volume (~200 msgs/day), this adds ~$12/month. "
            "Is that acceptable?"
        )
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["PLAN"],
        )
        observer = Observer(
            session=session,
            worker_output=output_with_questions,
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )

        decision = await observer.run()

        assert decision["action"] == "deliver", (
            f"Observer should DELIVER when output contains open questions for "
            f"architect/PM, but got {decision['action']}: "
            f"{decision.get('coaching_message', '')}"
        )

    @pytest.mark.asyncio
    async def test_steering_message_encourages_discernment(self):
        """The coaching message should encourage the agent to continue with
        discernment — not just a bare 'continue'. It should give the agent
        permission to raise critical questions while encouraging forward progress."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["BUILD"],
        )
        observer = Observer(
            session=session,
            worker_output=_STATUS_UPDATE_OUTPUTS["BUILD"],
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )

        decision = await observer.run()

        assert decision["action"] == "steer"
        coaching = decision.get("coaching_message", "")

        # The coaching message should NOT be a bare "continue"
        assert coaching.lower().strip() != "continue", (
            "Coaching message should be more substantive than bare 'continue'"
        )
        # Should be at least a meaningful sentence
        assert len(coaching) > 20, (
            f"Coaching message should be a substantive instruction, got: {coaching!r}"
        )
