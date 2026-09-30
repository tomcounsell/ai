"""Unit tests for models/graceful_embedding_field.py (issue #1904).

The GracefulEmbeddingField must let a Memory record persist when the embedding
provider fails, instead of the failure aborting the whole save (the original bug:
a provider timeout dropped the entire record — content, BM25 index, relevance —
not merely the vector).

These tests use real Redis (a plan prerequisite). They stub the global embedding
provider to raise, then assert the record still persists with embedding=None, is
retrievable via the other RRF signals, and that the degradation is observable
(counter increments; a warning is emitted, throttled).
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def restore_provider():
    """Save/restore the global embedding provider and reset degradation state."""
    from popoto.fields.embedding_field import get_default_provider, set_default_provider

    from models.graceful_embedding_field import reset_degradation_state

    original = get_default_provider()
    reset_degradation_state()
    try:
        yield set_default_provider
    finally:
        set_default_provider(original)
        reset_degradation_state()


class _RaisingProvider:
    """Provider stub that mimics an Ollama timeout on every embed call."""

    dimensions = 768

    def embed(self, texts, input_type="document"):
        raise RuntimeError("stubbed Ollama timeout after 5.0s")

    def is_available(self):
        return False


class _WrongDimProvider:
    """Provider stub that returns a vector of the wrong dimensionality."""

    dimensions = 768

    def embed(self, texts, input_type="document"):
        return [[0.1, 0.2, 0.3]]  # 3 dims, provider claims 768 -> parent raises ValueError

    def is_available(self):
        return True


def _make_memory(content: str, project_key: str):
    from models.memory import Memory

    return Memory(
        agent_id="test-agent",
        project_key=project_key,
        content=content,
        importance=6.0,  # human — well above the WriteFilter floor
        source="human",
    )


class TestGracefulEmbeddingField:
    def test_provider_timeout_persists_record_without_vector(self, restore_provider):
        """A RuntimeError from the provider must NOT drop the record (core AC1)."""
        from models.memory import Memory

        restore_provider(_RaisingProvider())

        project_key = f"test-graceful-{uuid.uuid4().hex[:8]}"
        content = f"Graceful degradation content {uuid.uuid4().hex}"
        m = _make_memory(content, project_key)

        result = m.save()
        assert result is not False  # not filtered out

        # The record persisted and is queryable.
        found = Memory.query.filter(project_key=project_key)
        assert len(found) == 1, "record must persist despite embedding failure"
        assert found[0].content == content
        # No vector: embedding stays at the field default (None / falsy).
        assert not found[0].embedding, "embedding must be absent on the degraded path"

        for r in found:
            r.delete()

    def test_dimension_mismatch_persists_record(self, restore_provider):
        """A ValueError (dimension mismatch) is also caught — record persists."""
        from models.memory import Memory

        restore_provider(_WrongDimProvider())

        project_key = f"test-graceful-dim-{uuid.uuid4().hex[:8]}"
        m = _make_memory(f"dim mismatch {uuid.uuid4().hex}", project_key)
        m.save()

        found = Memory.query.filter(project_key=project_key)
        assert len(found) == 1
        assert not found[0].embedding
        for r in found:
            r.delete()

    def test_oserror_from_write_is_caught(self, restore_provider, monkeypatch):
        """A post-embed OSError (atomic .npy write) must not drop the record either.

        Broadening the catch to include OSError is critique-mandated: an OSError
        during the parent's atomic write would otherwise still abort the save.
        """
        from popoto.fields.embedding_field import EmbeddingField

        from models.graceful_embedding_field import (
            GracefulEmbeddingField,
            get_degradation_count,
        )

        def _raise_oserror(cls, *args, **kwargs):
            raise OSError("disk full during .npy write")

        monkeypatch.setattr(EmbeddingField, "on_save", classmethod(_raise_oserror))

        sentinel_pipeline = object()
        returned = GracefulEmbeddingField.on_save(
            model_instance=_make_memory("os error", "test-os"),
            field_name="embedding",
            field_value=None,
            pipeline=sentinel_pipeline,
        )
        # The pipeline (carrying the queued main hset) is returned intact, so
        # Model.save can still commit the record.
        assert returned is sentinel_pipeline
        assert get_degradation_count() == 1

    def test_missing_embedding_record_is_retrievable(self, restore_provider):
        """AC2 regression: a record with no vector is still recalled via BM25/relevance."""
        from agent.memory_retrieval import retrieve_memories
        from models.memory import Memory

        restore_provider(_RaisingProvider())

        project_key = f"test-recall-{uuid.uuid4().hex[:8]}"
        token = f"quokka{uuid.uuid4().hex[:8]}"
        content = f"The {token} deployment finished successfully"
        _make_memory(content, project_key).save()

        results = retrieve_memories(token, project_key, limit=10)
        assert any(getattr(r, "content", "") == content for r in results), (
            "vectorless record must be retrievable via non-embedding RRF signals"
        )

        for r in Memory.query.filter(project_key=project_key):
            r.delete()

    def test_degradation_counter_increments_per_failed_save(self, restore_provider):
        """The counter increments on every degraded save (surfaced in the backfill summary)."""
        from models.graceful_embedding_field import get_degradation_count

        restore_provider(_RaisingProvider())

        from models.memory import Memory

        project_key = f"test-counter-{uuid.uuid4().hex[:8]}"
        assert get_degradation_count() == 0
        _make_memory(f"c1 {uuid.uuid4().hex}", project_key).save()
        _make_memory(f"c2 {uuid.uuid4().hex}", project_key).save()
        assert get_degradation_count() == 2

        for r in Memory.query.filter(project_key=project_key):
            r.delete()

    def test_warning_emitted_but_throttled(self, restore_provider, caplog):
        """First degradation warns; a rapid second is throttled (no log flood, critique C2)."""
        import logging

        restore_provider(_RaisingProvider())

        from models.memory import Memory

        project_key = f"test-warn-{uuid.uuid4().hex[:8]}"
        with caplog.at_level(logging.WARNING, logger="models.graceful_embedding_field"):
            _make_memory(f"w1 {uuid.uuid4().hex}", project_key).save()
            _make_memory(f"w2 {uuid.uuid4().hex}", project_key).save()

        degrade_warnings = [
            rec
            for rec in caplog.records
            if rec.name == "models.graceful_embedding_field"
            and "Embedding degraded" in rec.getMessage()
        ]
        assert len(degrade_warnings) == 1, "warning must fire once and then be throttled"

        for r in Memory.query.filter(project_key=project_key):
            r.delete()
