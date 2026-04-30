"""Tests for Popoto index cleanup reflection."""

import os
import tempfile
from unittest.mock import MagicMock, patch

from scripts.popoto_index_cleanup import (
    _count_disk_orphans,
    _count_orphans,
    _get_all_models,
    run_cleanup,
)


class TestGetAllModels:
    def test_returns_models_with_rebuild_indexes(self):
        models = _get_all_models()
        assert isinstance(models, list)
        # All returned models should have rebuild_indexes method
        for model in models:
            assert hasattr(model, "rebuild_indexes")

    def test_handles_import_error(self):
        with patch("scripts.popoto_index_cleanup.logger"):
            with patch.dict("sys.modules", {"models": None}):
                # Import error should return empty list, not crash
                result = _get_all_models()
                assert result == [] or isinstance(result, list)


class TestCountOrphans:
    def test_returns_zero_for_empty_set(self):
        mock_model = MagicMock()
        mock_model.__name__ = "TestModel"

        with patch("scripts.popoto_index_cleanup.POPOTO_REDIS_DB", create=True) as mock_db:
            mock_db.smembers.return_value = set()
            # Need to patch the import inside the function
            with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_db):
                result = _count_orphans(mock_model)
                assert result == 0

    def test_counts_missing_hashes(self):
        mock_model = MagicMock()
        mock_model.__name__ = "TestModel"

        mock_db = MagicMock()
        mock_db.smembers.return_value = {b"TestModel:1", b"TestModel:2", b"TestModel:3"}
        # Key 2 does not exist (exists returns 0/False)
        mock_db.exists.side_effect = lambda k: (
            0 if (k.decode() if isinstance(k, bytes) else k) == "TestModel:2" else 1
        )

        # Patch at the import target within the function
        import popoto.redis_db

        original = popoto.redis_db.POPOTO_REDIS_DB
        try:
            popoto.redis_db.POPOTO_REDIS_DB = mock_db
            result = _count_orphans(mock_model)
            assert result == 1
        finally:
            popoto.redis_db.POPOTO_REDIS_DB = original


class TestRunCleanup:
    def test_no_models_returns_no_models_status(self):
        with patch("scripts.popoto_index_cleanup._get_all_models", return_value=[]):
            result = run_cleanup()
            assert result["status"] == "no_models"
            assert result["models_processed"] == 0

    def test_processes_all_models(self):
        mock_model_a = MagicMock()
        mock_model_a.__name__ = "ModelA"
        mock_model_a.rebuild_indexes.return_value = 5

        mock_model_b = MagicMock()
        mock_model_b.__name__ = "ModelB"
        mock_model_b.rebuild_indexes.return_value = 3

        with (
            patch(
                "scripts.popoto_index_cleanup._get_all_models",
                return_value=[mock_model_a, mock_model_b],
            ),
            patch("scripts.popoto_index_cleanup._count_orphans", return_value=0),
        ):
            result = run_cleanup()
            assert result["status"] == "completed"
            assert result["models_processed"] == 2
            assert result["total_records_rebuilt"] == 8
            mock_model_a.rebuild_indexes.assert_called_once()
            mock_model_b.rebuild_indexes.assert_called_once()

    def test_one_model_error_does_not_abort(self):
        mock_model_ok = MagicMock()
        mock_model_ok.__name__ = "ModelOK"
        mock_model_ok.rebuild_indexes.return_value = 2

        mock_model_bad = MagicMock()
        mock_model_bad.__name__ = "ModelBad"
        mock_model_bad.rebuild_indexes.side_effect = RuntimeError("Redis down")

        with (
            patch(
                "scripts.popoto_index_cleanup._get_all_models",
                return_value=[mock_model_ok, mock_model_bad],
            ),
            patch("scripts.popoto_index_cleanup._count_orphans", return_value=0),
        ):
            result = run_cleanup()
            assert result["status"] == "completed"
            assert result["models_processed"] == 2
            assert len(result["errors"]) == 1
            assert result["per_model"]["ModelOK"]["status"] == "ok"
            assert result["per_model"]["ModelBad"]["status"] == "error"

    def test_reports_orphan_counts(self):
        mock_model = MagicMock()
        mock_model.__name__ = "ModelWithOrphans"
        mock_model.rebuild_indexes.return_value = 10

        with (
            patch("scripts.popoto_index_cleanup._get_all_models", return_value=[mock_model]),
            patch("scripts.popoto_index_cleanup._count_orphans", return_value=3),
        ):
            result = run_cleanup()
            assert result["total_orphans_found"] == 3
            assert result["per_model"]["ModelWithOrphans"]["orphans_found"] == 3


class TestCountOrphansUsesCanonicalKey:
    """B1 regression pin: _count_orphans must use the canonical class set key.

    The bug being fixed: ``_count_orphans`` previously read the legacy
    ``{Name}:_all`` key, which is empty in production. Reading the wrong
    key caused ``status --deep`` to silently report ``orphan_index_count: 0``
    regardless of true state (#1214).

    The canonical key is ``model_class._meta.db_class_set_key.redis_key``
    which yields ``$Class:{Name}``. This test asserts the function reads
    that key, NOT ``{Name}:_all``.
    """

    def test_uses_canonical_db_class_set_key(self):
        # Build a mock model whose canonical key is "$Class:Foo" but whose
        # legacy "Foo:_all" key would yield different content.
        mock_model = MagicMock()
        mock_model.__name__ = "Foo"
        mock_model._meta.db_class_set_key.redis_key = "$Class:Foo"

        canonical_members = {b"k1", b"k2"}
        legacy_members = set()  # legacy key is empty in production

        def smembers(key):
            if key == "$Class:Foo":
                return canonical_members
            if key == "Foo:_all":
                return legacy_members
            return set()

        mock_db = MagicMock()
        mock_db.smembers.side_effect = smembers
        # All keys "exist" — orphan_count == 0 here, but we're checking
        # which key was queried, not the count.
        mock_db.exists.return_value = 1

        import popoto.redis_db

        original = popoto.redis_db.POPOTO_REDIS_DB
        try:
            popoto.redis_db.POPOTO_REDIS_DB = mock_db
            _count_orphans(mock_model)
        finally:
            popoto.redis_db.POPOTO_REDIS_DB = original

        # Assert smembers was called with the canonical key — never the
        # legacy one.
        called_keys = [c.args[0] for c in mock_db.smembers.call_args_list]
        assert "$Class:Foo" in called_keys, (
            f"_count_orphans must read $Class:Foo (canonical), got calls: {called_keys}"
        )
        assert "Foo:_all" not in called_keys, (
            f"_count_orphans must NOT read Foo:_all (legacy/empty), "
            f"got calls: {called_keys} — this is the data-bug from #1214"
        )


class TestCountDiskOrphans:
    """Tests for the new disk-side orphan counter (parallel to _count_orphans)."""

    def test_returns_zero_for_missing_directory(self):
        """Fresh-install case: no embedding directory exists."""
        # Point POPOTO_CONTENT_PATH to a nonexistent path
        with tempfile.TemporaryDirectory() as tmpdir:
            ghost = os.path.join(tmpdir, "does_not_exist")
            with patch.dict(os.environ, {"POPOTO_CONTENT_PATH": ghost}):
                mock_model = MagicMock()
                mock_model.__name__ = "Ghost"
                # Even if the helper somehow reaches Redis, force empty
                with patch(
                    "popoto.fields.embedding_field._compute_expected_keep",
                    return_value=set(),
                ):
                    assert _count_disk_orphans(mock_model) == 0

    def test_counts_orphans_excluding_tmp_and_live(self):
        """Walks the directory and counts orphan .npy files only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            emb_dir = os.path.join(tmpdir, ".embeddings", "MyMem")
            os.makedirs(emb_dir, exist_ok=True)

            live_filename = "a" * 64 + ".npy"
            orphan_filename1 = "b" * 64 + ".npy"
            orphan_filename2 = "c" * 64 + ".npy"
            tmp_filename = "tmpXYZ.npy"

            for fname in (live_filename, orphan_filename1, orphan_filename2, tmp_filename):
                open(os.path.join(emb_dir, fname), "w").close()

            with patch.dict(os.environ, {"POPOTO_CONTENT_PATH": tmpdir}):
                with patch(
                    "popoto.fields.embedding_field._compute_expected_keep",
                    return_value={live_filename},
                ):
                    mock_model = MagicMock()
                    mock_model.__name__ = "MyMem"
                    # 2 orphans (tmp excluded, live excluded)
                    assert _count_disk_orphans(mock_model) == 2

    def test_uses_shared_compute_expected_keep_helper(self):
        """C-C: must call the shared helper, never inline SHA-256/key logic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            emb_dir = os.path.join(tmpdir, ".embeddings", "Spy")
            os.makedirs(emb_dir, exist_ok=True)
            with patch.dict(os.environ, {"POPOTO_CONTENT_PATH": tmpdir}):
                with patch(
                    "popoto.fields.embedding_field._compute_expected_keep",
                    return_value=set(),
                ) as spy:
                    mock_model = MagicMock()
                    mock_model.__name__ = "Spy"
                    _count_disk_orphans(mock_model)
                    assert spy.call_count >= 1, (
                        "_count_disk_orphans must call the shared "
                        "_compute_expected_keep helper (C-C single source of truth)"
                    )
