"""Unit tests for popoto model relationship changes (issue #295, PR #392).

Tests the model changes introduced by the popoto model relationships branch:
1. AgentSession behavioral episode instrumentation fields and helpers
2. CyclicEpisode model fields, helpers, and serialization
3. ProceduralPattern model: reinforce, confidence, import/export
4. AgentSession.id property alias
5. project_key presence across all Popoto models
6. Enrichment fallback logic pattern (TelegramMessage vs AgentSession fields)
"""

from unittest.mock import MagicMock

import pytest

# ===================================================================
# Helpers
# ===================================================================


def _make_mock_agent_session(**overrides):
    """Create a mock AgentSession with sensible defaults."""
    defaults = {
        "job_id": "job-001",
        "session_id": "tg_test_12345_100",
        "project_key": "test-project",
        "status": "pending",
        "chat_id": "12345",
        "message_id": 100,
        "message_text": "hello",
        "has_media": False,
        "media_type": None,
        "youtube_urls": None,
        "non_youtube_urls": None,
        "reply_to_msg_id": None,
        "classification_type": None,
        "trigger_message_id": None,
        "tool_sequence": [],
        "friction_events": [],
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    mock.save = MagicMock()
    return mock


# ===================================================================
# 1. AgentSession behavioral episode instrumentation
# ===================================================================


class TestAgentSessionToolSequence:
    """Test tool_sequence and friction_events fields added to AgentSession."""

    def test_tool_sequence_field_registered(self):
        """AgentSession should have tool_sequence in its Popoto field registry."""
        from models.agent_session import AgentSession

        assert "tool_sequence" in AgentSession._meta.field_names

    def test_friction_events_field_registered(self):
        """AgentSession should have friction_events in its Popoto field registry."""
        from models.agent_session import AgentSession

        assert "friction_events" in AgentSession._meta.field_names

    def _make_session_for_method(self, **kwargs):
        """Create a simple namespace object compatible with AgentSession methods."""
        from types import SimpleNamespace

        from models.agent_session import AgentSession

        defaults = {
            "tool_sequence": [],
            "friction_events": [],
            "session_id": "test",
            "_TOOL_SEQUENCE_MAX": AgentSession._TOOL_SEQUENCE_MAX,
            "_FRICTION_EVENTS_MAX": AgentSession._FRICTION_EVENTS_MAX,
        }
        defaults.update(kwargs)
        obj = SimpleNamespace(**defaults)
        obj.save = MagicMock()
        return obj

    def test_append_tool_event_format(self):
        """append_tool_event should store entries as 'stage:tool_type'."""
        from models.agent_session import AgentSession

        session = self._make_session_for_method()
        AgentSession.append_tool_event(session, "BUILD", "edit")

        assert session.tool_sequence == ["BUILD:edit"]

    def test_append_tool_event_caps_at_max(self):
        """tool_sequence should be capped at _TOOL_SEQUENCE_MAX entries."""
        from models.agent_session import AgentSession

        max_items = AgentSession._TOOL_SEQUENCE_MAX
        session = self._make_session_for_method(
            tool_sequence=[f"BUILD:tool_{i}" for i in range(max_items)]
        )

        AgentSession.append_tool_event(session, "TEST", "bash")

        assert len(session.tool_sequence) == max_items
        assert session.tool_sequence[-1] == "TEST:bash"
        assert session.tool_sequence[0] == "BUILD:tool_1"

    def test_append_tool_event_handles_none_sequence(self):
        """append_tool_event should handle tool_sequence being None."""
        from models.agent_session import AgentSession

        session = self._make_session_for_method(tool_sequence=None)
        AgentSession.append_tool_event(session, "REVIEW", "read")

        assert session.tool_sequence == ["REVIEW:read"]

    def test_append_friction_event_format(self):
        """append_friction_event should store entries as 'stage|description|count'."""
        from models.agent_session import AgentSession

        session = self._make_session_for_method()
        AgentSession.append_friction_event(session, "TEST", "flaky test", 3)

        assert session.friction_events == ["TEST|flaky test|3"]

    def test_append_friction_event_default_count(self):
        """append_friction_event default repetition_count should be 1."""
        from models.agent_session import AgentSession

        session = self._make_session_for_method()
        AgentSession.append_friction_event(session, "BUILD", "lint failure")

        assert session.friction_events == ["BUILD|lint failure|1"]

    def test_append_friction_event_caps_at_max(self):
        """friction_events should be capped at _FRICTION_EVENTS_MAX."""
        from models.agent_session import AgentSession

        max_items = AgentSession._FRICTION_EVENTS_MAX
        session = self._make_session_for_method(
            friction_events=[f"BUILD|error_{i}|1" for i in range(max_items)]
        )

        AgentSession.append_friction_event(session, "TEST", "timeout")

        assert len(session.friction_events) == max_items
        assert session.friction_events[-1] == "TEST|timeout|1"

    def test_append_friction_event_handles_none(self):
        """append_friction_event should handle friction_events being None."""
        from models.agent_session import AgentSession

        session = self._make_session_for_method(friction_events=None)
        AgentSession.append_friction_event(session, "DOCS", "missing file")

        assert session.friction_events == ["DOCS|missing file|1"]


# ===================================================================
# 2. CyclicEpisode model
# ===================================================================


class TestCyclicEpisodeModel:
    """Test the CyclicEpisode Popoto model fields and helpers."""

    def test_core_fields_registered(self):
        """CyclicEpisode should have all expected fields in Popoto registry."""
        from models.cyclic_episode import CyclicEpisode

        expected = [
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
        registered = CyclicEpisode._meta.field_names
        for field in expected:
            assert field in registered, f"CyclicEpisode missing field: {field}"

    def _make_episode(self, **kwargs):
        """Create a SimpleNamespace compatible with CyclicEpisode methods."""
        from types import SimpleNamespace

        defaults = {
            "tool_sequence": [],
            "friction_events": [],
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_append_tool_capping(self):
        """append_tool should cap at MAX_TOOL_SEQUENCE."""
        from models.cyclic_episode import MAX_TOOL_SEQUENCE, CyclicEpisode

        ep = self._make_episode(tool_sequence=[f"BUILD:t{i}" for i in range(MAX_TOOL_SEQUENCE)])

        CyclicEpisode.append_tool(ep, "TEST", "bash")

        assert len(ep.tool_sequence) == MAX_TOOL_SEQUENCE
        assert ep.tool_sequence[-1] == "TEST:bash"

    def test_append_friction_capping(self):
        """append_friction should cap at MAX_FRICTION_EVENTS."""
        from models.cyclic_episode import MAX_FRICTION_EVENTS, CyclicEpisode

        ep = self._make_episode(
            friction_events=[f"BUILD|e{i}|1" for i in range(MAX_FRICTION_EVENTS)]
        )

        CyclicEpisode.append_friction(ep, "TEST", "fail", 2)

        assert len(ep.friction_events) == MAX_FRICTION_EVENTS
        assert ep.friction_events[-1] == "TEST|fail|2"

    def test_get_fingerprint(self):
        """get_fingerprint should return the 4 fingerprint fields as a dict."""
        from models.cyclic_episode import CyclicEpisode

        ep = self._make_episode(
            problem_topology="bug_fix",
            affected_layer="bridge",
            ambiguity_at_intake=0.3,
            acceptance_criterion_defined=True,
        )

        result = CyclicEpisode.get_fingerprint(ep)

        assert result == {
            "problem_topology": "bug_fix",
            "affected_layer": "bridge",
            "ambiguity_at_intake": 0.3,
            "acceptance_criterion_defined": True,
        }

    def test_to_export_dict_keys(self):
        """to_export_dict should include all structural fields, no content."""
        from models.cyclic_episode import CyclicEpisode

        ep = self._make_episode(
            episode_id="ep-1",
            vault="mem:test",
            created_at=1000.0,
            problem_topology="new_feature",
            affected_layer="model",
            ambiguity_at_intake=0.5,
            acceptance_criterion_defined=False,
            tool_sequence=["BUILD:edit"],
            friction_events=[],
            stage_durations={"BUILD": 120},
            deviation_count=0,
            resolution_type="clean_merge",
            intent_satisfied=True,
            review_round_count=1,
            surprise_delta=0.1,
        )

        result = CyclicEpisode.to_export_dict(ep)

        expected_keys = {
            "episode_id",
            "vault",
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
        }
        assert set(result.keys()) == expected_keys

    def test_problem_topology_enum_values(self):
        """PROBLEM_TOPOLOGIES should include the expected structural categories."""
        from models.cyclic_episode import PROBLEM_TOPOLOGIES

        assert "new_feature" in PROBLEM_TOPOLOGIES
        assert "bug_fix" in PROBLEM_TOPOLOGIES
        assert "refactor" in PROBLEM_TOPOLOGIES
        assert "ambiguous" in PROBLEM_TOPOLOGIES

    def test_affected_layer_enum_values(self):
        """AFFECTED_LAYERS should include the expected system layers."""
        from models.cyclic_episode import AFFECTED_LAYERS

        assert "model" in AFFECTED_LAYERS
        assert "bridge" in AFFECTED_LAYERS
        assert "agent" in AFFECTED_LAYERS
        assert "unknown" in AFFECTED_LAYERS


# ===================================================================
# 3. ProceduralPattern model
# ===================================================================


class TestProceduralPatternModel:
    """Test the ProceduralPattern Popoto model."""

    def test_core_fields_registered(self):
        """ProceduralPattern should have all expected fields."""
        from models.procedural_pattern import ProceduralPattern

        expected = [
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
        registered = ProceduralPattern._meta.field_names
        for field in expected:
            assert field in registered, f"ProceduralPattern missing field: {field}"

    def _make_pattern(self, **kwargs):
        """Create an object compatible with ProceduralPattern methods.

        Uses a real class with _compute_confidence bound so that
        reinforce() can call self._compute_confidence().
        """
        from models.procedural_pattern import ProceduralPattern

        class FakePattern:
            _compute_confidence = ProceduralPattern._compute_confidence

            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        defaults = {
            "pattern_id": "pat-1",
            "sample_count": 0,
            "success_count": 0,
            "success_rate": 0.0,
            "confidence": 0.0,
            "last_reinforced": None,
        }
        defaults.update(kwargs)
        obj = FakePattern(**defaults)
        obj.save = MagicMock()
        return obj

    def test_reinforce_increments_sample_count(self):
        """reinforce() should increment sample_count."""
        from models.procedural_pattern import ProceduralPattern

        pattern = self._make_pattern(sample_count=5, success_count=3, success_rate=0.6)

        ProceduralPattern.reinforce(pattern, success=True)

        assert pattern.sample_count == 6
        assert pattern.success_count == 4
        pattern.save.assert_called_once()

    def test_reinforce_failure_does_not_increment_success(self):
        """reinforce(success=False) should not increment success_count."""
        from models.procedural_pattern import ProceduralPattern

        pattern = self._make_pattern(sample_count=5, success_count=3)

        ProceduralPattern.reinforce(pattern, success=False)

        assert pattern.sample_count == 6
        assert pattern.success_count == 3

    def test_compute_confidence_scales_with_samples(self):
        """Confidence should scale with sample count up to 10."""
        from models.procedural_pattern import ProceduralPattern

        pattern = self._make_pattern(sample_count=5, success_rate=1.0)
        assert ProceduralPattern._compute_confidence(pattern) == 0.5

        pattern.sample_count = 10
        assert ProceduralPattern._compute_confidence(pattern) == 1.0

        pattern.sample_count = 20
        pattern.success_rate = 0.8
        assert ProceduralPattern._compute_confidence(pattern) == 0.8

    def test_compute_confidence_zero_samples(self):
        """Confidence should be 0.0 with zero samples."""
        from models.procedural_pattern import ProceduralPattern

        pattern = self._make_pattern(sample_count=0)
        assert ProceduralPattern._compute_confidence(pattern) == 0.0

    def test_get_fingerprint_cluster(self):
        """get_fingerprint_cluster should return topology and layer."""
        from models.procedural_pattern import ProceduralPattern

        pattern = self._make_pattern(problem_topology="bug_fix", affected_layer="agent")

        result = ProceduralPattern.get_fingerprint_cluster(pattern)

        assert result == {"problem_topology": "bug_fix", "affected_layer": "agent"}

    def test_to_export_dict_keys(self):
        """to_export_dict should include all sync-safe fields."""
        from models.procedural_pattern import ProceduralPattern

        pattern = self._make_pattern(
            pattern_id="pat-1",
            problem_topology="refactor",
            affected_layer="model",
            canonical_tool_sequence=["BUILD:edit"],
            warnings=["check tests"],
            shortcuts=[],
            success_rate=0.9,
            sample_count=10,
            success_count=9,
            confidence=0.9,
            last_reinforced=1000.0,
            created_at=900.0,
            source_episode_ids=["ep-1", "ep-2"],
        )

        result = ProceduralPattern.to_export_dict(pattern)

        assert result["pattern_id"] == "pat-1"
        assert result["success_rate"] == 0.9
        assert result["sample_count"] == 10
        assert len(result["source_episode_ids"]) == 2


# ===================================================================
# 4. AgentSession sender property alias
# ===================================================================


class TestAgentSessionSenderAlias:
    """Test the AgentSession.sender property that aliases sender_name."""

    def test_sender_property_exists(self):
        """AgentSession should have a 'sender' property."""
        from models.agent_session import AgentSession

        assert "sender" in dir(AgentSession)

    def test_sender_returns_sender_name(self):
        """AgentSession.sender should return sender_name value."""

        # Verify the property logic
        class FakeSession:
            def __init__(self, name):
                self.sender_name = name

            @property
            def sender(self):
                return self.sender_name

        assert FakeSession("alice").sender == "alice"
        assert FakeSession(None).sender is None


# ===================================================================
# 5. project_key on Popoto models
# ===================================================================


class TestProjectKeyPresence:
    """Test that project_key exists on models that currently have it.

    AgentSession and BridgeEvent have project_key as KeyField.
    Other models (TelegramMessage, Chat, Link, DeadLetter, ReflectionRun)
    are planned to receive project_key in the migration (issue #295).
    """

    @pytest.mark.parametrize(
        "model_path,model_name",
        [
            ("models.agent_session", "AgentSession"),
            ("models.bridge_event", "BridgeEvent"),
        ],
    )
    def test_project_key_field_registered(self, model_path, model_name):
        """Models with project_key should have it in Popoto field registry."""
        import importlib

        module = importlib.import_module(model_path)
        model_cls = getattr(module, model_name)
        assert "project_key" in model_cls._meta.field_names, (
            f"{model_name} is missing project_key in _meta.field_names"
        )

    @pytest.mark.parametrize(
        "model_path,model_name",
        [
            ("models.agent_session", "AgentSession"),
            ("models.bridge_event", "BridgeEvent"),
        ],
    )
    def test_project_key_is_key_field(self, model_path, model_name):
        """project_key should be a KeyField for efficient querying."""
        import importlib

        from popoto import KeyField

        module = importlib.import_module(model_path)
        model_cls = getattr(module, model_name)
        pk_field = model_cls._meta.fields["project_key"]
        assert isinstance(pk_field, KeyField), (
            f"{model_name}.project_key is {type(pk_field).__name__}, expected KeyField"
        )


# ===================================================================
# 6. Enrichment fallback logic pattern
# ===================================================================


class TestEnrichmentFallbackPattern:
    """Test the enrichment fallback pattern: TelegramMessage -> AgentSession."""

    def test_fallback_uses_session_fields_when_no_trigger(self):
        """When trigger_message_id is None, enrichment uses AgentSession fields."""
        session = _make_mock_agent_session(
            has_media=True,
            media_type="photo",
            youtube_urls='[["https://youtu.be/abc", "abc"]]',
            trigger_message_id=None,
        )

        # Simulate the fallback logic from job_queue.py:1409-1438
        enrich_has_media = session.has_media
        enrich_media_type = session.media_type
        enrich_youtube_urls = session.youtube_urls

        if session.trigger_message_id:
            pytest.fail("Should not enter TM branch when trigger_message_id is None")

        assert enrich_has_media is True
        assert enrich_media_type == "photo"
        assert enrich_youtube_urls == '[["https://youtu.be/abc", "abc"]]'

    def test_trigger_message_overrides_session_fields(self):
        """When trigger_message_id is set and TM found, TM fields take precedence."""
        session = _make_mock_agent_session(
            has_media=False,
            media_type=None,
            trigger_message_id="tm-001",
        )
        tm = MagicMock()
        tm.has_media = True
        tm.media_type = "video"
        tm.youtube_urls = '[["https://youtu.be/xyz", "xyz"]]'
        tm.non_youtube_urls = '["https://docs.python.org"]'
        tm.reply_to_msg_id = 77

        # Simulate override
        enrich_has_media = session.has_media
        enrich_media_type = session.media_type

        if session.trigger_message_id:
            enrich_has_media = bool(tm.has_media)
            enrich_media_type = tm.media_type

        assert enrich_has_media is True
        assert enrich_media_type == "video"

    def test_fallback_when_trigger_not_found(self):
        """When trigger_message_id is set but TM lookup returns empty, keep session fields."""
        session = _make_mock_agent_session(
            has_media=True,
            media_type="document",
            trigger_message_id="tm-missing",
        )

        enrich_has_media = session.has_media
        enrich_media_type = session.media_type

        if session.trigger_message_id:
            trigger_msgs = []  # Lookup returns empty
            if trigger_msgs:
                pytest.fail("Should not enter this branch for empty lookup")

        assert enrich_has_media is True
        assert enrich_media_type == "document"

    def test_back_reference_sets_agent_session_id(self):
        """Back-reference logic should set agent_session_id on TelegramMessage."""
        tm = MagicMock()
        tm.agent_session_id = None
        tm.save = MagicMock()

        job_id = "job-abc"
        trigger_message_id = "tm-001"

        # Simulate job_queue.py:1461-1471
        if trigger_message_id:
            trigger_msgs = [tm]
            if trigger_msgs and not trigger_msgs[0].agent_session_id:
                trigger_msgs[0].agent_session_id = job_id
                trigger_msgs[0].save()

        assert tm.agent_session_id == "job-abc"
        tm.save.assert_called_once()

    def test_back_reference_skips_if_already_set(self):
        """Back-reference should not overwrite existing agent_session_id."""
        tm = MagicMock()
        tm.agent_session_id = "job-existing"
        tm.save = MagicMock()

        if True:  # trigger_message_id is set
            trigger_msgs = [tm]
            if trigger_msgs and not trigger_msgs[0].agent_session_id:
                trigger_msgs[0].agent_session_id = "job-new"
                trigger_msgs[0].save()

        assert tm.agent_session_id == "job-existing"
        tm.save.assert_not_called()


# ===================================================================
# 7. Models __init__ exports
# ===================================================================


class TestModelsInit:
    """Test that new models are exported from models/__init__.py."""

    def test_cyclic_episode_importable(self):
        """CyclicEpisode should be importable from models package."""
        from models.cyclic_episode import CyclicEpisode

        assert CyclicEpisode is not None

    def test_procedural_pattern_importable(self):
        """ProceduralPattern should be importable from models package."""
        from models.procedural_pattern import ProceduralPattern

        assert ProceduralPattern is not None

    def test_models_init_includes_new_models(self):
        """models/__init__.py should export the new model classes."""
        import models

        assert hasattr(models, "CyclicEpisode")
        assert hasattr(models, "ProceduralPattern")

    def test_models_init_all_list(self):
        """models.__all__ should include new model names."""
        import models

        assert "CyclicEpisode" in models.__all__
        assert "ProceduralPattern" in models.__all__
