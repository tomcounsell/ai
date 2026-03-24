"""Tests for the SDLC data access layer and Pydantic serializers."""

import json

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]


class TestStageStateParsing:
    """Tests for parsing stage_states into typed StageState objects."""

    def test_parse_none(self):
        from ui.data.sdlc import _parse_stage_states

        result = _parse_stage_states(None)
        assert len(result) == 8  # All SDLC stages
        assert all(s.status == "pending" for s in result)

    def test_parse_empty_string(self):
        from ui.data.sdlc import _parse_stage_states

        result = _parse_stage_states("")
        assert len(result) == 8
        assert all(s.status == "pending" for s in result)

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
        assert len(result) == 8
        assert all(s.status == "pending" for s in result)

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


class TestPipelineProgress:
    """Tests for the PipelineProgress Pydantic model."""

    def test_display_name_with_slug(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(job_id="123", slug="my-feature")
        assert p.display_name == "my-feature"

    def test_display_name_with_message(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(job_id="123", message_text="Build the web UI for reflections")
        assert p.display_name == "Build the web UI for reflections"

    def test_display_name_truncated(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(job_id="123", message_text="A" * 100)
        assert len(p.display_name) < 100
        assert "..." in p.display_name

    def test_is_active_states(self):
        from ui.data.sdlc import PipelineProgress

        for status in ("pending", "running", "active", "waiting_for_children"):
            p = PipelineProgress(job_id="123", status=status)
            assert p.is_active, f"Expected {status} to be active"

    def test_is_complete_states(self):
        from ui.data.sdlc import PipelineProgress

        for status in ("completed", "failed"):
            p = PipelineProgress(job_id="123", status=status)
            assert p.is_complete

    def test_duration_calculation(self):
        import time

        from ui.data.sdlc import PipelineProgress

        now = time.time()
        p = PipelineProgress(job_id="123", started_at=now - 100, completed_at=now)
        assert abs(p.duration - 100) < 1

    def test_duration_none_when_no_start(self):
        from ui.data.sdlc import PipelineProgress

        p = PipelineProgress(job_id="123")
        assert p.duration is None


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


class TestSdlcQueryFunctions:
    """Tests for SDLC query functions against Redis."""

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

        result = get_pipeline_detail("nonexistent-job-id-12345")
        assert result is None
