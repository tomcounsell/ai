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
        "session_type": "dev",
        "session_mode": None,
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
        "watchdog_unhealthy": None,
        "priority": "normal",
        "extra_context": None,
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

    def test_display_name_prefers_context_summary(self):
        """context_summary takes priority over slug and message_text."""
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(
            agent_session_id="123",
            context_summary="Implementing auth flow",
            slug="auth-flow",
            message_text="Build the auth",
        )
        assert p.display_name == "Implementing auth flow"

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
            watchdog_unhealthy="No response for 15 minutes",
            priority="high",
            classification_type="sdlc",
            is_stale=True,
            parent_agent_session_id="parent-456",
        )
        assert p.context_summary == "Building auth flow"
        assert p.expectations == "Need API key from human"
        assert p.turn_count == 5
        assert p.tool_call_count == 12
        assert p.watchdog_unhealthy == "No response for 15 minutes"
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
            watchdog_unhealthy="Stuck for 20 min",
            priority="high",
            extra_context={"classification_type": "sdlc"},
        )
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.context_summary == "Implementing feature X"
        assert pipeline.expectations == "Waiting for review"
        assert pipeline.turn_count == 10
        assert pipeline.tool_call_count == 25
        assert pipeline.watchdog_unhealthy == "Stuck for 20 min"
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

        # Use UTC-aware datetime to avoid timezone ambiguity: _safe_float() re-attaches UTC
        # to naive datetimes, so using a naive local datetime would shift the result.
        now = datetime.datetime.now(tz=datetime.UTC)
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

    def test_missing_new_fields_handled_gracefully(self):
        """Sessions without new fields (e.g., old records) should not raise."""
        from ui.data.sdlc import _session_to_pipeline

        mock_session = MagicMock()
        mock_session.agent_session_id = "old-session"
        mock_session.session_id = "sess-1"
        mock_session.session_type = "dev"
        mock_session.session_mode = None
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
        del mock_session.watchdog_unhealthy
        del mock_session.priority
        del mock_session.extra_context

        # Should not raise
        pipeline = _session_to_pipeline(mock_session)
        assert pipeline.agent_session_id == "old-session"
        assert pipeline.parent_agent_session_id is None
        assert pipeline.context_summary is None


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
        """datetime.datetime objects should be converted to a float timestamp."""
        from ui.data.sdlc import _safe_float

        # Use UTC-aware datetime: _safe_float() re-attaches UTC to naive datetimes,
        # so comparing against dt.timestamp() (local-tz) would fail in non-UTC zones.
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
    """Tests for routing stage reads through PipelineStateMachine.get_display_progress()."""

    def test_session_with_stage_states_uses_pipeline_state_machine(self):
        """Sessions with stage_states should call PipelineStateMachine.get_display_progress()."""
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
            stage_states={"ISSUE": "completed", "PLAN": "completed"},
        )

        with patch("ui.data.sdlc.PipelineStateMachine") as mock_psm:
            mock_psm.return_value.get_display_progress.return_value = mock_display
            pipeline = _session_to_pipeline(session)

        assert len(pipeline.stages) == 8
        by_name = {s.name: s for s in pipeline.stages}
        assert by_name["BUILD"].is_active
        assert by_name["ISSUE"].is_done
        mock_psm.return_value.get_display_progress.assert_called_once_with()

    def test_pipeline_state_machine_exception_falls_back_to_parse(self):
        """When PipelineStateMachine raises, fall back to _parse_stage_states()."""
        from ui.data.sdlc import _session_to_pipeline

        session = _make_mock_session(
            stage_states={"ISSUE": "completed", "PLAN": "in_progress"},
        )

        with patch(
            "ui.data.sdlc.PipelineStateMachine",
            side_effect=Exception("state machine error"),
        ):
            pipeline = _session_to_pipeline(session)

        assert len(pipeline.stages) == 8
        by_name = {s.name: s for s in pipeline.stages}
        # Should fall back to stored state via _parse_stage_states()
        assert by_name["ISSUE"].is_done
        assert by_name["PLAN"].is_active

    def test_session_with_no_stage_states_gets_empty_stages(self):
        """Sessions with no stage_states should have empty stages, no PipelineStateMachine call."""
        from ui.data.sdlc import _session_to_pipeline

        session = _make_mock_session(stage_states=None)

        with patch("ui.data.sdlc.PipelineStateMachine") as mock_psm:
            pipeline = _session_to_pipeline(session)

        mock_psm.assert_not_called()
        assert pipeline.stages == []

    def test_session_with_empty_string_stage_states_gets_empty_stages(self):
        """Sessions with empty string stage_states should not call PipelineStateMachine."""
        from ui.data.sdlc import _session_to_pipeline

        session = _make_mock_session(stage_states="")

        with patch("ui.data.sdlc.PipelineStateMachine") as mock_psm:
            pipeline = _session_to_pipeline(session)

        mock_psm.assert_not_called()
        assert pipeline.stages == []

    def test_session_with_malformed_stage_states_falls_back_gracefully(self):
        """Sessions with malformed stage_states fall back gracefully via _parse_stage_states."""
        from ui.data.sdlc import _session_to_pipeline

        # "not-valid-json{{{" is truthy so PipelineStateMachine is attempted
        # It will raise, and _parse_stage_states("not-valid-json{{{") returns []
        session = _make_mock_session(stage_states="not-valid-json{{{")

        with patch(
            "ui.data.sdlc.PipelineStateMachine",
            side_effect=Exception("cannot parse"),
        ):
            pipeline = _session_to_pipeline(session)

        # Malformed JSON falls back to empty list from _parse_stage_states
        assert isinstance(pipeline.stages, list)


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
