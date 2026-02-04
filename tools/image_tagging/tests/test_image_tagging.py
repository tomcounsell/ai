"""
Integration tests for image-tagging tool.

Run with: pytest tools/image-tagging/tests/ -v
"""

import os
import pytest
import tempfile
from pathlib import Path

from tools.image_tagging import tag_image, batch_tag_images


class TestImageTaggingInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.image_tagging import tag_image

        assert callable(tag_image)

    def test_api_key_required(self):
        """Tool returns error when API key missing."""
        original = os.environ.get("OPENROUTER_API_KEY")
        if "OPENROUTER_API_KEY" in os.environ:
            del os.environ["OPENROUTER_API_KEY"]

        try:
            result = tag_image("test.jpg")
            assert "error" in result
            assert "OPENROUTER_API_KEY" in result["error"]
        finally:
            if original:
                os.environ["OPENROUTER_API_KEY"] = original


class TestImageTaggingValidation:
    """Test input validation."""

    def test_nonexistent_file(self):
        """Returns error for nonexistent file."""
        result = tag_image("/nonexistent/path/image.jpg")
        assert "error" in result


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"
)
class TestImageTaggingCore:
    """Test core tagging functionality."""

    @pytest.fixture
    def test_image(self):
        """Create a simple test image."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not installed")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (100, 100), color="green")
            img.save(f.name)
            yield f.name
            Path(f.name).unlink(missing_ok=True)

    def test_basic_tagging(self, test_image):
        """Basic tagging returns results."""
        result = tag_image(test_image)

        assert "error" not in result, f"Tagging failed: {result.get('error')}"
        assert "tags" in result
        assert "image_type" in result

    def test_custom_categories(self, test_image):
        """Custom categories work."""
        result = tag_image(test_image, tag_categories=["objects", "colors"])

        assert "error" not in result, f"Tagging failed: {result.get('error')}"

    def test_max_tags(self, test_image):
        """Max tags limit works."""
        result = tag_image(test_image, max_tags=3)

        assert "error" not in result, f"Tagging failed: {result.get('error')}"


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"
)
class TestBatchTagging:
    """Test batch tagging functionality."""

    @pytest.fixture
    def test_images(self):
        """Create test images."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not installed")

        paths = []
        for color in ["red", "blue"]:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                img = Image.new("RGB", (100, 100), color=color)
                img.save(f.name)
                paths.append(f.name)

        yield paths

        for path in paths:
            Path(path).unlink(missing_ok=True)

    def test_batch_tagging(self, test_images):
        """Batch tagging processes multiple images."""
        result = batch_tag_images(test_images)

        assert "results" in result
        assert result["total"] == 2
