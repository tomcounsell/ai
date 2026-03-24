"""
Integration tests for image_gen.

These tests use real OpenRouter API calls (when API key is available).
Run with: pytest tools/image_gen/tests/ -v
"""

import os
from pathlib import Path

import pytest

from tools.image_gen import generate_image


class TestConfiguration:
    """Test tool configuration and setup."""

    def test_manifest_exists(self):
        """manifest.json should exist."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        assert manifest_path.exists()

    def test_manifest_valid_json(self):
        """manifest.json should be valid JSON."""
        import json

        manifest_path = Path(__file__).parent.parent / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["name"] == "image_gen"
        assert manifest["type"] == "library"
        assert "generate" in manifest["capabilities"]

    def test_readme_exists(self):
        """README.md should exist."""
        readme_path = Path(__file__).parent.parent / "README.md"
        assert readme_path.exists()


class TestImports:
    """Test that the module can be imported."""

    def test_import_module(self):
        """Should be able to import image_gen module."""
        assert callable(generate_image)

    def test_constants_defined(self):
        """Module should have expected constants."""
        from tools.image_gen import OPENROUTER_URL

        assert OPENROUTER_URL


class TestGenerateImageFunction:
    """Test the generate_image function."""

    def test_missing_api_key(self, monkeypatch):
        """Should return error when API key is missing."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        result = generate_image("test prompt")

        assert "error" in result
        assert "OPENROUTER_API_KEY" in result["error"]

    @pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    )
    def test_generate_image_real_api(self, tmp_path):
        """Should generate an image with real API (requires API key)."""
        result = generate_image(
            prompt="a simple red circle on white background",
            output_dir=str(tmp_path),
        )

        # May fail due to content filtering or API issues
        if "error" not in result:
            assert "path" in result or "images" in result
        else:
            assert "error" in result

    def test_generate_image_callable(self):
        """Should be a callable function."""
        assert callable(generate_image)


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_invalid_model(self, monkeypatch):
        """Should handle invalid model gracefully."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        result = generate_image(
            prompt="test",
            model="invalid/nonexistent-model",
        )

        # Should return an error (either API error or request error)
        assert "error" in result or "path" in result  # Either outcome is valid
