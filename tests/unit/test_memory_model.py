"""Unit tests for the Memory model and config defaults."""


class TestMemoryDefaults:
    """Test config/memory_defaults.py apply_defaults()."""

    def test_apply_defaults_sets_decay_rate(self):
        from popoto import Defaults

        from config.memory_defaults import MEMORY_DECAY_RATE, apply_defaults

        apply_defaults()
        assert Defaults.DECAY_RATE == MEMORY_DECAY_RATE

    def test_apply_defaults_sets_write_filter(self):
        from popoto import Defaults

        from config.memory_defaults import MEMORY_WF_MIN_THRESHOLD, apply_defaults

        apply_defaults()
        assert Defaults.WF_MIN_THRESHOLD == MEMORY_WF_MIN_THRESHOLD

    def test_apply_defaults_idempotent(self):
        from popoto import Defaults

        from config.memory_defaults import apply_defaults

        apply_defaults()
        rate1 = Defaults.DECAY_RATE
        apply_defaults()
        assert Defaults.DECAY_RATE == rate1


class TestMemoryModel:
    """Test models/memory.py Memory model."""

    def test_import_memory(self):
        from models.memory import Memory

        assert Memory is not None

    def test_memory_has_required_fields(self):
        from models.memory import Memory

        field_names = set(Memory._meta.fields.keys())
        required = {"memory_id", "agent_id", "project_key", "content", "importance", "source"}
        assert required.issubset(field_names), f"Missing fields: {required - field_names}"

    def test_memory_has_bloom_filter(self):
        from popoto.fields.existence_filter import ExistenceFilter

        from models.memory import Memory

        bloom = Memory._meta.fields.get("bloom")
        assert bloom is not None
        assert isinstance(bloom, ExistenceFilter)

    def test_memory_create_and_save(self):
        from models.memory import Memory

        m = Memory(
            agent_id="test-agent",
            project_key="test-project",
            content="Test memory content for unit testing",
            importance=2.0,
            source="human",
        )
        result = m.save()
        # save() returns None on success for popoto models, or False if filtered
        assert result is not False

    def test_memory_write_filter_rejects_low_importance(self):
        from models.memory import Memory

        m = Memory(
            agent_id="test-agent",
            project_key="test-project",
            content="Low importance noise",
            importance=0.05,  # below _wf_min_threshold of 0.15
            source="agent",
        )
        result = m.save()
        assert result is False

    def test_memory_safe_save_success(self):
        from models.memory import Memory

        m = Memory.safe_save(
            agent_id="test-agent",
            project_key="test-project",
            content="Safe save test memory",
            importance=1.0,
            source="agent",
        )
        assert m is not None

    def test_memory_safe_save_filtered(self):
        from models.memory import Memory

        m = Memory.safe_save(
            agent_id="test-agent",
            project_key="test-project",
            content="Filtered memory",
            importance=0.01,
            source="agent",
        )
        assert m is None

    def test_memory_query_by_project(self):
        from models.memory import Memory

        # Save a memory
        Memory.safe_save(
            agent_id="test-agent",
            project_key="test-query-project",
            content="Queryable memory for testing",
            importance=3.0,
            source="human",
        )

        # Query by project_key
        results = Memory.query.filter(project_key="test-query-project")
        assert len(results) > 0

    def test_memory_compute_filter_score(self):
        from models.memory import Memory

        m = Memory(
            agent_id="test",
            project_key="test",
            content="test",
            importance=5.0,
        )
        assert m.compute_filter_score() == 5.0

    def test_memory_compute_filter_score_none(self):
        from models.memory import Memory

        m = Memory(
            agent_id="test",
            project_key="test",
            content="test",
            importance=None,
        )
        assert m.compute_filter_score() == 0.0

    def test_memory_from_models_init(self):
        """Verify Memory is exported from models package."""
        from models import Memory

        assert Memory is not None
