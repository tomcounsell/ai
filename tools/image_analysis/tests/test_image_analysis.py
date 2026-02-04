"""
Integration tests for image-analysis tool.

Run with: pytest tools/image-analysis/tests/ -v
"""

import os
import pytest
import tempfile
from pathlib import Path

from tools.image_analysis import analyze_image, extract_text, generate_alt_text


class TestImageAnalysisInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.image_analysis import analyze_image

        assert callable(analyze_image)

    def test_api_key_required(self):
        """Tool returns error when API key missing."""
        original = os.environ.get("OPENROUTER_API_KEY")
        if "OPENROUTER_API_KEY" in os.environ:
            del os.environ["OPENROUTER_API_KEY"]

        try:
            result = analyze_image("test.jpg")
            assert "error" in result
            assert "OPENROUTER_API_KEY" in result["error"]
        finally:
            if original:
                os.environ["OPENROUTER_API_KEY"] = original


class TestImageAnalysisValidation:
    """Test input validation."""

    def test_nonexistent_file(self):
        """Returns error for nonexistent file."""
        result = analyze_image("/nonexistent/path/image.jpg")
        assert "error" in result


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"
)
class TestImageAnalysisCore:
    """Test core analysis functionality."""

    @pytest.fixture
    def test_image(self):
        """Create a simple test image."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not installed")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (100, 100), color="red")
            img.save(f.name)
            yield f.name
            Path(f.name).unlink(missing_ok=True)

    def test_basic_analysis(self, test_image):
        """Basic analysis returns results."""
        result = analyze_image(test_image)

        assert "error" not in result, f"Analysis failed: {result.get('error')}"
        assert "raw_analysis" in result or "description" in result

    def test_analysis_types(self, test_image):
        """Different analysis types work."""
        result = analyze_image(test_image, analysis_types=["description", "objects"])

        assert "error" not in result, f"Analysis failed: {result.get('error')}"

    def test_detail_levels(self, test_image):
        """Different detail levels work."""
        for level in ["minimal", "standard", "detailed"]:
            result = analyze_image(test_image, detail_level=level)
            assert "error" not in result, f"Analysis at {level} failed"


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"
)
class TestImageAnalysisHelpers:
    """Test helper functions."""

    @pytest.fixture
    def test_image(self):
        """Create a simple test image."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not installed")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (100, 100), color="blue")
            img.save(f.name)
            yield f.name
            Path(f.name).unlink(missing_ok=True)

    def test_extract_text(self, test_image):
        """Extract text function works."""
        result = extract_text(test_image)
        assert "error" not in result, f"OCR failed: {result.get('error')}"

    def test_generate_alt_text(self, test_image):
        """Alt-text generation works."""
        result = generate_alt_text(test_image)
        assert "error" not in result, f"Alt-text failed: {result.get('error')}"
