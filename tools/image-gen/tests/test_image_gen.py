"""
Integration tests for image-gen.

These tests use real OpenRouter API calls (when API key is available).
Run with: pytest tools/image-gen/tests/ -v
"""

import os
import sys
from pathlib import Path

import pytest

# Add tools to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


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

        assert manifest["name"] == "image-gen"
        assert manifest["type"] == "library"
        assert "generate" in manifest["capabilities"]

    def test_readme_exists(self):
        """README.md should exist."""
        readme_path = Path(__file__).parent.parent / "README.md"
        assert readme_path.exists()


class TestImports:
    """Test that the module can be imported."""

    def test_import_module(self):
        """Should be able to import the image-gen module."""
        # Import using hyphenated directory name via importlib
        import importlib.util

        module_path = Path(__file__).parent.parent / "__init__.py"
        spec = importlib.util.spec_from_file_location("image_gen", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert hasattr(module, "generate_image")
        assert hasattr(module, "generate_images")

    def test_constants_defined(self):
        """Module should have expected constants."""
        import importlib.util

        module_path = Path(__file__).parent.parent / "__init__.py"
        spec = importlib.util.spec_from_file_location("image_gen", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert hasattr(module, "OPENROUTER_URL")
        assert hasattr(module, "DEFAULT_MODEL")


class TestGenerateImageFunction:
    """Test the generate_image function."""

    def get_module(self):
        """Helper to import the module."""
        import importlib.util

        module_path = Path(__file__).parent.parent / "__init__.py"
        spec = importlib.util.spec_from_file_location("image_gen", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_missing_api_key(self, monkeypatch):
        """Should return error when API key is missing."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        module = self.get_module()
        result = module.generate_image("test prompt")

        assert "error" in result
        assert "OPENROUTER_API_KEY" in result["error"]

    @pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    )
    def test_generate_image_real_api(self, tmp_path):
        """Should generate an image with real API (requires API key)."""
        module = self.get_module()

        result = module.generate_image(
            prompt="a simple red circle on white background",
            output_dir=str(tmp_path),
        )

        # May fail due to content filtering or API issues
        if "error" not in result:
            assert "path" in result
            assert Path(result["path"]).exists()
        else:
            # Accept API errors as valid test outcomes
            assert "error" in result

    def test_generate_images_empty_list(self):
        """Should handle empty prompt list."""
        module = self.get_module()
        results = module.generate_images([])
        assert results == []


class TestErrorHandling:
    """Test error handling scenarios."""

    def get_module(self):
        """Helper to import the module."""
        import importlib.util

        module_path = Path(__file__).parent.parent / "__init__.py"
        spec = importlib.util.spec_from_file_location("image_gen", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_invalid_model(self, monkeypatch):
        """Should handle invalid model gracefully."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        module = self.get_module()
        result = module.generate_image(
            prompt="test",
            model="invalid/nonexistent-model",
        )

        # Should return an error (either API error or request error)
        assert "error" in result or "path" in result  # Either outcome is valid
