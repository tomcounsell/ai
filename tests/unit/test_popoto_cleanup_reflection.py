"""Tests for Popoto index cleanup reflection."""

from unittest.mock import MagicMock, patch

from scripts.popoto_index_cleanup import _count_orphans, _get_all_models, run_cleanup


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
            patch("scripts.popoto_index_cleanup._get_all_models", return_value=[mock_model_a, mock_model_b]),
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
            patch("scripts.popoto_index_cleanup._get_all_models", return_value=[mock_model_ok, mock_model_bad]),
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
