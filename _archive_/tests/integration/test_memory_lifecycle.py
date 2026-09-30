"""Integration test: full Memory embedding-file lifecycle (#1214).

Asserts:

1. Saving a Memory record writes a SHA-256-hashed ``.npy`` file at
   ``EmbeddingField._embedding_path(...)``.
2. Deleting the Memory record removes the ``.npy`` (immediate cleanup
   path via ``EmbeddingField.on_delete``).
3. Manually dropping a stray ``.npy`` with a non-live SHA-256 name and
   running ``EmbeddingField.garbage_collect(Memory)`` removes the stray.
4. A fresh ``.npy`` (mtime newer than the 5-minute guard) survives
   ``garbage_collect`` — race protection for in-flight saves.
5. A ``tmp123.npy`` older than 1 hour is removed by
   ``EmbeddingField.sweep_stale_tempfiles(Memory)``; a fresh
   ``tmp123.npy`` is kept.

These tests use the autouse ``redis_test_db`` fixture (per-worker
isolated Redis db, flushed between tests). Embeddings are wired to a
deterministic mock provider so we don't depend on Ollama being live.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = [pytest.mark.integration]

np = pytest.importorskip("numpy")


def _get_emb_dir(model_name: str = "Memory") -> str:
    from popoto.fields.embedding_field import _get_embeddings_dir

    return os.path.join(_get_embeddings_dir(), model_name)


def _make_npy(path: str, mtime_offset_seconds: float = 0) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.save(path, np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float32))
    if mtime_offset_seconds:
        old = time.time() - mtime_offset_seconds
        os.utime(path, (old, old))


@pytest.fixture
def deterministic_provider():
    """Wire EmbeddingField to a deterministic mock provider for the test.

    Avoids dependence on Ollama being running. Restores the prior provider
    on teardown.
    """
    from popoto.embeddings import AbstractEmbeddingProvider
    from popoto.fields.embedding_field import (
        get_default_provider,
        invalidate_cache,
        set_default_provider,
    )

    class _MockProvider(AbstractEmbeddingProvider):
        def embed(self, texts, input_type=None):
            return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

        @property
        def dimensions(self):
            return 4

        @property
        def max_batch_size(self):
            return 32

    prior = get_default_provider()
    set_default_provider(_MockProvider())
    invalidate_cache()
    try:
        yield
    finally:
        set_default_provider(prior)
        invalidate_cache()


@pytest.fixture
def isolated_project_key():
    """Per-test project_key for clean isolation."""
    key = f"lifecycle-test-{uuid.uuid4().hex[:8]}"
    yield key
    # Best-effort cleanup of any leftover Memory rows
    try:
        from models.memory import Memory

        for record in Memory.query.filter(project_key=key):
            try:
                record.delete()
            except Exception:
                pass
    except Exception:
        pass


class TestMemoryLifecycle:
    def test_save_writes_sha256_npy(self, deterministic_provider, isolated_project_key):
        from popoto.fields.embedding_field import EmbeddingField

        from models.memory import SOURCE_HUMAN, Memory

        rec = Memory.safe_save(
            agent_id="lifecycle-test",
            project_key=isolated_project_key,
            content="lifecycle save smoke test " + uuid.uuid4().hex,
            importance=6.0,
            source=SOURCE_HUMAN,
        )
        if rec is None:
            pytest.skip("Memory.safe_save returned None (bloom dedup)")

        try:
            redis_key = rec._redis_key or rec.db_key.redis_key
            npy_path = EmbeddingField._embedding_path("Memory", redis_key)
            assert os.path.exists(npy_path), f".npy must be written on save: {npy_path}"
            # Format check: 64 hex chars + .npy
            filename = os.path.basename(npy_path)
            assert len(filename) == 68
            assert filename.endswith(".npy")
            assert all(c in "0123456789abcdef" for c in filename[:-4])
        finally:
            try:
                rec.delete()
            except Exception:
                pass

    def test_delete_removes_npy(self, deterministic_provider, isolated_project_key):
        from popoto.fields.embedding_field import EmbeddingField

        from models.memory import SOURCE_HUMAN, Memory

        rec = Memory.safe_save(
            agent_id="lifecycle-delete",
            project_key=isolated_project_key,
            content="lifecycle delete smoke test " + uuid.uuid4().hex,
            importance=6.0,
            source=SOURCE_HUMAN,
        )
        if rec is None:
            pytest.skip("Memory.safe_save returned None (bloom dedup)")

        redis_key = rec._redis_key or rec.db_key.redis_key
        npy_path = EmbeddingField._embedding_path("Memory", redis_key)
        assert os.path.exists(npy_path)

        rec.delete()

        assert not os.path.exists(npy_path), (
            ".npy must be removed by Memory.delete() (on_delete hook)"
        )

    def test_garbage_collect_removes_stray(self, deterministic_provider, isolated_project_key):
        from popoto.fields.embedding_field import EmbeddingField

        from models.memory import SOURCE_HUMAN, Memory

        # Seed one live record so $Class:Memory is non-empty
        # (garbage_collect refuses to act when expected_keep is empty)
        live = Memory.safe_save(
            agent_id="lifecycle-gc",
            project_key=isolated_project_key,
            content="gc lifecycle " + uuid.uuid4().hex,
            importance=6.0,
            source=SOURCE_HUMAN,
        )
        if live is None:
            pytest.skip("Memory.safe_save returned None (bloom dedup)")

        try:
            emb_dir = _get_emb_dir("Memory")
            stray_path = os.path.join(emb_dir, "f" * 64 + ".npy")
            _make_npy(stray_path, mtime_offset_seconds=99999)

            removed = EmbeddingField.garbage_collect(Memory)

            assert removed >= 1
            assert not os.path.exists(stray_path), (
                "garbage_collect must remove the stray orphan file"
            )

            # Live record's .npy must still exist
            redis_key = live._redis_key or live.db_key.redis_key
            live_path = EmbeddingField._embedding_path("Memory", redis_key)
            assert os.path.exists(live_path), "live record .npy must be preserved"
        finally:
            try:
                live.delete()
            except Exception:
                pass

    def test_garbage_collect_mtime_guard_protects_fresh(
        self, deterministic_provider, isolated_project_key
    ):
        from popoto.fields.embedding_field import EmbeddingField

        from models.memory import SOURCE_HUMAN, Memory

        live = Memory.safe_save(
            agent_id="lifecycle-mtime",
            project_key=isolated_project_key,
            content="mtime lifecycle " + uuid.uuid4().hex,
            importance=6.0,
            source=SOURCE_HUMAN,
        )
        if live is None:
            pytest.skip("Memory.safe_save returned None (bloom dedup)")

        try:
            emb_dir = _get_emb_dir("Memory")
            fresh = os.path.join(emb_dir, "9" * 64 + ".npy")
            _make_npy(fresh, mtime_offset_seconds=10)  # 10s — well within 300s guard

            EmbeddingField.garbage_collect(Memory, min_age_seconds=300)

            assert os.path.exists(fresh), (
                "fresh orphan must survive the 5-minute mtime guard "
                "(race protection for in-flight saves)"
            )
            # Cleanup
            try:
                os.unlink(fresh)
            except OSError:
                pass
        finally:
            try:
                live.delete()
            except Exception:
                pass

    def test_sweep_stale_tempfiles_removes_old_keeps_recent(
        self, deterministic_provider, isolated_project_key
    ):
        from popoto.fields.embedding_field import EmbeddingField

        emb_dir = _get_emb_dir("Memory")
        os.makedirs(emb_dir, exist_ok=True)

        old_tmp = os.path.join(emb_dir, "tmpOLDLIFE.npy")
        new_tmp = os.path.join(emb_dir, "tmpNEWLIFE.npy")
        _make_npy(old_tmp, mtime_offset_seconds=7200)  # 2hr — beyond 1hr cutoff
        _make_npy(new_tmp, mtime_offset_seconds=10)  # 10s — fresh

        try:
            removed = EmbeddingField.sweep_stale_tempfiles(
                __import__("models.memory", fromlist=["Memory"]).Memory,
                max_age_seconds=3600,
            )
            assert removed >= 1
            assert not os.path.exists(old_tmp), "stale tempfile must be removed"
            assert os.path.exists(new_tmp), "fresh tempfile must be kept"
        finally:
            for path in (old_tmp, new_tmp):
                try:
                    os.unlink(path)
                except OSError:
                    pass
