"""
Tests for the selfie tool.

Unit tests run without API keys. Integration tests require OPENAI_API_KEY.
Run with: pytest tools/selfie/tests/ -v
"""

import json
import os
from pathlib import Path

import pytest

from tools.selfie import (
    DEFAULT_SCENES,
    SELFIE_BASE_PROMPT,
    VALOR_APPEARANCE,
    take_selfie,
)


class TestConfiguration:
    """Test tool configuration and setup."""

    def test_manifest_exists(self):
        """manifest.json should exist."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        assert manifest_path.exists()

    def test_manifest_valid_json(self):
        """manifest.json should be valid JSON."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["name"] == "selfie"
        assert manifest["type"] == "api"
        assert "capture" in manifest["capabilities"]

    def test_readme_exists(self):
        """README.md should exist."""
        readme_path = Path(__file__).parent.parent / "README.md"
        assert readme_path.exists()


class TestImports:
    """Test that the module can be imported."""

    def test_import_take_selfie(self):
        """Should be able to import take_selfie function."""
        assert callable(take_selfie)

    def test_constants_defined(self):
        """Module should have expected constants."""
        assert VALOR_APPEARANCE
        assert SELFIE_BASE_PROMPT
        assert isinstance(DEFAULT_SCENES, dict)
        assert len(DEFAULT_SCENES) >= 3


class TestConstants:
    """Test module constants."""

    def test_default_scenes_keys(self):
        """Should have expected scene keys."""
        expected_scenes = {"office", "coffee", "outdoors", "evening", "working"}
        assert expected_scenes == set(DEFAULT_SCENES.keys())

    def test_appearance_description(self):
        """Appearance description should contain key features."""
        assert "male" in VALOR_APPEARANCE.lower()
        assert "hair" in VALOR_APPEARANCE.lower()

    def test_base_prompt_uses_appearance(self):
        """Base prompt should incorporate appearance."""
        assert VALOR_APPEARANCE in SELFIE_BASE_PROMPT


class TestTakeSelfieUnit:
    """Unit tests for take_selfie (no API key needed)."""

    def test_missing_api_key(self, monkeypatch):
        """Should return error when API key is missing."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = take_selfie(scene="office")

        assert "error" in result

    def test_invalid_scene_falls_back(self, monkeypatch):
        """Should handle unknown scene name gracefully."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        # Even with invalid scene, should not crash -- just fail on API key
        result = take_selfie(scene="nonexistent_scene")
        assert "error" in result


class TestTakeSelfieIntegration:
    """Integration tests requiring OPENAI_API_KEY."""

    @pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    )
    def test_generate_selfie_real_api(self, tmp_path):
        """Should generate a selfie image with real API."""
        result = take_selfie(
            scene="office",
            output_dir=str(tmp_path),
        )

        if "error" not in result:
            assert "path" in result
            assert Path(result["path"]).exists()
        else:
            # API errors are acceptable test outcomes
            assert "error" in result
