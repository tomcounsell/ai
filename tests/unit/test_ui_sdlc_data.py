"""Tests for the SDLC data access layer and Pydantic serializers."""

import datetime
import json
import time
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]


def _make_mock_session(**overrides):
    """Create a mock AgentSession with sensible defaults for all fields.

    Supports all fields needed by _session_to_pipeline(), including the new
    metadata fields added for parent/child hierarchy and session visibility.
    """
    defaults = {
        "agent_session_id": "mock-session-1",
        "session_id": "sess-1",
        "session_type": "eng",
        "status": "running",
        "slug": None,
        "message_text": "test message",
        "project_key": None,
        "branch_name": None,
        "created_at": time.time(),
        "started_at": time.time(),
        "completed_at": None,
        "updated_at": time.time(),
        "stage_states": None,
        "history": [],
        "issue_url": None,
        "plan_url": None,
        "pr_url": None,
        "parent_agent_session_id": None,
        "context_summary": None,
        "expectations": None,
        "turn_count": 0,
        "tool_call_count": 0,
        "unhealthy_reason": None,
        "priority": "normal",
        "extra_context": None,
        "thread_first_created_at": None,
        "thread_turn_count": None,
        "thread_tool_call_count": None,
        "thread_run_count": None,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, val in defaults.items():
        setattr(mock, key, val)
    return mock


class TestStageStateParsing:
    """Tests for parsing stage_states into typed StageState objects."""

    def test_parse_none(self):
        from ui.data.sdlc import _parse_stage_states

        result = _parse_stage_states(None)
        assert len(result) == 0  # Non-SDLC sessions get empty stages

    def test_parse_empty_string(self):
        from ui.data.sdlc import _parse_stage_states

        result = _parse_stage_states("")
        assert len(result) == 0  # Non-SDLC sessions get empty stages

    def test_parse_valid_json_string(self):
        from ui.data.sdlc import _parse_stage_states

        data = json.dumps({"ISSUE": "completed", "PLAN": "in_progress", "BUILD": "pending"})
        result = _parse_stage_states(data)
        assert len(result) == 8

        by_name = {s.name: s for s in result}
        assert by_name["ISSUE"].status == "completed"
        assert by_name["PLAN"].status == "in_progress"
        assert by_name["BUILD"].status == "pending"

    def test_parse_dict(self):
        from ui.data.sdlc import _parse_stage_states

        data = {"ISSUE": "completed", "PLAN": "completed", "CRITIQUE": "in_progress"}
        result = _parse_stage_states(data)
        by_name = {s.name: s for s in result}
        assert by_name["CRITIQUE"].is_active
        assert by_name["ISSUE"].is_done

    def test_parse_malformed_json(self):
        from ui.data.sdlc import _parse_stage_states

        result = _parse_stage_states("not valid json{{{")
        assert len(result) == 0  # Malformed JSON treated as no stage data

    def test_parse_nested_status_dict(self):
        """Handle stage_states where values are dicts with a 'status' key."""
        from ui.data.sdlc import _parse_stage_states

        data = {
            "ISSUE": {"status": "completed", "started_at": 123},
            "PLAN": {"status": "in_progress"},
        }
        result = _parse_stage_states(data)
        by_name = {s.name: s for s in result}
        assert by_name["ISSUE"].status == "completed"
        assert by_name["PLAN"].status == "in_progress"

    def test_parse_with_patch_cycle_count_metadata(self):
        """Verify _patch_cycle_count keys are ignored (only SDLC_STAGES iterated)."""
        from ui.data.sdlc import _parse_stage_states

        data = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "_patch_cycle_count": 2,
            "_critique_cycle_count": 1,
        }
        result = _parse_stage_states(data)
        assert len(result) == 8
        names = {s.name for s in result}
        assert "_patch_cycle_count" not in names


class TestStageStateModel:
    """Tests for the StageState Pydantic model."""

    def test_is_active(self):
        from ui.data.sdlc import StageState

        s = StageState(name="BUILD", status="in_progress")
        assert s.is_active
        assert not s.is_done
        assert not s.is_failed

    def test_is_done(self):
        from ui.data.sdlc import StageState

        s = StageState(name="ISSUE", status="completed")
        assert s.is_done
        assert not s.is_active

    def test_is_failed(self):
        from ui.data.sdlc import StageState

        s = StageState(name="TEST", status="failed")
        assert s.is_failed
        assert not s.is_done
        assert not s.is_active

    def test_skipped_is_done(self):
        from ui.data.sdlc import StageState

        s = StageState(name="CRITIQUE", status="skipped")
        assert s.is_done

    def test_is_ready(self):
        from ui.data.sdlc import StageState

        s = StageState(name="BUILD", status="ready")
        assert s.is_ready
        assert not s.is_active
        assert not s.is_done
        assert not s.is_failed


class TestPipelineProgress:
    """Tests for the PipelineProgress Pydantic model."""

    def test_display_name_prefers_slug(self):
        """slug takes priority over context_summary and message_text.

        Slug is the durable, stable identifier tying together branch,
        worktree, plan doc, and GitHub issue — it's the canonical name
        operators use to refer to planned work. It should win even when
        an AI-generated context_summary is also present.
        """
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(
            agent_session_id="123",
            context_summary="Implementing auth flow",
            slug="auth-flow",
            message_text="Build the auth",
        )
        assert p.display_name == "auth-flow"

    def test_display_name_falls_back_to_context_summary(self):
        """context_summary is used when no slug is set (ad-hoc sessions)."""
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(
            agent_session_id="123",
            context_summary="Investigating logging",
            message_text="Tell me about our logging",
        )
        assert p.display_name == "Investigating logging"

    def test_display_name_with_slug(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(agent_session_id="123", slug="my-feature")
        assert p.display_name == "my-feature"

    def test_display_name_with_message(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(
            agent_session_id="123", message_text="Build the web UI for reflections"
        )
        assert p.display_name == "Build the web UI for reflections"

    def test_display_name_truncated(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(agent_session_id="123", message_text="A" * 100)
        assert len(p.display_name) < 100
        assert "..." in p.display_name


class TestExtractGithubLinks:
    """Tests for the history-based GitHub link fallback."""

    def test_extract_from_empty_list(self):
        from ui.data.sdlc import _extract_github_links

        assert _extract_github_links([]) == (None, None)
        assert _extract_github_links(None) == (None, None)

    def test_extract_issue_from_pipeline_event(self):
        from ui.data.sdlc import PipelineEvent, _extract_github_links

        events = [
            PipelineEvent(
                role="lifecycle", text="Created issue https://github.com/org/repo/issues/42"
            ),
        ]
        issue_url, pr_url = _extract_github_links(events)
        assert issue_url == "https://github.com/org/repo/issues/42"
        assert pr_url is None

    def test_extract_pr_from_pipeline_event(self):
        from ui.data.sdlc import PipelineEvent, _extract_github_links

        events = [
            PipelineEvent(role="lifecycle", text="Opened PR https://github.com/org/repo/pull/99"),
        ]
        issue_url, pr_url = _extract_github_links(events)
        assert issue_url is None
        assert pr_url == "https://github.com/org/repo/pull/99"

    def test_extract_both_from_separate_events(self):
        from ui.data.sdlc import PipelineEvent, _extract_github_links

        events = [
            PipelineEvent(role="lifecycle", text="issue https://github.com/org/repo/issues/1"),
            PipelineEvent(role="lifecycle", text="pr https://github.com/org/repo/pull/2"),
        ]
        issue_url, pr_url = _extract_github_links(events)
        assert issue_url == "https://github.com/org/repo/issues/1"
        assert pr_url == "https://github.com/org/repo/pull/2"

    def test_newest_event_wins(self):
        """Iteration is newest→oldest so fresher URLs override older ones."""
        from ui.data.sdlc import PipelineEvent, _extract_github_links

        events = [
            PipelineEvent(role="lifecycle", text="first pr https://github.com/org/repo/pull/1"),
            PipelineEvent(role="lifecycle", text="second pr https://github.com/org/repo/pull/2"),
        ]
        _, pr_url = _extract_github_links(events)
        assert pr_url == "https://github.com/org/repo/pull/2"

    def test_ignores_non_github_urls(self):
        from ui.data.sdlc import PipelineEvent, _extract_github_links

        events = [
            PipelineEvent(role="lifecycle", text="See https://gitlab.com/org/repo/-/issues/42"),
        ]
        assert _extract_github_links(events) == (None, None)

    def test_accepts_plain_strings(self):
        """Tolerates raw string entries as well as PipelineEvent objects."""
        from ui.data.sdlc import _extract_github_links

        events = ["Done. https://github.com/org/repo/pull/7 is merged"]
        _, pr_url = _extract_github_links(events)
        assert pr_url == "https://github.com/org/repo/pull/7"

    def test_is_active_states(self):
        from ui.data.sdlc import PipelineProgress

        for status in ("pending", "running", "active", "waiting_for_children"):
            p = PipelineProgress(agent_session_id="123", status=status)
            assert p.is_active, f"Expected {status} to be active"

    def test_is_complete_states(self):
        from ui.data.sdlc import PipelineProgress

        for status in ("completed", "failed"):
            p = PipelineProgress(agent_session_id="123", status=status)
            assert p.is_complete

    def test_duration_calculation(self):
        from ui.data.sdlc import PipelineProgress

        now = time.time()
        p = PipelineProgress(agent_session_id="123", started_at=now - 100, completed_at=now)
        assert abs(p.duration - 100) < 1

    def test_duration_none_when_no_start(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(agent_session_id="123")
        assert p.duration is None

    def test_project_name_field(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(
            agent_session_id="123",
            project_key="popoto",
            project_name="Popoto ORM",
        )
        assert p.project_name == "Popoto ORM"

    def test_project_name_defaults_none(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(agent_session_id="123", project_key="popoto")
        assert p.project_name is None

    def test_project_metadata_field(self):
        from ui.data.sdlc import PipelineProgress

        metadata = {
            "github_repo": "tomcounsell/popoto",
            "tech_stack": "Python, Redis",
        }
        p = PipelineProgress(
            agent_session_id="123",
            project_key="popoto",
            project_metadata=metadata,
        )
        assert p.project_metadata["github_repo"] == "tomcounsell/popoto"

    def test_new_metadata_fields(self):
        """All new session metadata fields should be settable."""
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(
            agent_session_id="123",
            context_summary="Building auth flow",
            expectations="Need API key from human",
            turn_count=5,
            tool_call_count=12,
            unhealthy_reason="No response for 15 minutes",
            priority="high",
            classification_type="sdlc",
            is_stale=True,
            parent_agent_session_id="parent-456",
        )
        assert p.context_summary == "Building auth flow"
        assert p.expectations == "Need API key from human"
        assert p.turn_count == 5
        assert p.tool_call_count == 12
        assert p.unhealthy_reason == "No response for 15 minutes"
        assert p.priority == "high"
        assert p.classification_type == "sdlc"
        assert p.is_stale is True
        assert p.parent_agent_session_id == "parent-456"

    def test_children_field_default_empty(self):
        """Children field defaults to empty list."""
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(agent_session_id="123")
        assert p.children == []

    def test_is_stale_default_false(self):
        """is_stale defaults to False."""
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(agent_session_id="123")
        assert p.is_stale is False

    def test_none_metadata_fields_render_cleanly(self):
        """None/missing metadata fields should not raise errors."""
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(
            agent_session_id="123",
            context_summary=None,
            expectations=None,
            turn_count=None,
            tool_call_count=None,
        )
        assert p.context_summary is None
        assert p.expectations is None
        assert p.turn_count is None
        assert p.tool_call_count is None

    def test_runner_identity_fields(self):
        """Session-runner identity fields are settable and default None/False.

        Post-cutover (#1924): ``dev_pid`` and ``pty_slot`` died with the PTY
        substrate. Schema diet (#1927) removed the two transcript-path
        fields (no live writer); the surviving identity surface is
        exit_reason, pm_pid, and user_facing_routed.
        """
        from ui.data.sdlc import PipelineProgress

        # Defaults
        p = PipelineProgress(agent_session_id="x")
        assert p.exit_reason is None
        assert p.pm_pid is None
        assert p.user_facing_routed is False

        # Explicit values
        p2 = PipelineProgress(
            agent_session_id="x",
            exit_reason="pm_complete",
            pm_pid=1234,
            user_facing_routed=True,
        )
        assert p2.exit_reason == "pm_complete"
        assert p2.pm_pid == 1234
        assert p2.user_facing_routed is True

    def test_resume_scalar_fields(self):
        """Resume scalars (#1924 Success Criterion 3) are settable, default None."""
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(agent_session_id="x")
        assert p.dev_agent_id is None
        assert p.runner_cwd is None
        assert p.claude_version is None

        p2 = PipelineProgress(
            agent_session_id="x",
            dev_agent_id="agent-abc123",
            runner_cwd="/Users/x/src/proj",
            claude_version="2.0.5",
        )
        assert p2.dev_agent_id == "agent-abc123"
        assert p2.runner_cwd == "/Users/x/src/proj"
        assert p2.claude_version == "2.0.5"

    def test_pty_fields_stay_deleted(self):
        """``pty_slot`` and ``dev_pid`` must not resurface on PipelineProgress
        (#1924 one-way cutover; names checked as strings intentionally)."""
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(agent_session_id="x")
        assert not hasattr(p, "pty_slot")
        assert not hasattr(p, "dev_pid")


class TestSessionToPipeline:
    """Tests for _session_to_pipeline conversion with new fields."""

    def test_populates_new_metadata_fields(self):
        """_session_to_pipeline should extract new fields from AgentSession."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            context_summary="Implementing feature X",
            expectations="Waiting for review",
            turn_count=10,
            tool_call_count=25,
            unhealthy_reason="Stuck for 20 min",
            priority="high",
            extra_context={"classification_type": "sdlc"},
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.context_summary == "Implementing feature X"
        assert pipeline.expectations == "Waiting for review"
        assert pipeline.turn_count == 10
        assert pipeline.tool_call_count == 25
        assert pipeline.unhealthy_reason == "Stuck for 20 min"
        assert pipeline.priority == "high"
        assert pipeline.classification_type == "sdlc"

    def test_staleness_detection_stale(self):
        """Running session with old updated_at should be flagged stale."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            status="running",
            updated_at=time.time() - 700,  # >10 minutes ago
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.is_stale is True

    def test_staleness_detection_fresh(self):
        """Running session with recent updated_at should not be stale."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            status="running",
            updated_at=time.time() - 60,  # 1 minute ago
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.is_stale is False

    def test_staleness_not_applied_to_completed(self):
        """Completed sessions should never be flagged stale."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            status="completed",
            updated_at=time.time() - 7200,  # 2 hours ago
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.is_stale is False

    def test_datetime_timestamps_converted(self):
        """datetime.datetime values in timestamp fields should be converted to float."""
        from ui.data.sdlc import _session_to_pipeline

        now = datetime.datetime.now(datetime.UTC)
        mock_session = _make_mock_session(
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        pipeline = _session_to_pipeline(mock_session)
        assert isinstance(pipeline.created_at, float)
        assert isinstance(pipeline.started_at, float)
        assert isinstance(pipeline.updated_at, float)
        assert pipeline.created_at == now.timestamp()

    def test_parent_agent_session_id_populated(self):
        """parent_agent_session_id should be passed through."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            parent_agent_session_id="parent-abc",
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.parent_agent_session_id == "parent-abc"

    def test_github_links_from_model_fields(self):
        """When AgentSession.issue_url/pr_url are set, they flow through directly."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            issue_url="https://github.com/org/repo/issues/42",
            pr_url="https://github.com/org/repo/pull/99",
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.issue_url == "https://github.com/org/repo/issues/42"
        assert pipeline.pr_url == "https://github.com/org/repo/pull/99"

    def test_github_links_fallback_from_history(self):
        """When model fields are None, URLs are extracted from history events."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            issue_url=None,
            pr_url=None,
            history=[
                {"role": "lifecycle", "text": "Created issue https://github.com/org/repo/issues/7"},
                {"role": "lifecycle", "text": "Opened PR https://github.com/org/repo/pull/8"},
            ],
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.issue_url == "https://github.com/org/repo/issues/7"
        assert pipeline.pr_url == "https://github.com/org/repo/pull/8"

    def test_github_links_model_field_wins_over_history(self):
        """Real model data is authoritative; history fallback only fills gaps."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            issue_url="https://github.com/org/repo/issues/1",
            pr_url=None,
            history=[
                {
                    "role": "lifecycle",
                    "text": "Created issue https://github.com/org/repo/issues/999",
                },
                {"role": "lifecycle", "text": "Opened PR https://github.com/org/repo/pull/2"},
            ],
        )
        pipeline = _session_to_pipeline(mock_session)
        # Model field wins for issue_url (authoritative)
        assert pipeline.issue_url == "https://github.com/org/repo/issues/1"
        # History fills in pr_url (gap-filling)
        assert pipeline.pr_url == "https://github.com/org/repo/pull/2"

    def test_missing_new_fields_handled_gracefully(self):
        """Sessions without new fields (e.g., old records) should not raise."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = MagicMock()
        mock_session.agent_session_id = "old-session"
        mock_session.session_id = "sess-1"
        mock_session.session_type = "eng"
        mock_session.status = "completed"
        mock_session.slug = None
        mock_session.message_text = "old message"
        mock_session.project_key = None
        mock_session.branch_name = None
        mock_session.created_at = time.time()
        mock_session.started_at = time.time()
        mock_session.completed_at = time.time()
        mock_session.updated_at = time.time()
        mock_session.stage_states = None
        mock_session.history = []
        mock_session.issue_url = None
        mock_session.plan_url = None
        mock_session.pr_url = None
        # These attributes won't exist on very old sessions
        del mock_session.parent_agent_session_id
        del mock_session.context_summary
        del mock_session.expectations
        del mock_session.turn_count
        del mock_session.tool_call_count
        del mock_session.unhealthy_reason
        del mock_session.priority
        del mock_session.extra_context

        # Should not raise
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.agent_session_id == "old-session"
        assert pipeline.parent_agent_session_id is None
        assert pipeline.context_summary is None

    def test_resume_scalars_populated(self):
        """The three resume scalars flow from AgentSession to PipelineProgress."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(
            dev_agent_id="agent-dev42",
            runner_cwd="/Users/x/src/ai/.worktrees/slug",
            claude_version="2.0.5",
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.dev_agent_id == "agent-dev42"
        assert pipeline.runner_cwd == "/Users/x/src/ai/.worktrees/slug"
        assert pipeline.claude_version == "2.0.5"

    def test_resume_scalars_absent_on_old_records(self):
        """Old AgentSession records without the resume scalars must not raise."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session()
        del mock_session.dev_agent_id
        del mock_session.runner_cwd
        del mock_session.claude_version

        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.dev_agent_id is None
        assert pipeline.runner_cwd is None
        assert pipeline.claude_version is None


class TestThreadRollupFold:
    """Tests for the per-thread rollup + fold logic
    (issue: dashboard-thread-timing-aggregation). The thread_* raw fields
    are carried forward from prior completed runs by a separate write-path
    task; this covers the read/render side: raw passthrough plus the
    fold that computes per-thread display totals with a per-run fallback."""

    def test_raw_thread_fields_pass_through(self):
        """The four raw thread_* ORM fields flow through unchanged."""
        from ui.data.sdlc import _session_to_pipeline

        earlier = time.time() - 1800
        mock_session = _make_mock_session(
            thread_first_created_at=earlier,
            thread_turn_count=5,
            thread_tool_call_count=3,
            thread_run_count=2,
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.thread_first_created_at == earlier
        assert pipeline.thread_turn_count == 5
        assert pipeline.thread_tool_call_count == 3
        assert pipeline.thread_run_count == 2

    def test_fold_falls_back_to_per_run_values_when_never_resumed(self):
        """A never-resumed / pre-migration thread (all thread_* fields null)
        must render identically to before this feature: the folded display
        values equal the per-run values exactly."""
        from ui.data.sdlc import _session_to_pipeline

        created = time.time() - 60
        mock_session = _make_mock_session(
            created_at=created,
            turn_count=3,
            tool_call_count=2,
            thread_first_created_at=None,
            thread_turn_count=None,
            thread_tool_call_count=None,
            thread_run_count=None,
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.thread_display_turn_count == pipeline.turn_count == 3
        assert pipeline.thread_display_tool_call_count == pipeline.tool_call_count == 2
        assert pipeline.thread_display_started_at == pipeline.created_at
        assert pipeline.thread_display_run_count == 1

    def test_fold_sums_rollup_and_current_run_when_resumed(self):
        """A resumed thread folds prior-run rollup with this run's in-flight
        counters, and the thread start time is the earliest run's creation."""
        from ui.data.sdlc import _session_to_pipeline

        thread_start = time.time() - 1800  # thread actually started 30 min ago
        this_run_created = time.time() - 180  # this resume started 3 min ago
        mock_session = _make_mock_session(
            created_at=this_run_created,
            turn_count=3,
            tool_call_count=2,
            thread_first_created_at=thread_start,
            thread_turn_count=5,
            thread_tool_call_count=3,
            thread_run_count=2,
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.thread_display_turn_count == 8
        assert pipeline.thread_display_tool_call_count == 5
        assert pipeline.thread_display_started_at == thread_start
        assert pipeline.thread_display_run_count == 2

    def test_missing_thread_fields_handled_gracefully(self):
        """Records predating this migration (no thread_* attrs at all) must
        not raise -- graceful degradation, not an exception path."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = _make_mock_session(turn_count=4, tool_call_count=1)
        del mock_session.thread_first_created_at
        del mock_session.thread_turn_count
        del mock_session.thread_tool_call_count
        del mock_session.thread_run_count

        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.thread_first_created_at is None
        assert pipeline.thread_turn_count is None
        assert pipeline.thread_tool_call_count is None
        assert pipeline.thread_run_count is None
        assert pipeline.thread_display_turn_count == 4
        assert pipeline.thread_display_tool_call_count == 1
        assert pipeline.thread_display_started_at == pipeline.created_at
        assert pipeline.thread_display_run_count == 1


class TestParentChildGrouping:
    """Tests for parent/child session grouping in get_all_sessions."""

    def test_children_grouped_under_parent(self):
        """Child sessions should be nested under their parent."""
        from ui.data.sdlc import get_all_sessions

        parent = _make_mock_session(
            agent_session_id="parent-1",
            status="running",
            parent_agent_session_id=None,
        )
        child = _make_mock_session(
            agent_session_id="child-1",
            status="running",
            parent_agent_session_id="parent-1",
        )

        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.all.return_value = [parent, child]
            with patch("ui.data.sdlc._get_project_metadata", return_value=(None, None)):
                result = get_all_sessions()

        # Child should be nested, not at top level
        assert len(result) == 1
        assert result[0].agent_session_id == "parent-1"
        assert len(result[0].children) == 1
        assert result[0].children[0].agent_session_id == "child-1"

    def test_orphaned_child_stays_top_level(self):
        """Child whose parent is not in the list stays at top level."""
        from ui.data.sdlc import get_all_sessions

        child = _make_mock_session(
            agent_session_id="orphan-1",
            status="running",
            parent_agent_session_id="missing-parent",
        )

        with patch("models.agent_session.AgentSession") as mock_as:
            mock_as.query.all.return_value = [child]
            with patch("ui.data.sdlc._get_project_metadata", return_value=(None, None)):
                result = get_all_sessions()

        assert len(result) == 1
        assert result[0].agent_session_id == "orphan-1"


class TestTargetRepoResolutionIsRequestScoped:
    """Regression for #2122: dashboard.json blocked ~20s because the
    issue-independent env-fallback repo resolution (`_resolve_target_repo`,
    which shells out to `gh repo view` / `git rev-parse`) ran once per
    session inside `get_all_sessions` — an O(N·subprocess) pattern. It must
    now resolve at most once per request regardless of session count."""

    def test_env_fallback_resolved_once_across_many_sessions(self):
        """N sessions, each with a distinct issue number whose lock peek
        pins no target_repo, must trigger the env-fallback subprocess
        resolution at most once — not once per session."""
        from ui.data.sdlc import get_all_sessions

        sessions = [
            _make_mock_session(
                agent_session_id=f"sess-{i}",
                status="running",
                parent_agent_session_id=None,
                issue_number=9_000 + i,
                stage_states=None,
            )
            for i in range(12)
        ]

        resolve_calls = {"n": 0}

        def _counting_resolve():
            resolve_calls["n"] += 1
            return None  # unresolved -> (None, None) ledger, no PipelineLedger

        # Peek pins no target_repo -> resolution falls through to the
        # env-fallback, which is exactly the path #2122 collapses.
        peek = MagicMock()
        peek.target_repo = None

        with (
            patch("models.agent_session.AgentSession") as mock_as,
            patch("tools._sdlc_utils._resolve_target_repo", side_effect=_counting_resolve),
            patch("models.session_lifecycle.touch_issue_lock", return_value=peek),
        ):
            mock_as.query.all.return_value = sessions
            result = get_all_sessions()

        assert len(result) == 12
        assert resolve_calls["n"] <= 1, (
            f"env-fallback _resolve_target_repo invoked {resolve_calls['n']} times "
            "across 12 sessions; expected <=1 (O(N·subprocess) regression, #2122)"
        )

    def test_no_caching_scope_outside_dashboard_fanout(self):
        """Outside a `cached_target_repo_resolution()` scope, each read
        resolves fresh — the memo is inert and must not leak across
        unrelated callers (writers/lease-acquire, SDLC tools)."""
        from tools import _sdlc_utils

        calls = {"n": 0}

        def _fresh():
            calls["n"] += 1
            return "owner/repo"

        with patch("tools._sdlc_utils._resolve_target_repo", side_effect=_fresh):
            _sdlc_utils.resolve_target_repo_for_read(None)
            _sdlc_utils.resolve_target_repo_for_read(None)
            assert calls["n"] == 2, "resolution must be uncached outside a scope"

            calls["n"] = 0
            with _sdlc_utils.cached_target_repo_resolution():
                for _ in range(4):
                    _sdlc_utils.resolve_target_repo_for_read(None)
            assert calls["n"] == 1, "resolution must be memoized within a scope"


class TestRetentionWithDatetime:
    """Tests verifying retention filter works with datetime timestamps."""

    def test_datetime_timestamps_enable_retention(self):
        """Sessions with datetime timestamps should pass the retention filter."""
        from ui.data.sdlc import _safe_float

        # Before the fix, this would return None and sessions would get
        # timestamp 0, placing them before the 48h cutoff
        now_dt = datetime.datetime.now()
        ts = _safe_float(now_dt)
        assert ts is not None
        cutoff = time.time() - 48 * 3600
        assert ts > cutoff  # Recent datetime should be within retention


class TestHistoryParsing:
    """Tests for parsing AgentSession history into PipelineEvent objects."""

    def test_parse_none(self):
        from ui.data.sdlc import _parse_history

        assert _parse_history(None) == []

    def test_parse_empty_list(self):
        from ui.data.sdlc import _parse_history

        assert _parse_history([]) == []

    def test_parse_bracketed_entries(self):
        from ui.data.sdlc import _parse_history

        history = [
            "[stage] ISSUE -> PLAN",
            "[lifecycle] pending->running",
            "[user] Start building",
        ]
        events = _parse_history(history)
        assert len(events) == 3
        assert events[0].role == "stage"
        assert events[0].text == "ISSUE -> PLAN"
        assert events[1].role == "lifecycle"

    def test_parse_plain_string(self):
        from ui.data.sdlc import _parse_history

        events = _parse_history(["some plain text"])
        assert events[0].role == "system"
        assert events[0].text == "some plain text"

    def test_parse_dict_entries(self):
        from ui.data.sdlc import _parse_history

        events = _parse_history([{"role": "stage", "text": "BUILD started", "timestamp": 123.0}])
        assert events[0].role == "stage"
        assert events[0].timestamp == 123.0

    def test_granite_routing_events_render_readably(self):
        """Granite routing events (event_type key) produce typed PipelineEvents."""
        from ui.data.sdlc import _parse_history

        history = [
            {"event_type": "granite_user_routed", "text": "user message delivered", "ts": 111.0},
            {"event_type": "granite_complete_routed", "text": "complete delivered", "ts": 222.0},
            {
                "event_type": "granite_delivery_recovered_via_outbox",
                "text": "recovered",
                "ts": 333.0,
            },
        ]
        events = _parse_history(history)
        assert len(events) == 3
        assert events[0].event_type == "granite_user_routed"
        assert events[0].text == "user message delivered"
        assert events[0].timestamp == 111.0
        assert events[1].event_type == "granite_complete_routed"
        assert events[2].event_type == "granite_delivery_recovered_via_outbox"

    def test_exit_anomaly_event_rendered_with_reason(self):
        """exit_anomaly entries are rendered with their exit_reason."""
        from ui.data.sdlc import _parse_history

        history = [{"type": "exit_anomaly", "exit_reason": "crash", "ts": 555.0}]
        events = _parse_history(history)
        assert len(events) == 1
        assert events[0].event_type == "exit_anomaly"
        assert "crash" in events[0].text
        assert events[0].timestamp == 555.0

    def test_turn_history_event_labeled_with_actor(self):
        """Runner turn-history mirror entries surface with actor + text, not
        as generic 'system' events (#1924 Success Criterion 3)."""
        from ui.data.sdlc import _parse_history

        history = [
            {
                "type": "turn_history",
                "event_type": "turn_history",
                "actor": "dev",
                "text": "built the thing",
                "ts": "2026-07-07T10:00:00+00:00",
            },
            {
                "type": "turn_history",
                "event_type": "turn_history",
                "actor": "pm",
                "text": "reviewed and shipped",
                "ts": "2026-07-07T10:01:00+00:00",
            },
        ]
        events = _parse_history(history)
        assert len(events) == 2
        assert events[0].event_type == "turn_history"
        assert "dev" in events[0].text
        assert "built the thing" in events[0].text
        assert events[1].event_type == "turn_history"
        assert "pm" in events[1].text
        assert "reviewed and shipped" in events[1].text

    def test_turn_history_type_only_entry_tolerated(self):
        """Mirror entries written before the dual-key fix (type-only) still
        parse as labeled turn history, not 'system' (exit_anomaly precedent)."""
        from ui.data.sdlc import _parse_history

        events = _parse_history([{"type": "turn_history", "actor": "pm", "text": "hi", "ts": "t"}])
        assert len(events) == 1
        assert events[0].event_type == "turn_history"
        assert "pm" in events[0].text
        assert "hi" in events[0].text

    def test_ts_key_used_for_timestamp_when_no_timestamp(self):
        """Events with 'ts' key (granite format) populate the timestamp field."""
        from ui.data.sdlc import _parse_history

        events = _parse_history([{"event_type": "granite_user_routed", "text": "x", "ts": 999.0}])
        assert events[0].timestamp == 999.0


class TestSafeStr:
    """Tests for _safe_str helper that sanitizes Popoto field values."""

    def test_normal_string(self):
        from ui.data.sdlc import _safe_str

        assert _safe_str("hello") == "hello"

    def test_none_returns_default(self):
        from ui.data.sdlc import _safe_str

        assert _safe_str(None) is None
        assert _safe_str(None, "fallback") == "fallback"

    def test_int_converted(self):
        from ui.data.sdlc import _safe_str

        assert _safe_str(42) == "42"

    def test_float_converted(self):
        from ui.data.sdlc import _safe_str

        assert _safe_str(3.14) == "3.14"

    def test_bool_converted(self):
        from ui.data.sdlc import _safe_str

        assert _safe_str(True) == "True"

    def test_popoto_object_returns_default(self):
        """Popoto DB_key or Field objects should be rejected, not str()-ed."""
        from ui.data.sdlc import _safe_str

        class FakeField:
            pass

        assert _safe_str(FakeField()) is None
        assert _safe_str(FakeField(), "default") == "default"

    def test_empty_string_preserved(self):
        from ui.data.sdlc import _safe_str

        assert _safe_str("") == ""


class TestSafeFloat:
    """Tests for _safe_float helper that sanitizes Popoto numeric fields."""

    def test_int(self):
        from ui.data.sdlc import _safe_float

        assert _safe_float(42) == 42.0

    def test_float(self):
        from ui.data.sdlc import _safe_float

        assert _safe_float(3.14) == 3.14

    def test_numeric_string(self):
        from ui.data.sdlc import _safe_float

        assert _safe_float("1711234567.89") == 1711234567.89

    def test_none(self):
        from ui.data.sdlc import _safe_float

        assert _safe_float(None) is None

    def test_non_numeric_string(self):
        from ui.data.sdlc import _safe_float

        assert _safe_float("not a number") is None

    def test_popoto_object(self):
        from ui.data.sdlc import _safe_float

        class FakeField:
            pass

        assert _safe_float(FakeField()) is None

    def test_empty_string(self):
        from ui.data.sdlc import _safe_float

        assert _safe_float("") is None

    def test_datetime_input(self):
        """datetime.datetime objects should be converted via .timestamp()."""
        from ui.data.sdlc import _safe_float

        dt = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        result = _safe_float(dt)
        assert result is not None
        assert isinstance(result, float)
        assert result == dt.timestamp()

    def test_datetime_with_timezone(self):
        """datetime with UTC timezone should convert correctly."""
        from ui.data.sdlc import _safe_float

        dt = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        result = _safe_float(dt)
        assert result is not None
        assert isinstance(result, float)
        assert result == dt.timestamp()

    def test_datetime_preserves_precision(self):
        """datetime conversion should preserve sub-second precision."""
        from ui.data.sdlc import _safe_float

        dt = datetime.datetime(2026, 6, 15, 12, 30, 45, 123456)
        result = _safe_float(dt)
        assert result is not None
        # Reconstruct and verify roundtrip
        reconstructed = datetime.datetime.fromtimestamp(result)
        assert reconstructed.year == 2026
        assert reconstructed.month == 6


class TestPipelineStateMachineRouting:
    """Tests for stage routing through PipelineStateMachine in _session_to_pipeline."""

    def test_session_with_stage_states_uses_pipeline_state_machine(self):
        """Sessions with stage_states route through PipelineStateMachine."""
        from ui.data.sdlc import _session_to_pipeline

        mock_display = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "in_progress",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
            "MERGE": "pending",
        }
        session = _make_mock_session(
            slug="my-feature",
            stage_states={"ISSUE": "completed", "PLAN": "pending"},
        )

        with patch("agent.pipeline_state.PipelineStateMachine") as mock_psm:
            mock_psm.return_value.get_display_progress.return_value = mock_display
            pipeline = _session_to_pipeline(session)

        assert len(pipeline.stages) == 8
        by_name = {s.name: s for s in pipeline.stages}
        assert by_name["PLAN"].is_done
        assert by_name["BUILD"].is_active
        mock_psm.return_value.get_display_progress.assert_called_once()

    def test_session_without_stage_states_produces_empty_stages(self):
        """Sessions with no stage_states get empty stages (no PSM call)."""
        from ui.data.sdlc import _session_to_pipeline

        session = _make_mock_session(
            slug=None,
            stage_states=None,
        )

        with patch("agent.pipeline_state.PipelineStateMachine") as mock_psm:
            pipeline = _session_to_pipeline(session)

        mock_psm.assert_not_called()
        assert pipeline.stages == []

    def test_empty_stage_states_produces_empty_stages(self):
        """Sessions with empty string stage_states should get empty stages."""
        from ui.data.sdlc import _session_to_pipeline

        session = _make_mock_session(
            slug="some-slug",
            stage_states="",
        )

        with patch("agent.pipeline_state.PipelineStateMachine") as mock_psm:
            pipeline = _session_to_pipeline(session)

        mock_psm.assert_not_called()
        assert pipeline.stages == []

    def test_pipeline_state_machine_failure_falls_back_to_parse(self):
        """When PipelineStateMachine raises, fall back to _parse_stage_states()."""
        from ui.data.sdlc import _session_to_pipeline

        session = _make_mock_session(
            slug="failing-feature",
            stage_states={"ISSUE": "completed", "PLAN": "in_progress"},
        )

        with patch(
            "agent.pipeline_state.PipelineStateMachine",
            side_effect=Exception("state machine error"),
        ):
            pipeline = _session_to_pipeline(session)

        assert len(pipeline.stages) == 8
        by_name = {s.name: s for s in pipeline.stages}
        # Should fall back to stored state via _parse_stage_states
        assert by_name["ISSUE"].is_done
        assert by_name["PLAN"].is_active

    def test_no_slug_no_stage_states_produces_empty_stages(self):
        """Sessions with no slug and no stage_states should have empty stages."""
        from ui.data.sdlc import _session_to_pipeline

        session = _make_mock_session(slug=None, stage_states=None)
        pipeline = _session_to_pipeline(session)
        assert pipeline.stages == []


class TestProjectMetadata:
    """Tests for project name and metadata resolution."""

    def test_get_project_metadata_no_key(self):
        from ui.data.sdlc import _get_project_metadata

        name, meta = _get_project_metadata(None)
        assert name is None
        assert meta is None

    def test_get_project_metadata_not_found(self):
        from ui.data.sdlc import _get_project_metadata

        with patch("ui.data.sdlc._load_project_configs", return_value={}):
            name, meta = _get_project_metadata("nonexistent")
            assert name is None
            assert meta is None

    def test_get_project_metadata_found(self):
        from ui.data.sdlc import _get_project_metadata

        mock_configs = {
            "myproject": {
                "name": "My Project",
                "github_repo": "owner/myproject",
                "working_directory": "/home/user/myproject",
                "telegram": {"groups": ["PM: MyProject"]},
                "context": {"tech_stack": "Python, FastAPI"},
                "machine": "macbook-pro",
            }
        }
        with patch("ui.data.sdlc._load_project_configs", return_value=mock_configs):
            name, meta = _get_project_metadata("myproject")
            assert name == "My Project"
            assert meta["github_repo"] == "owner/myproject"
            assert meta["telegram_chat"] == "PM: MyProject"
            assert meta["working_dir"] == "/home/user/myproject"
            assert meta["tech_stack"] == "Python, FastAPI"
            assert meta["machine"] == "macbook-pro"

    def test_get_project_metadata_minimal(self):
        """Project with only a name still returns name, None metadata."""
        from ui.data.sdlc import _get_project_metadata

        mock_configs = {"simple": {"name": "Simple"}}
        with patch("ui.data.sdlc._load_project_configs", return_value=mock_configs):
            name, meta = _get_project_metadata("simple")
            assert name == "Simple"
            assert meta is None


class TestRetentionFilter:
    """Tests for configurable session retention in get_all_sessions."""

    def test_sessions_within_retention_included(self):
        """Sessions with recent timestamps should be included."""
        from ui.data.sdlc import PipelineProgress

        now = time.time()
        p = PipelineProgress(
            agent_session_id="123",
            status="completed",
            completed_at=now - 3600,  # 1 hour ago
        )
        # completed_at is within default 48h retention
        assert p.completed_at > (now - 48 * 3600)

    def test_timestamp_fallback_chain(self):
        """When updated_at is None, fallback to other timestamps."""
        from ui.data.sdlc import PipelineProgress

        now = time.time()
        # Session with only created_at set
        p = PipelineProgress(
            agent_session_id="123",
            status="completed",
            created_at=now - 3600,
            updated_at=None,
            completed_at=None,
            started_at=None,
        )
        # Best timestamp fallback: completed_at or updated_at or started_at or created_at
        best_ts = p.completed_at or p.updated_at or p.started_at or p.created_at or 0
        assert best_ts == p.created_at
        assert best_ts > 0


class TestSdlcQueryFunctions:
    """Tests for SDLC query functions against Redis."""

    def test_get_all_sessions_returns_list(self):
        from ui.data.sdlc import get_all_sessions

        result = get_all_sessions()
        assert isinstance(result, list)

    def test_get_active_pipelines_returns_list(self):
        from ui.data.sdlc import get_active_pipelines

        result = get_active_pipelines()
        assert isinstance(result, list)

    def test_get_recent_completions_returns_list(self):
        from ui.data.sdlc import get_recent_completions

        result = get_recent_completions()
        assert isinstance(result, list)

    def test_get_pipeline_detail_not_found(self):
        from ui.data.sdlc import get_pipeline_detail

        result = get_pipeline_detail("nonexistent-session-id-12345")
        assert result is None


class TestIssueKeyedLedgerRouting:
    """Issue #2012 task 2: the dashboard reads stage display data through the
    issue-keyed PipelineLedger first, with a retained session-state
    fallback -- and its "has this session recorded any progress" filter
    must not skip a takeover session whose writes all landed on the
    ledger instead of this particular session's stage_states field."""

    def test_derive_issue_number_ignores_magicmock_autovivification(self):
        """A bare MagicMock() auto-vivifies ANY attribute -- isinstance(int)
        must reject it rather than treating the mock object as a real
        (garbage) issue number."""
        from ui.data.sdlc import _derive_issue_number

        session = _make_mock_session()  # issue_url=None, no explicit issue_number
        assert _derive_issue_number(session) is None

    def test_derive_issue_number_prefers_mirror_field(self):
        from ui.data.sdlc import _derive_issue_number

        session = _make_mock_session(issue_number=1234)
        assert _derive_issue_number(session) == 1234

    def test_derive_issue_number_falls_back_to_issue_url(self):
        from ui.data.sdlc import _derive_issue_number

        session = _make_mock_session(
            issue_number=None, issue_url="https://github.com/tomcounsell/ai/issues/5678"
        )
        assert _derive_issue_number(session) == 5678

    def test_resolve_issue_ledger_returns_none_when_target_repo_unresolved(self):
        """Risk 5 (reader side): target_repo cannot be resolved at all ->
        (None, None), never a phantom PipelineLedger[(None, issue)] key."""
        from ui.data.sdlc import _resolve_issue_ledger

        with (
            patch("tools._sdlc_utils.resolve_target_repo_for_read", return_value=None),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get_or_create,
        ):
            target_repo, ledger = _resolve_issue_ledger(999777)

        assert target_repo is None
        assert ledger is None
        mock_get_or_create.assert_not_called()

    def test_session_to_pipeline_reads_via_ledger_when_populated(self):
        """A session with NO stage_states of its own, but whose issue has a
        populated PipelineLedger, still renders real stage data -- the
        takeover-session scenario issue #2012 exists to fix."""
        from agent.pipeline_ledger import PipelineLedger
        from ui.data.sdlc import _session_to_pipeline

        ledger = PipelineLedger.get_or_create("owner/dashboard-ledger-routing", 700601)
        ledger.stage_states_json = json.dumps({"ISSUE": "completed", "PLAN": "in_progress"})
        ledger.save()

        session = _make_mock_session(
            slug="takeover-session", stage_states=None, issue_number=700601
        )

        with patch(
            "tools._sdlc_utils.resolve_target_repo_for_read",
            return_value="owner/dashboard-ledger-routing",
        ):
            pipeline = _session_to_pipeline(session)

        by_name = {s.name: s for s in pipeline.stages}
        assert by_name["ISSUE"].is_done
        assert by_name["PLAN"].is_active

    def test_session_to_pipeline_falls_back_to_session_when_ledger_empty(self):
        """target_repo resolves and the ledger loads, but it carries no
        recorded stage state yet -- falls back to the session's own
        stage_states, byte-identical to pre-#2012 behavior."""
        from ui.data.sdlc import _session_to_pipeline

        session = _make_mock_session(
            slug="pre-cutover-session",
            stage_states={"ISSUE": "completed", "PLAN": "pending"},
            issue_number=700602,
        )

        with (
            patch(
                "tools._sdlc_utils.resolve_target_repo_for_read",
                return_value="owner/empty-dashboard-ledger",
            ),
            patch("agent.pipeline_state.PipelineStateMachine") as mock_psm,
        ):
            mock_psm.return_value.get_display_progress.return_value = {
                "ISSUE": "completed",
                "PLAN": "pending",
            }
            pipeline = _session_to_pipeline(session)

        by_name = {s.name: s for s in pipeline.stages}
        assert by_name["ISSUE"].is_done
        # Constructed against the SESSION-keyed path (not for_issue()).
        mock_psm.assert_called_once_with(session)

    def test_session_has_stage_data_true_when_own_stage_states_populated(self):
        from ui.data.sdlc import _session_has_stage_data

        session = _make_mock_session(stage_states={"ISSUE": "completed"})
        assert _session_has_stage_data(session) is True

    def test_session_has_stage_data_true_when_ledger_populated_but_session_empty(self):
        """The exact takeover-session scenario the dashboard filter must
        not silently drop from get_recent_completions()."""
        from agent.pipeline_ledger import PipelineLedger
        from ui.data.sdlc import _session_has_stage_data

        ledger = PipelineLedger.get_or_create("owner/filter-ledger-routing", 700603)
        ledger.stage_states_json = json.dumps({"ISSUE": "completed"})
        ledger.save()

        session = _make_mock_session(stage_states=None, issue_number=700603)

        with patch(
            "tools._sdlc_utils.resolve_target_repo_for_read",
            return_value="owner/filter-ledger-routing",
        ):
            assert _session_has_stage_data(session) is True

    def test_session_has_stage_data_false_when_neither_session_nor_ledger_populated(self):
        from ui.data.sdlc import _session_has_stage_data

        session = _make_mock_session(stage_states=None, issue_number=None, issue_url=None)
        assert _session_has_stage_data(session) is False
