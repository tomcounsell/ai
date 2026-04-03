"""Tests for the SDLC data access layer and Pydantic serializers."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]


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


class TestArtifactInference:
    """Tests for artifact-enriched stage states via PipelineStateMachine."""

    def _make_session(self, **overrides):
        """Create a mock session with sensible defaults."""
        session = MagicMock()
        session.agent_session_id = overrides.get("agent_session_id", "test-123")
        session.session_id = overrides.get("session_id", "sess-1")
        session.session_type = overrides.get("session_type", "pm")
        session.session_mode = overrides.get("session_mode", None)
        session.status = overrides.get("status", "running")
        session.slug = overrides.get("slug", None)
        session.message_text = overrides.get("message_text", "test")
        session.project_key = overrides.get("project_key", None)
        session.branch_name = overrides.get("branch_name", None)
        session.created_at = overrides.get("created_at", time.time())
        session.started_at = overrides.get("started_at", time.time())
        session.completed_at = overrides.get("completed_at", None)
        session.updated_at = overrides.get("updated_at", None)
        session.stage_states = overrides.get("stage_states", None)
        session.history = overrides.get("history", None)
        session.issue_url = overrides.get("issue_url", None)
        session.plan_url = overrides.get("plan_url", None)
        session.pr_url = overrides.get("pr_url", None)
        return session

    def test_session_with_slug_uses_artifact_inference(self):
        """Sessions with a slug should use PipelineStateMachine.get_display_progress()."""
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
        session = self._make_session(
            slug="my-feature",
            stage_states={"ISSUE": "completed", "PLAN": "pending"},
        )

        with patch(
            "bridge.pipeline_state.PipelineStateMachine"
        ) as MockPSM:
            MockPSM.return_value.get_display_progress.return_value = mock_display
            pipeline = _session_to_pipeline(session)

        assert len(pipeline.stages) == 8
        by_name = {s.name: s for s in pipeline.stages}
        # Artifact inference should show PLAN as completed (overriding stored "pending")
        assert by_name["PLAN"].is_done
        assert by_name["BUILD"].is_active
        MockPSM.return_value.get_display_progress.assert_called_once_with(slug="my-feature")

    def test_session_without_slug_uses_stored_state(self):
        """Sessions without a slug should use _parse_stage_states() only."""
        from ui.data.sdlc import _session_to_pipeline

        session = self._make_session(
            slug=None,
            stage_states={"ISSUE": "completed", "PLAN": "in_progress"},
        )

        with patch(
            "bridge.pipeline_state.PipelineStateMachine"
        ) as MockPSM:
            pipeline = _session_to_pipeline(session)

        # PipelineStateMachine should not be called without a slug
        MockPSM.assert_not_called()
        assert len(pipeline.stages) == 8
        by_name = {s.name: s for s in pipeline.stages}
        assert by_name["ISSUE"].is_done
        assert by_name["PLAN"].is_active

    def test_session_with_empty_slug_uses_stored_state(self):
        """Sessions with empty string slug should not use artifact inference."""
        from ui.data.sdlc import _session_to_pipeline

        session = self._make_session(
            slug="",
            stage_states={"ISSUE": "completed"},
        )

        with patch(
            "bridge.pipeline_state.PipelineStateMachine"
        ) as MockPSM:
            pipeline = _session_to_pipeline(session)

        MockPSM.assert_not_called()

    def test_artifact_inference_failure_falls_back_to_stored_state(self):
        """When PipelineStateMachine raises, fall back to _parse_stage_states()."""
        from ui.data.sdlc import _artifact_inference_cache, _session_to_pipeline

        # Clear cache to force a fresh call
        _artifact_inference_cache.clear()

        session = self._make_session(
            slug="failing-feature",
            stage_states={"ISSUE": "completed", "PLAN": "in_progress"},
        )

        with patch(
            "bridge.pipeline_state.PipelineStateMachine",
            side_effect=Exception("gh not available"),
        ):
            pipeline = _session_to_pipeline(session)

        assert len(pipeline.stages) == 8
        by_name = {s.name: s for s in pipeline.stages}
        # Should fall back to stored state
        assert by_name["ISSUE"].is_done
        assert by_name["PLAN"].is_active

    def test_session_no_slug_no_stage_states_produces_empty_stages(self):
        """Sessions with no slug and no stage_states should have empty stages."""
        from ui.data.sdlc import _session_to_pipeline

        session = self._make_session(slug=None, stage_states=None)
        pipeline = _session_to_pipeline(session)
        assert pipeline.stages == []

    def test_cache_hit_within_ttl(self):
        """Cache should return cached results within the same time bucket."""
        from ui.data.sdlc import (
            _ARTIFACT_INFERENCE_TTL,
            _artifact_inference_cache,
            _get_artifact_enriched_stages,
        )

        _artifact_inference_cache.clear()

        cached_display = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "pending",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
            "MERGE": "pending",
        }
        time_bucket = int(time.time() / _ARTIFACT_INFERENCE_TTL)
        _artifact_inference_cache[("cached-slug", time_bucket)] = cached_display

        session = self._make_session(slug="cached-slug")

        with patch(
            "bridge.pipeline_state.PipelineStateMachine"
        ) as MockPSM:
            result = _get_artifact_enriched_stages(session, "cached-slug")

        # PipelineStateMachine should NOT be called -- cache hit
        MockPSM.assert_not_called()
        by_name = {s.name: s for s in result}
        assert by_name["ISSUE"].is_done
        assert by_name["PLAN"].is_done

    def test_cache_miss_after_ttl(self):
        """Cache should miss when the time bucket has changed."""
        from ui.data.sdlc import (
            _ARTIFACT_INFERENCE_TTL,
            _artifact_inference_cache,
            _get_artifact_enriched_stages,
        )

        _artifact_inference_cache.clear()

        # Insert with an old time bucket
        old_bucket = int(time.time() / _ARTIFACT_INFERENCE_TTL) - 5
        _artifact_inference_cache[("stale-slug", old_bucket)] = {"ISSUE": "completed"}

        session = self._make_session(slug="stale-slug")

        fresh_display = {s: "pending" for s in [
            "ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"
        ]}
        fresh_display["ISSUE"] = "in_progress"

        with patch(
            "bridge.pipeline_state.PipelineStateMachine"
        ) as MockPSM:
            MockPSM.return_value.get_display_progress.return_value = fresh_display
            result = _get_artifact_enriched_stages(session, "stale-slug")

        # Should have called PipelineStateMachine (cache miss)
        MockPSM.return_value.get_display_progress.assert_called_once_with(slug="stale-slug")
        by_name = {s.name: s for s in result}
        assert by_name["ISSUE"].is_active


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
