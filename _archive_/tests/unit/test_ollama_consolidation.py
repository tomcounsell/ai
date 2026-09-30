"""Unit tests for the gemma4→granite+cloud Ollama consolidation (issue #1636).

Covers the config-layer changes:
  - OLLAMA_CLASSIFIER_MODEL / OLLAMA_SUPERSEDED_MODELS / MIN_LOCAL_GEN_RAM_GB
  - ensure_generation_model() branches (cloud no-op, mlx RAM-guard, local probe)
  - ModelSettings.ollama_generation_model default + env override
  - the /update superseded-rm gate predicate (granite_smoke_passed AND marker)
  - the scripts/update + indexer importer smoke (Blocker 1)
"""

from __future__ import annotations

import importlib
import subprocess
from unittest.mock import patch

import pytest


class TestModelConstants:
    def test_classifier_constant_is_granite(self):
        from config.models import OLLAMA_CLASSIFIER_MODEL

        assert OLLAMA_CLASSIFIER_MODEL == "granite4.1:3b"

    def test_gemma_is_superseded(self):
        from config.models import OLLAMA_SUPERSEDED_MODELS

        assert "gemma4:e2b" in OLLAMA_SUPERSEDED_MODELS

    def test_old_constant_removed(self):
        import config.models as models

        assert not hasattr(models, "OLLAMA_LOCAL_MODEL")

    def test_min_local_gen_ram_threshold(self):
        from config.models import MIN_LOCAL_GEN_RAM_GB

        assert MIN_LOCAL_GEN_RAM_GB == 48


class TestEnsureGenerationModel:
    def test_cloud_tag_is_no_op_available(self):
        """A :cloud tag is always reported available without any pull."""
        from config.models import ensure_generation_model

        with patch("config.models.subprocess.run") as mock_run:
            ok, detail = ensure_generation_model("gemma4:31b-cloud")
        assert ok is True
        assert "cloud" in detail.lower()
        mock_run.assert_not_called()  # no probe, no pull for cloud

    def test_mlx_below_threshold_skips_pull(self):
        """An -mlx tag on a small host returns False WITHOUT pulling."""
        from config.models import ensure_generation_model

        with (
            patch("config.models._host_ram_gb", return_value=16.0),
            patch("config.models.subprocess.run") as mock_run,
        ):
            ok, detail = ensure_generation_model("gemma4:31b-mlx")
        assert ok is False
        assert "RAM too low" in detail
        mock_run.assert_not_called()  # guard fires before any ollama call

    def test_mlx_above_threshold_probes(self):
        """An -mlx tag on a RAM-rich host probes (and reports responsive)."""
        from config.models import ensure_generation_model

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ready", stderr="")
        with (
            patch("config.models._host_ram_gb", return_value=64.0),
            patch("config.models.shutil.which", return_value="/usr/bin/ollama"),
            patch("config.models.subprocess.run", return_value=completed) as mock_run,
        ):
            ok, detail = ensure_generation_model("gemma4:31b-mlx")
        assert ok is True
        assert "responsive" in detail
        mock_run.assert_called()  # probe happened


class TestModelSettings:
    def test_generation_model_default(self):
        # Assert the CODE default via a bare ModelSettings() — the process-wide
        # `settings` singleton reflects the machine-local env override
        # MODELS__OLLAMA_GENERATION_MODEL (written to ~/.zshenv by /setup), so
        # reading the singleton here would test the machine, not the default.
        from config.settings import ModelSettings

        assert ModelSettings().ollama_generation_model == "gemma4:31b-cloud"

    def test_generation_model_env_override(self, monkeypatch):
        monkeypatch.setenv("MODELS__OLLAMA_GENERATION_MODEL", "gemma4:31b-mlx")
        from config.settings import Settings

        fresh = Settings()
        assert fresh.models.ollama_generation_model == "gemma4:31b-mlx"

    def test_vision_model_removed(self):
        from config.settings import settings

        assert not hasattr(settings.models, "ollama_vision_model")


class TestSupersededRmGate:
    """The /update gemma rm proceeds only when the ollama classifier model is
    present AND the spike-1 parity marker exists (data/spike1_parity_ok).

    Post-cutover (#1924, task 7): the old `granite_smoke_passed` boolean died
    with the PTY substrate; the gate is now classifier-presence-based.
    """

    @staticmethod
    def _should_rm(classifier_present: bool, marker_exists: bool) -> bool:
        # Mirrors the gate predicate in scripts/update/run.py Step 4.76.
        return classifier_present and marker_exists

    @pytest.mark.parametrize(
        "classifier,marker,expected",
        [
            (True, True, True),
            (True, False, False),
            (False, True, False),
            (False, False, False),
        ],
    )
    def test_rm_gate_predicate(self, classifier, marker, expected):
        assert self._should_rm(classifier, marker) is expected

    def test_run_py_gates_rm_on_both_conditions(self):
        """The relocated rm loop must require both the classifier probe and
        the marker — and the retired granite_smoke boolean must stay gone."""
        src = importlib.import_module("scripts.update.run").__file__
        text = open(src).read()
        assert "spike1_parity_ok" in text
        assert "classifier_present and spike1_parity_ok" in text
        assert "granite_smoke_passed" not in text


class TestImporterSmoke:
    """Blocker 1: removing OLLAMA_LOCAL_MODEL must not crash any importer."""

    def test_update_and_indexer_modules_import(self):
        for mod in (
            "scripts.update.run",
            "scripts.update.mcp_memory",
            "scripts.update.verify",
            "tools.knowledge.indexer",
        ):
            assert importlib.import_module(mod) is not None

    def test_no_gemma_literal_in_indexer(self):
        import tools.knowledge.indexer as indexer

        text = open(indexer.__file__).read()
        assert "gemma4:e2b" not in text
        assert "OLLAMA_LOCAL_MODEL" not in text
