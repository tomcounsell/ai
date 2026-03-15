"""Tests for the Observer Agent and Stage Detector.

Tests the deterministic stage detector (pure function, no mocks needed)
and the Observer's routing decision framework.
"""

import json

import pytest

from bridge.observer import Observer, _next_sdlc_skill
from bridge.stage_detector import STAGE_ORDER, apply_transitions, detect_stages
from config.models import HAIKU
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
        """When Observer errors, it returns a deliver decision.

        Uses a non-SDLC session so the deterministic SDLC guard (Phase 1.75)
        doesn't intercept before reaching the LLM error path we're testing.
        """

        class MockSession:
            session_id = "test-fallback"
            classification_type = "general"
            context_summary = None
            expectations = None
            queued_steering_messages = []
            history = []

            def get_stage_progress(self):
                return {}

            def get_history_list(self):
                return []

            _get_history_list = get_history_list

            def get_links(self):
                return {}

            def is_sdlc_job(self):
                return False

            def has_remaining_stages(self):
                return False

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
        import bridge.observer as obs_mod

        original_fn = obs_mod.get_anthropic_api_key
        try:
            obs_mod.get_anthropic_api_key = lambda: None

            decision = await observer.run()
            assert decision["action"] == "deliver"
            assert "No API key" in decision.get("reason", "")
        finally:
            obs_mod.get_anthropic_api_key = original_fn


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

    Uses real Anthropic API calls with Haiku — if decisions are correct at
    lower intelligence, they'll be even more robust with Sonnet in production.
    """

    # All integration tests use Haiku as a floor test for decision quality.
    # Production runs Sonnet for better nuance with real-world edge cases.
    MODEL = HAIKU

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stage", ["PLAN", "BUILD", "PATCH", "TEST", "REVIEW", "DOCS"])
    async def test_status_update_steered_not_delivered(self, stage):
        """Status update at each SDLC stage should be steered, not delivered."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR[stage],
            history=[
                f"[stage] {s} COMPLETED"
                for s in ["ISSUE", "PLAN"]
                if _STAGE_PROGRESS_FOR[stage].get(s) == "completed"
            ],
        )
        observer = Observer(
            session=session,
            worker_output=_STATUS_UPDATE_OUTPUTS[stage],
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "steer", (
            f"Observer should STEER at {stage} stage with status update, "
            f"but got {decision['action']}: "
            f"{decision.get('reason', decision.get('coaching_message', ''))}"
        )
        # Coaching message should exist and be substantive
        coaching = decision.get("coaching_message", "")
        assert len(coaching) > 10, f"Coaching message too short at {stage} stage: {coaching!r}"

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
            model=self.MODEL,
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
            model=self.MODEL,
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

    @pytest.mark.asyncio
    async def test_max_auto_continues_cap_causes_delivery(self):
        """When auto_continue_count hits the SDLC cap (10), the Observer
        should deliver to the human even if stages remain. This prevents
        infinite loops where the agent keeps producing status updates."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["BUILD"],
        )
        observer = Observer(
            session=session,
            worker_output=_STATUS_UPDATE_OUTPUTS["BUILD"],
            auto_continue_count=10,  # At the cap
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "deliver", (
            f"Observer should DELIVER when auto-continue cap (10) is reached, "
            f"but got {decision['action']}: "
            f"{decision.get('coaching_message', '')}"
        )

    @pytest.mark.asyncio
    async def test_non_sdlc_cap_of_3_causes_delivery(self):
        """Non-SDLC jobs have a lower cap of 3. After 3 auto-continues,
        even planning-language outputs should be delivered."""
        session = _MockSessionForSteering(
            stage_progress={s: "pending" for s in SDLC_STAGES},
            classification_type="conversation",  # Non-SDLC
        )
        observer = Observer(
            session=session,
            worker_output=(
                "✅\n"
                "• I'll research the best approach for this\n"
                "• Will compare three different libraries\n"
                "• Need to check compatibility with the existing stack"
            ),
            auto_continue_count=3,  # At the non-SDLC cap
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "deliver", (
            f"Observer should DELIVER when non-SDLC cap (3) is reached, "
            f"but got {decision['action']}: "
            f"{decision.get('coaching_message', '')}"
        )

    @pytest.mark.asyncio
    async def test_genuine_question_delivered_mid_build(self):
        """A genuine question mid-build (not open questions section, but
        the agent directly asking for a decision) should be delivered."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["BUILD"],
        )
        observer = Observer(
            session=session,
            worker_output=(
                "I've implemented the Observer Agent but I need a decision "
                "before proceeding.\n\n"
                "The current test suite takes 55 seconds because each test "
                "makes a real API call to Sonnet. Should I:\n\n"
                "A) Keep the real API calls for maximum confidence\n"
                "B) Add a mock mode that uses cached responses for CI speed\n"
                "C) Split into a fast unit suite and a slow integration suite\n\n"
                "This affects the CI pipeline design, so I'd rather get your "
                "input than guess."
            ),
            auto_continue_count=2,
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "deliver", (
            f"Observer should DELIVER when agent asks a genuine decision "
            f"question, but got {decision['action']}: "
            f"{decision.get('coaching_message', '')}"
        )

    @pytest.mark.asyncio
    async def test_error_output_delivered(self):
        """When the worker hits an error it can't recover from, the Observer
        should deliver to the human for intervention."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["TEST"],
        )
        observer = Observer(
            session=session,
            worker_output=(
                "FATAL: The Anthropic API key has been revoked.\n\n"
                "```\nauthentication_error: Your API key has been disabled. "
                "Please contact support@anthropic.com.\n```\n\n"
                "I cannot proceed with any API-dependent work. This requires "
                "a human to generate a new API key in the Anthropic console "
                "and update the .env file. There is nothing I can do to fix "
                "this programmatically."
            ),
            auto_continue_count=1,
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "deliver", (
            f"Observer should DELIVER on unrecoverable errors, "
            f"but got {decision['action']}: "
            f"{decision.get('coaching_message', '')}"
        )

    @pytest.mark.asyncio
    async def test_completion_with_evidence_delivered(self):
        """When all stages are done and the output shows completion evidence,
        the Observer should deliver the final result."""
        session = _MockSessionForSteering(
            stage_progress={s: "completed" for s in SDLC_STAGES},
        )
        observer = Observer(
            session=session,
            worker_output=(
                "All SDLC stages complete:\n\n"
                "- Issue: #309\n"
                "- Plan: docs/plans/observer_agent.md\n"
                "- PR: https://github.com/tomcounsell/ai/pull/321\n"
                "- Tests: 41 passed, 0 failed\n"
                "- Review: Approved\n"
                "- Docs: docs/features/observer-agent.md created\n\n"
                "The Observer Agent is ready to merge."
            ),
            auto_continue_count=5,
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "deliver", (
            f"Observer should DELIVER when all stages complete with evidence, "
            f"but got {decision['action']}: "
            f"{decision.get('coaching_message', '')}"
        )


# ============================================================================
# Typed Outcome Merge Tests (Bug 3 regression tests)
# ============================================================================


class TestApplyTransitionsTypedOutcomeMerge:
    """Test that apply_transitions merges typed outcomes when regex misses stages.

    Regression tests for issue #375 Bug 3: when a typed SkillOutcome reports
    success but the regex didn't detect the stage completion, the stage should
    be recorded in session history (not just warned about).
    """

    def _make_mock_session(self, history=None):
        """Create a mock session that tracks history appends."""

        class MockSession:
            def __init__(self):
                self.history = history or []
                self._appended = []

            def get_stage_progress(self):
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

    def _make_outcome(self, status="success", stage="DOCS"):
        """Create a SkillOutcome for testing."""
        from agent.skill_outcome import SkillOutcome

        return SkillOutcome(status=status, stage=stage)

    def test_typed_outcome_merged_when_regex_misses(self):
        """When regex detects nothing but typed outcome says DOCS succeeded,
        apply_transitions should record DOCS COMPLETED in session history."""
        session = self._make_mock_session()
        outcome = self._make_outcome(status="success", stage="DOCS")
        result = apply_transitions(session, [], outcome=outcome)
        assert result == 1
        assert any("DOCS COMPLETED" in entry for entry in session.history)

    def test_typed_outcome_not_merged_on_failure(self):
        """When typed outcome says stage failed, it should NOT be merged."""
        session = self._make_mock_session()
        outcome = self._make_outcome(status="fail", stage="DOCS")
        result = apply_transitions(session, [], outcome=outcome)
        assert result == 0
        assert not any("DOCS COMPLETED" in entry for entry in session.history)

    def test_typed_outcome_skipped_when_regex_already_detected(self):
        """When regex already detected the stage, typed outcome should not
        create a duplicate entry."""
        session = self._make_mock_session()
        transitions = [
            {"stage": "DOCS", "status": "completed", "reason": "Regex detected"},
        ]
        outcome = self._make_outcome(status="success", stage="DOCS")
        result = apply_transitions(session, transitions, outcome=outcome)
        assert result == 1  # Only the regex transition should be applied
        docs_entries = [e for e in session._appended if "DOCS" in e[1]]
        assert len(docs_entries) == 1  # No duplicate

    def test_typed_outcome_skipped_when_stage_already_completed(self):
        """When the stage is already completed in session history, typed outcome
        should not re-record it."""
        session = self._make_mock_session(history=["[stage] DOCS COMPLETED"])
        outcome = self._make_outcome(status="success", stage="DOCS")
        result = apply_transitions(session, [], outcome=outcome)
        assert result == 0  # Already completed, skip
        assert len(session._appended) == 0

    def test_typed_outcome_none_stage_no_crash(self):
        """When typed outcome has stage=None, apply_transitions should not crash."""
        session = self._make_mock_session()
        from agent.skill_outcome import SkillOutcome

        outcome = SkillOutcome(status="success", stage=None)
        result = apply_transitions(session, [], outcome=outcome)
        assert result == 0

    def test_typed_outcome_with_regex_transitions_both_recorded(self):
        """When regex detects BUILD and typed outcome reports DOCS, both should
        be recorded in session history."""
        session = self._make_mock_session()
        transitions = [
            {"stage": "BUILD", "status": "completed", "reason": "PR created"},
        ]
        outcome = self._make_outcome(status="success", stage="DOCS")
        result = apply_transitions(session, transitions, outcome=outcome)
        assert result == 2
        assert any("BUILD COMPLETED" in entry for entry in session.history)
        assert any("DOCS COMPLETED" in entry for entry in session.history)


# ============================================================================
# Observer SDLC Steering Tests
# ============================================================================


class TestObserverSdlcSteering:
    """Test Observer steering decisions for SDLC sessions."""

    MODEL = HAIKU

    @pytest.mark.asyncio
    async def test_observer_steers_when_is_sdlc_and_remaining_stages(self):
        """When session is SDLC with remaining stages, Observer should steer
        (continue) rather than deliver."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["BUILD"],
        )
        observer = Observer(
            session=session,
            worker_output=(
                "Build completed successfully. PR created at "
                "https://github.com/tomcounsell/ai/pull/42\n\n"
                "All implementation tasks done. Ready for next stage."
            ),
            auto_continue_count=1,
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "steer", (
            f"Observer should STEER when SDLC has remaining stages, but got {decision['action']}"
        )

    @pytest.mark.asyncio
    async def test_observer_delivers_when_all_stages_complete(self):
        """When all SDLC stages are complete, Observer should deliver."""
        session = _MockSessionForSteering(
            stage_progress={s: "completed" for s in SDLC_STAGES},
        )
        observer = Observer(
            session=session,
            worker_output=(
                "All stages complete. PR merged successfully.\n\n"
                "Summary of work:\n"
                "- Issue: #375\n"
                "- PR: https://github.com/tomcounsell/ai/pull/380\n"
                "- All tests passing\n"
                "- Documentation updated"
            ),
            auto_continue_count=5,
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "deliver", (
            f"Observer should DELIVER when all stages complete, but got {decision['action']}"
        )

    @pytest.mark.asyncio
    async def test_guard_bypassed_when_stage_has_failed(self):
        """Deterministic guard must NOT force-steer when a stage has failed.
        Failed stages need human attention."""
        stage_progress = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "failed",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        session = _MockSessionForSteering(stage_progress=stage_progress)
        observer = Observer(
            session=session,
            worker_output="Build failed: tests are broken, cannot proceed.",
            auto_continue_count=1,
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        # Should NOT be a deterministic guard steer — must fall through to LLM
        # which should deliver the failure to human
        assert decision.get("deterministic_guard") is not True, (
            "Deterministic guard should NOT fire when a stage has failed"
        )

    @pytest.mark.asyncio
    async def test_guard_bypassed_when_stop_reason_is_fail(self):
        """Deterministic guard must NOT force-steer when stop_reason is 'fail'."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["BUILD"],
        )
        observer = Observer(
            session=session,
            worker_output="Worker encountered an unrecoverable error.",
            auto_continue_count=1,
            send_cb=None,
            enqueue_fn=None,
            stop_reason="fail",
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision.get("deterministic_guard") is not True, (
            "Deterministic guard should NOT fire when stop_reason is 'fail'"
        )

    @pytest.mark.asyncio
    async def test_guard_bypassed_when_stop_reason_is_budget_exceeded(self):
        """Deterministic guard must NOT force-steer when stop_reason is 'budget_exceeded'.
        budget_exceeded is caught earlier at Phase 1.5, but the guard should also
        not fire as defense-in-depth."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["BUILD"],
        )
        observer = Observer(
            session=session,
            worker_output="Partial work done before budget ran out.",
            auto_continue_count=1,
            send_cb=None,
            enqueue_fn=None,
            stop_reason="budget_exceeded",
            model=self.MODEL,
        )

        decision = await observer.run()

        # budget_exceeded is caught at Phase 1.5 (before Phase 1.75),
        # so it delivers directly. Either way, the guard should not fire.
        assert decision.get("deterministic_guard") is not True, (
            "Deterministic guard should NOT fire when stop_reason is 'budget_exceeded'"
        )

    @pytest.mark.asyncio
    async def test_guard_fires_on_normal_sdlc_with_remaining(self):
        """Deterministic guard MUST fire for normal SDLC sessions with remaining
        stages and no failures."""
        session = _MockSessionForSteering(
            stage_progress=_STAGE_PROGRESS_FOR["BUILD"],
        )
        observer = Observer(
            session=session,
            worker_output="Build done. Moving on.",
            auto_continue_count=1,
            send_cb=None,
            enqueue_fn=None,
            model=self.MODEL,
        )

        decision = await observer.run()

        assert decision["action"] == "steer", (
            f"Guard should steer on normal SDLC with remaining stages, got {decision['action']}"
        )
        assert decision.get("deterministic_guard") is True, (
            "Decision should be marked as deterministic_guard"
        )


# ============================================================================
# Typed Outcome Graph Routing Tests (issue #414)
# ============================================================================


class TestTypedOutcomeGraphRouting:
    """Test that the Observer resolves next_skill from the pipeline graph
    when outcome.next_skill is None (regression test for issue #414)."""

    def _make_session(self, stage_progress, pr_url=None):
        """Create a mock session with stage progress and has_remaining_stages."""

        class MockSession:
            def __init__(self):
                self.session_id = "test-session"
                self.correlation_id = "test-cid"
                self.pr_url = pr_url
                self._stage_progress = stage_progress
                self._history = []

            def get_stage_progress(self):
                return dict(self._stage_progress)

            def has_remaining_stages(self):
                return any(v in ("pending", "in_progress") for v in self._stage_progress.values())

            def get_history_list(self):
                return self._history

            def save(self):
                pass

        return MockSession()

    def test_next_skill_none_resolves_from_graph(self):
        """When outcome.next_skill is None and BUILD just completed,
        the Observer should resolve /do-test from the pipeline graph."""
        from agent.skill_outcome import SkillOutcome

        session = self._make_session(
            stage_progress={
                "ISSUE": "completed",
                "PLAN": "completed",
                "BUILD": "completed",
                "TEST": "pending",
                "REVIEW": "pending",
                "DOCS": "pending",
            }
        )

        # Simulate what the Observer does at line 557-566
        outcome = SkillOutcome(
            status="success",
            stage="BUILD",
            notes="PR created",
            next_skill=None,
        )

        # This is the exact logic from the fix
        if outcome.next_skill:
            next_skill = outcome.next_skill
        else:
            next_info = _next_sdlc_skill(session)
            next_skill = next_info[1] if next_info else "the next pipeline stage"

        assert next_skill == "/do-test", f"Expected /do-test, got {next_skill}"

    def test_next_skill_explicit_used_directly(self):
        """When outcome.next_skill is explicitly set, it should be used directly."""
        from agent.skill_outcome import SkillOutcome

        session = self._make_session(
            stage_progress={
                "ISSUE": "completed",
                "PLAN": "completed",
                "BUILD": "completed",
                "TEST": "pending",
                "REVIEW": "pending",
                "DOCS": "pending",
            }
        )

        outcome = SkillOutcome(
            status="success",
            stage="BUILD",
            notes="PR created",
            next_skill="/do-custom",
        )

        if outcome.next_skill:
            next_skill = outcome.next_skill
        else:
            next_info = _next_sdlc_skill(session)
            next_skill = next_info[1] if next_info else "the next pipeline stage"

        assert next_skill == "/do-custom", f"Expected /do-custom, got {next_skill}"

    def test_next_skill_none_all_complete_uses_fallback(self):
        """When _next_sdlc_skill returns None (all stages done), use fallback string."""
        from agent.skill_outcome import SkillOutcome

        session = self._make_session(
            stage_progress={
                "ISSUE": "completed",
                "PLAN": "completed",
                "BUILD": "completed",
                "TEST": "completed",
                "REVIEW": "completed",
                "DOCS": "completed",
            }
        )

        outcome = SkillOutcome(
            status="success",
            stage="DOCS",
            notes="Docs done",
            next_skill=None,
        )

        if outcome.next_skill:
            next_skill = outcome.next_skill
        else:
            next_info = _next_sdlc_skill(session)
            next_skill = next_info[1] if next_info else "the next pipeline stage"

        assert next_skill == "the next pipeline stage"


# ============================================================================
# _next_sdlc_skill PR Guard Tests
# ============================================================================


class TestNextSdlcSkillPrGuard:
    """Test that _next_sdlc_skill routes to BUILD when REVIEW is next but no PR exists."""

    def _make_session(self, stage_progress, pr_url=None):
        """Create a minimal mock session."""

        class MockSession:
            pass

        session = MockSession()
        session.pr_url = pr_url
        session._stage_progress = stage_progress

        def get_stage_progress():
            return dict(session._stage_progress)

        session.get_stage_progress = get_stage_progress
        return session

    def test_review_pending_no_pr_routes_to_build(self):
        """When REVIEW is pending and no pr_url, route to BUILD."""
        session = self._make_session(
            stage_progress={
                "ISSUE": "completed",
                "PLAN": "completed",
                "BUILD": "completed",
                "TEST": "completed",
                "REVIEW": "pending",
                "DOCS": "pending",
            },
            pr_url=None,
        )
        stage, skill = _next_sdlc_skill(session)
        assert stage == "BUILD", f"Expected BUILD, got {stage}"
        assert skill == "/do-build"

    def test_review_pending_with_pr_routes_to_review(self):
        """When REVIEW is pending and pr_url exists, route to REVIEW."""
        session = self._make_session(
            stage_progress={
                "ISSUE": "completed",
                "PLAN": "completed",
                "BUILD": "completed",
                "TEST": "completed",
                "REVIEW": "pending",
                "DOCS": "pending",
            },
            pr_url="https://github.com/tomcounsell/ai/pull/42",
        )
        stage, skill = _next_sdlc_skill(session)
        assert stage == "REVIEW", f"Expected REVIEW, got {stage}"
        assert skill == "/do-pr-review"

    def test_review_in_progress_no_pr_continues_review(self):
        """When REVIEW is already in_progress, continue even without pr_url.
        The worker is mid-review — don't redirect."""
        session = self._make_session(
            stage_progress={
                "ISSUE": "completed",
                "PLAN": "completed",
                "BUILD": "completed",
                "TEST": "completed",
                "REVIEW": "in_progress",
                "DOCS": "pending",
            },
            pr_url=None,
        )
        stage, skill = _next_sdlc_skill(session)
        assert stage == "REVIEW", f"Expected REVIEW, got {stage}"
        assert skill == "/do-pr-review"

    def test_non_review_stage_unaffected(self):
        """Guard only applies to REVIEW stage — other stages unaffected."""
        session = self._make_session(
            stage_progress={
                "ISSUE": "completed",
                "PLAN": "completed",
                "BUILD": "pending",
                "TEST": "pending",
                "REVIEW": "pending",
                "DOCS": "pending",
            },
            pr_url=None,
        )
        stage, skill = _next_sdlc_skill(session)
        assert stage == "BUILD", f"Expected BUILD, got {stage}"
        assert skill == "/do-build"
