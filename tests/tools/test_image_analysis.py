"""Tests for the image analysis tool."""

import os
from pathlib import Path

import pytest

from tools.image_analysis import (
    analyze_image,
    extract_text,
    generate_alt_text,
)


class TestAnalyzeImageValidation:
    """Test input validation."""

    def test_missing_api_key(self, tmp_path):
        """Test analysis without API key."""
        original_key = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            result = analyze_image("test.jpg")
            assert "error" in result
            assert "OPENROUTER_API_KEY" in result["error"]
        finally:
            if original_key:
                os.environ["OPENROUTER_API_KEY"] = original_key

    def test_nonexistent_file(self, openrouter_api_key):
        """Test analysis of non-existent file."""
        result = analyze_image("/nonexistent/image.jpg")
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestAnalyzeImageFromUrl:
    """Test image analysis from URL."""

    def test_analyze_url_image(self, temp_image_url, openrouter_api_key):
        """Test analyzing image from URL."""
        result = analyze_image(
            temp_image_url,
            analysis_types=["description"],
            detail_level="minimal"
        )

        if "error" not in result:
            assert result.get("image_source") == temp_image_url
            assert "raw_analysis" in result or "description" in result

    def test_analyze_with_multiple_types(self, temp_image_url, openrouter_api_key):
        """Test analyzing with multiple analysis types."""
        result = analyze_image(
            temp_image_url,
            analysis_types=["description", "objects", "tags"],
            detail_level="standard"
        )

        if "error" not in result:
            assert result.get("analysis_types") == ["description", "objects", "tags"]


class TestAnalyzeImageDetailLevels:
    """Test different detail levels."""

    def test_minimal_detail(self, temp_image_url, openrouter_api_key):
        """Test minimal detail level."""
        result = analyze_image(
            temp_image_url,
            detail_level="minimal"
        )

        if "error" not in result:
            assert result.get("detail_level") == "minimal"

    def test_comprehensive_detail(self, temp_image_url, openrouter_api_key):
        """Test comprehensive detail level."""
        result = analyze_image(
            temp_image_url,
            detail_level="comprehensive"
        )

        if "error" not in result:
            assert result.get("detail_level") == "comprehensive"


class TestAnalyzeImageOutputFormats:
    """Test different output formats."""

    def test_structured_format(self, temp_image_url, openrouter_api_key):
        """Test structured output format."""
        result = analyze_image(
            temp_image_url,
            output_format="structured"
        )

        if "error" not in result:
            assert "raw_analysis" in result

    def test_accessibility_format(self, temp_image_url, openrouter_api_key):
        """Test accessibility output format."""
        result = analyze_image(
            temp_image_url,
            output_format="accessibility"
        )

        if "error" not in result:
            assert "raw_analysis" in result


class TestExtractText:
    """Test OCR functionality."""

    def test_extract_text_from_image(self, temp_image_url, openrouter_api_key):
        """Test extracting text from image."""
        result = extract_text(temp_image_url)

        # The function should return analysis result
        if "error" not in result:
            assert "text" in result.get("analysis_types", [])


class TestGenerateAltText:
    """Test alt text generation."""

    def test_generate_alt_text(self, temp_image_url, openrouter_api_key):
        """Test generating alt text for image."""
        result = generate_alt_text(temp_image_url)

        if "error" not in result:
            assert "raw_analysis" in result or "description" in result


class TestAnalyzeImageFromFile:
    """Test image analysis from local file."""

    def test_analyze_local_png(self, tmp_path, openrouter_api_key):
        """Test analyzing local PNG file."""
        # Create a small valid PNG file
        import struct
        import zlib

        def create_minimal_png(width=10, height=10):
            """Create a minimal valid PNG file."""
            # PNG signature
            signature = b'\x89PNG\r\n\x1a\n'

            # IHDR chunk
            ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff
            ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)

            # IDAT chunk (minimal image data)
            raw_data = b'\x00' * (width * 3 + 1) * height
            compressed = zlib.compress(raw_data)
            idat_crc = zlib.crc32(b'IDAT' + compressed) & 0xffffffff
            idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc)

            # IEND chunk
            iend_crc = zlib.crc32(b'IEND') & 0xffffffff
            iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)

            return signature + ihdr + idat + iend

        test_image = tmp_path / "test.png"
        test_image.write_bytes(create_minimal_png())

        result = analyze_image(
            str(test_image),
            analysis_types=["description"],
            detail_level="minimal"
        )

        assert result.get("image_source") == str(test_image)

    def test_analyze_jpg_extension_recognition(self, tmp_path, openrouter_api_key):
        """Test that JPG extension is recognized."""
        test_image = tmp_path / "test.jpg"
        # Create minimal JPEG (not valid but tests extension handling)
        test_image.write_bytes(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00')

        result = analyze_image(str(test_image))

        # Will likely error due to invalid JPEG but tests path handling
        assert result.get("image_source") == str(test_image)
