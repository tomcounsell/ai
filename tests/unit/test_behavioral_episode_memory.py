"""Tests for the Behavioral Episode Memory System.

Covers:
- CyclicEpisode model CRUD and helpers
- ProceduralPattern model CRUD, reinforcement, and import/export
- AgentSession instrumentation fields (tool_sequence, friction_events)
- Fingerprint classifier (with mocked LLM responses)
- Pattern sync export/import
- Reflections cycle-close and pattern crystallization steps
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is in path
_project_root = str(Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# ============================================================================
# CyclicEpisode model tests
# ============================================================================


class TestCyclicEpisode:
    """Tests for CyclicEpisode model."""

    def test_import(self):
        """CyclicEpisode can be imported."""
        from models.cyclic_episode import CyclicEpisode

        assert CyclicEpisode is not None

    def test_field_definitions(self):
        """CyclicEpisode has all required fields."""
        from models.cyclic_episode import CyclicEpisode

        required_fields = [
            "episode_id",
            "vault",
            "raw_ref",
            "created_at",
            "problem_topology",
            "affected_layer",
            "ambiguity_at_intake",
            "acceptance_criterion_defined",
            "tool_sequence",
            "friction_events",
            "stage_durations",
            "deviation_count",
            "resolution_type",
            "intent_satisfied",
            "review_round_count",
            "surprise_delta",
            "issue_url",
            "branch_name",
            "session_summary",
        ]
        for field_name in required_fields:
            assert hasattr(CyclicEpisode, field_name), f"Missing field: {field_name}"

    def test_append_tool(self):
        """append_tool adds entries and caps at MAX_TOOL_SEQUENCE."""
        from models.cyclic_episode import CyclicEpisode, MAX_TOOL_SEQUENCE

        episode = CyclicEpisode.__new__(CyclicEpisode)
        episode.tool_sequence = None

        episode.append_tool("BUILD", "edit")
        assert episode.tool_sequence == ["BUILD:edit"]

        episode.append_tool("TEST", "bash")
        assert episode.tool_sequence == ["BUILD:edit", "TEST:bash"]

        # Test capping
        episode.tool_sequence = [f"s:{i}" for i in range(MAX_TOOL_SEQUENCE)]
        episode.append_tool("NEW", "tool")
        assert len(episode.tool_sequence) == MAX_TOOL_SEQUENCE
        assert episode.tool_sequence[-1] == "NEW:tool"

    def test_append_friction(self):
        """append_friction adds entries and caps at MAX_FRICTION_EVENTS."""
        from models.cyclic_episode import CyclicEpisode, MAX_FRICTION_EVENTS

        episode = CyclicEpisode.__new__(CyclicEpisode)
        episode.friction_events = None

        episode.append_friction("BUILD", "test_failure", 2)
        assert episode.friction_events == ["BUILD|test_failure|2"]

        # Test capping
        episode.friction_events = [f"s|f|{i}" for i in range(MAX_FRICTION_EVENTS)]
        episode.append_friction("NEW", "friction")
        assert len(episode.friction_events) == MAX_FRICTION_EVENTS
        assert episode.friction_events[-1] == "NEW|friction|1"

    def test_get_fingerprint(self):
        """get_fingerprint returns correct dict."""
        from models.cyclic_episode import CyclicEpisode

        episode = CyclicEpisode.__new__(CyclicEpisode)
        episode.problem_topology = "bug_fix"
        episode.affected_layer = "bridge"
        episode.ambiguity_at_intake = 0.3
        episode.acceptance_criterion_defined = True

        fp = episode.get_fingerprint()
        assert fp == {
            "problem_topology": "bug_fix",
            "affected_layer": "bridge",
            "ambiguity_at_intake": 0.3,
            "acceptance_criterion_defined": True,
        }

    def test_to_export_dict(self):
        """to_export_dict returns serializable dict without content."""
        from models.cyclic_episode import CyclicEpisode

        episode = CyclicEpisode.__new__(CyclicEpisode)
        episode.episode_id = "test-123"
        episode.vault = "mem:ai"
        episode.created_at = 1000.0
        episode.problem_topology = "new_feature"
        episode.affected_layer = "model"
        episode.ambiguity_at_intake = 0.2
        episode.acceptance_criterion_defined = True
        episode.tool_sequence = ["BUILD:edit"]
        episode.friction_events = []
        episode.stage_durations = {"BUILD": 120.0}
        episode.deviation_count = 0
        episode.resolution_type = "clean_merge"
        episode.intent_satisfied = True
        episode.review_round_count = 1
        episode.surprise_delta = 0.1

        export = episode.to_export_dict()
        assert export["episode_id"] == "test-123"
        assert export["problem_topology"] == "new_feature"
        assert "session_summary" not in export  # content stripped
        assert "issue_url" not in export  # content stripped

    def test_enums(self):
        """Enum lists are defined."""
        from models.cyclic_episode import (
            PROBLEM_TOPOLOGIES,
            AFFECTED_LAYERS,
            RESOLUTION_TYPES,
        )

        assert "new_feature" in PROBLEM_TOPOLOGIES
        assert "ambiguous" in PROBLEM_TOPOLOGIES
        assert "model" in AFFECTED_LAYERS
        assert "unknown" in AFFECTED_LAYERS
        assert "clean_merge" in RESOLUTION_TYPES


# ============================================================================
# ProceduralPattern model tests
# ============================================================================


class TestProceduralPattern:
    """Tests for ProceduralPattern model."""

    def test_import(self):
        """ProceduralPattern can be imported."""
        from models.procedural_pattern import ProceduralPattern

        assert ProceduralPattern is not None

    def test_field_definitions(self):
        """ProceduralPattern has all required fields."""
        from models.procedural_pattern import ProceduralPattern

        required_fields = [
            "pattern_id",
            "vault",
            "problem_topology",
            "affected_layer",
            "canonical_tool_sequence",
            "warnings",
            "shortcuts",
            "success_rate",
            "sample_count",
            "success_count",
            "confidence",
            "last_reinforced",
            "created_at",
            "source_episode_ids",
        ]
        for field_name in required_fields:
            assert hasattr(ProceduralPattern, field_name), f"Missing field: {field_name}"

    def test_compute_confidence(self):
        """_compute_confidence scales with sample count and success rate."""
        from models.procedural_pattern import ProceduralPattern

        pattern = ProceduralPattern.__new__(ProceduralPattern)

        # Zero samples
        pattern.sample_count = 0
        pattern.success_rate = 0.0
        assert pattern._compute_confidence() == 0.0

        # Low samples, high success
        pattern.sample_count = 3
        pattern.success_rate = 1.0
        conf = pattern._compute_confidence()
        assert 0.0 < conf < 1.0  # scaled down by sample factor

        # High samples, high success
        pattern.sample_count = 10
        pattern.success_rate = 1.0
        conf = pattern._compute_confidence()
        assert conf == 1.0

        # High samples, low success
        pattern.sample_count = 10
        pattern.success_rate = 0.5
        conf = pattern._compute_confidence()
        assert conf == 0.5

    def test_reinforce_success(self):
        """reinforce increments counters correctly on success."""
        from models.procedural_pattern import ProceduralPattern

        pattern = ProceduralPattern.__new__(ProceduralPattern)
        pattern.sample_count = 5
        pattern.success_count = 3
        pattern.success_rate = 0.6
        pattern.confidence = 0.3
        pattern.last_reinforced = 0

        with patch.object(pattern, "save"):
            pattern.reinforce(success=True)

        assert pattern.sample_count == 6
        assert pattern.success_count == 4
        assert abs(pattern.success_rate - 4 / 6) < 0.01

    def test_reinforce_failure(self):
        """reinforce increments sample_count but not success_count on failure."""
        from models.procedural_pattern import ProceduralPattern

        pattern = ProceduralPattern.__new__(ProceduralPattern)
        pattern.sample_count = 5
        pattern.success_count = 3
        pattern.success_rate = 0.6
        pattern.confidence = 0.3
        pattern.last_reinforced = 0

        with patch.object(pattern, "save"):
            pattern.reinforce(success=False)

        assert pattern.sample_count == 6
        assert pattern.success_count == 3
        assert abs(pattern.success_rate - 3 / 6) < 0.01

    def test_get_fingerprint_cluster(self):
        """get_fingerprint_cluster returns correct dict."""
        from models.procedural_pattern import ProceduralPattern

        pattern = ProceduralPattern.__new__(ProceduralPattern)
        pattern.problem_topology = "bug_fix"
        pattern.affected_layer = "bridge"

        cluster = pattern.get_fingerprint_cluster()
        assert cluster == {
            "problem_topology": "bug_fix",
            "affected_layer": "bridge",
        }

    def test_to_export_dict(self):
        """to_export_dict returns serializable dict."""
        from models.procedural_pattern import ProceduralPattern

        pattern = ProceduralPattern.__new__(ProceduralPattern)
        pattern.pattern_id = "pat-1"
        pattern.problem_topology = "new_feature"
        pattern.affected_layer = "model"
        pattern.canonical_tool_sequence = ["BUILD:edit", "TEST:bash"]
        pattern.warnings = ["watch out"]
        pattern.shortcuts = []
        pattern.success_rate = 0.8
        pattern.sample_count = 5
        pattern.success_count = 4
        pattern.confidence = 0.4
        pattern.last_reinforced = 1000.0
        pattern.created_at = 900.0
        pattern.source_episode_ids = ["ep-1", "ep-2"]

        export = pattern.to_export_dict()
        assert export["pattern_id"] == "pat-1"
        assert export["success_rate"] == 0.8
        assert export["canonical_tool_sequence"] == ["BUILD:edit", "TEST:bash"]
        # Should be JSON-serializable
        json.dumps(export)


# ============================================================================
# AgentSession instrumentation tests
# ============================================================================


class TestAgentSessionInstrumentation:
    """Tests for new AgentSession tool_sequence and friction_events fields."""

    def test_fields_exist(self):
        """AgentSession has the new instrumentation fields."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "tool_sequence")
        assert hasattr(AgentSession, "friction_events")

    def test_append_tool_event(self):
        """append_tool_event adds and caps entries."""
        from models.agent_session import AgentSession

        session = AgentSession.__new__(AgentSession)
        session.tool_sequence = None
        session.session_id = "test"

        with patch.object(session, "save"):
            session.append_tool_event("BUILD", "edit")

        assert session.tool_sequence == ["BUILD:edit"]

        with patch.object(session, "save"):
            session.append_tool_event("TEST", "bash")

        assert session.tool_sequence == ["BUILD:edit", "TEST:bash"]

    def test_append_tool_event_capping(self):
        """append_tool_event caps at _TOOL_SEQUENCE_MAX."""
        from models.agent_session import AgentSession

        session = AgentSession.__new__(AgentSession)
        session.tool_sequence = [f"s:{i}" for i in range(AgentSession._TOOL_SEQUENCE_MAX)]
        session.session_id = "test"

        with patch.object(session, "save"):
            session.append_tool_event("NEW", "tool")

        assert len(session.tool_sequence) == AgentSession._TOOL_SEQUENCE_MAX
        assert session.tool_sequence[-1] == "NEW:tool"

    def test_append_friction_event(self):
        """append_friction_event adds and caps entries."""
        from models.agent_session import AgentSession

        session = AgentSession.__new__(AgentSession)
        session.friction_events = None
        session.session_id = "test"

        with patch.object(session, "save"):
            session.append_friction_event("BUILD", "test_failure", 2)

        assert session.friction_events == ["BUILD|test_failure|2"]

    def test_append_friction_event_capping(self):
        """append_friction_event caps at _FRICTION_EVENTS_MAX."""
        from models.agent_session import AgentSession

        session = AgentSession.__new__(AgentSession)
        session.friction_events = [f"s|f|{i}" for i in range(AgentSession._FRICTION_EVENTS_MAX)]
        session.session_id = "test"

        with patch.object(session, "save"):
            session.append_friction_event("NEW", "friction")

        assert len(session.friction_events) == AgentSession._FRICTION_EVENTS_MAX
        assert session.friction_events[-1] == "NEW|friction|1"

    def test_backward_compatibility(self):
        """New fields default to None, not breaking existing sessions."""
        from models.agent_session import AgentSession

        session = AgentSession.__new__(AgentSession)
        # These should not raise
        ts = session.tool_sequence if hasattr(session, "tool_sequence") else None
        fe = session.friction_events if hasattr(session, "friction_events") else None
        # They should be accessible
        assert hasattr(session, "tool_sequence")
        assert hasattr(session, "friction_events")


# ============================================================================
# Fingerprint classifier tests
# ============================================================================


class TestFingerprintClassifier:
    """Tests for the fingerprint classifier."""

    def test_import(self):
        """Classifier can be imported."""
        from scripts.fingerprint_classifier import classify_fingerprint

        assert callable(classify_fingerprint)

    def test_default_fingerprint_on_missing_key(self):
        """Returns default fingerprint when ANTHROPIC_API_KEY is missing."""
        from scripts.fingerprint_classifier import classify_fingerprint, DEFAULT_FINGERPRINT

        with patch.dict(os.environ, {}, clear=True):
            # Remove ANTHROPIC_API_KEY if present
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                result = classify_fingerprint(summary="test")
                assert result == DEFAULT_FINGERPRINT

    def test_valid_llm_response(self):
        """Correctly parses a valid LLM response."""
        from scripts.fingerprint_classifier import classify_fingerprint

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "problem_topology": "bug_fix",
                        "affected_layer": "bridge",
                        "ambiguity_at_intake": 0.3,
                        "acceptance_criterion_defined": True,
                    }
                )
            )
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("scripts.fingerprint_classifier.anthropic") as mock_anthropic:
                mock_anthropic.Anthropic.return_value = mock_client
                result = classify_fingerprint(summary="Fix bridge crash on startup")

        assert result["problem_topology"] == "bug_fix"
        assert result["affected_layer"] == "bridge"
        assert result["ambiguity_at_intake"] == 0.3
        assert result["acceptance_criterion_defined"] is True

    def test_malformed_json_returns_default(self):
        """Returns default fingerprint on malformed JSON."""
        from scripts.fingerprint_classifier import classify_fingerprint, DEFAULT_FINGERPRINT

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json {{{")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("scripts.fingerprint_classifier.anthropic") as mock_anthropic:
                mock_anthropic.Anthropic.return_value = mock_client
                result = classify_fingerprint(summary="test")

        assert result == DEFAULT_FINGERPRINT

    def test_api_error_returns_default(self):
        """Returns default fingerprint on API error."""
        from scripts.fingerprint_classifier import classify_fingerprint, DEFAULT_FINGERPRINT

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("scripts.fingerprint_classifier.anthropic") as mock_anthropic:
                mock_anthropic.Anthropic.return_value = mock_client
                result = classify_fingerprint(summary="test")

        assert result == DEFAULT_FINGERPRINT

    def test_invalid_topology_normalized(self):
        """Invalid topology values are normalized to 'ambiguous'."""
        from scripts.fingerprint_classifier import classify_fingerprint

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "problem_topology": "invalid_value",
                        "affected_layer": "model",
                        "ambiguity_at_intake": 0.5,
                        "acceptance_criterion_defined": False,
                    }
                )
            )
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("scripts.fingerprint_classifier.anthropic") as mock_anthropic:
                mock_anthropic.Anthropic.return_value = mock_client
                result = classify_fingerprint(summary="test")

        assert result["problem_topology"] == "ambiguous"

    def test_ambiguity_clamped(self):
        """ambiguity_at_intake is clamped to [0.0, 1.0]."""
        from scripts.fingerprint_classifier import classify_fingerprint

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "problem_topology": "new_feature",
                        "affected_layer": "model",
                        "ambiguity_at_intake": 5.0,  # out of range
                        "acceptance_criterion_defined": True,
                    }
                )
            )
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("scripts.fingerprint_classifier.anthropic") as mock_anthropic:
                mock_anthropic.Anthropic.return_value = mock_client
                result = classify_fingerprint(summary="test")

        assert result["ambiguity_at_intake"] == 1.0

    def test_classify_session_convenience(self):
        """classify_session wraps classify_fingerprint for AgentSession objects."""
        from scripts.fingerprint_classifier import classify_session, DEFAULT_FINGERPRINT

        session = MagicMock()
        session.summary = "test summary"
        session.issue_url = "https://github.com/test/1"
        session.branch_name = "feature/test"
        session.tool_sequence = ["BUILD:edit"]
        session.friction_events = []
        session.tags = ["sdlc"]

        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                result = classify_session(session)
                assert result == DEFAULT_FINGERPRINT


# ============================================================================
# Pattern sync tests
# ============================================================================


class TestPatternSync:
    """Tests for pattern export/import."""

    def test_import(self):
        """Pattern sync module can be imported."""
        from scripts.pattern_sync import export_shared_patterns, import_shared_patterns

        assert callable(export_shared_patterns)
        assert callable(import_shared_patterns)

    def test_import_nonexistent_dir(self):
        """Import from nonexistent directory returns 0."""
        from scripts.pattern_sync import import_shared_patterns

        result = import_shared_patterns(source_dir=Path("/nonexistent/dir"))
        assert result == 0

    def test_import_empty_dir(self):
        """Import from empty directory returns 0."""
        from scripts.pattern_sync import import_shared_patterns

        with tempfile.TemporaryDirectory() as tmpdir:
            result = import_shared_patterns(source_dir=Path(tmpdir))
            assert result == 0

    def test_import_invalid_json(self):
        """Import handles invalid JSON gracefully."""
        from scripts.pattern_sync import import_shared_patterns

        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "patterns_othermachine.json"
            bad_file.write_text("not json {{{")

            result = import_shared_patterns(source_dir=Path(tmpdir))
            assert result == 0

    def test_get_machine_id(self):
        """_get_machine_id returns a string."""
        from scripts.pattern_sync import _get_machine_id

        mid = _get_machine_id()
        assert isinstance(mid, str)
        assert len(mid) > 0


# ============================================================================
# Reflections integration tests (mocked)
# ============================================================================


class TestReflectionsCycleClose:
    """Tests for the episode cycle-close Reflections step."""

    def test_step_exists_in_steps_list(self):
        """step_episode_cycle_close is registered as step 16."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner.__new__(ReflectionRunner)
        runner.state = MagicMock()
        runner.projects = []
        runner.steps = [
            (1, "Clean Up Legacy Code", None),
        ]
        # Re-init to get actual steps
        with patch("scripts.reflections.ReflectionRunner._load_state", return_value=MagicMock()):
            with patch("scripts.reflections.load_local_projects", return_value=[]):
                runner2 = ReflectionRunner()

        step_names = {name for _, name, _ in runner2.steps}
        assert "Episode Cycle-Close" in step_names
        assert "Pattern Crystallization" in step_names

        # Check step numbers
        step_nums = {name: num for num, name, _ in runner2.steps}
        assert step_nums["Episode Cycle-Close"] == 16
        assert step_nums["Pattern Crystallization"] == 17


class TestReflectionsPatternCrystallization:
    """Tests for the pattern crystallization Reflections step."""

    def test_crystallization_threshold(self):
        """Pattern crystallization requires 3+ episodes."""
        # This is a design validation test - the threshold is defined
        # in step_pattern_crystallization
        from scripts.reflections import ReflectionRunner

        # Read the source to verify threshold
        import inspect

        source = inspect.getsource(ReflectionRunner.step_pattern_crystallization)
        assert "CRYSTALLIZATION_THRESHOLD = 3" in source


# ============================================================================
# Models __init__ integration test
# ============================================================================


class TestModelsInit:
    """Tests for models package exports."""

    def test_new_models_exported(self):
        """CyclicEpisode and ProceduralPattern are exported from models."""
        from models import CyclicEpisode, ProceduralPattern

        assert CyclicEpisode is not None
        assert ProceduralPattern is not None

    def test_all_list(self):
        """__all__ includes new models."""
        from models import __all__

        assert "CyclicEpisode" in __all__
        assert "ProceduralPattern" in __all__
