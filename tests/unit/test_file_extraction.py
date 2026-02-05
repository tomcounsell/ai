"""
Unit tests for file extraction from agent responses.

Tests the logic that detects files to send via Telegram.
"""

import re
import tempfile
from pathlib import Path

# Re-implement the extraction logic for testing (matches bridge implementation)
FILE_MARKER_PATTERN = re.compile(r"<<FILE:([^>]+)>>")
ABSOLUTE_PATH_PATTERN = re.compile(
    r'(/(?:Users|home|tmp|var)[^\s\'"<>|]*\.(?:png|jpg|jpeg|gif|webp|bmp|pdf|mp3|mp4|wav|ogg))',
    re.IGNORECASE,
)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def extract_files_from_response(response: str) -> tuple[str, list[Path]]:
    """Extract files to send from response text."""
    files_to_send: list[Path] = []
    seen_paths: set[str] = set()  # Use resolved paths to handle symlinks

    # Method 1: Explicit file markers
    for match in FILE_MARKER_PATTERN.finditer(response):
        path_str = match.group(1).strip()
        path = Path(path_str)
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in seen_paths:
                files_to_send.append(path)
                seen_paths.add(resolved)

    # Method 2: Fallback - absolute paths to media files
    for match in ABSOLUTE_PATH_PATTERN.finditer(response):
        path_str = match.group(1).strip()
        path = Path(path_str)
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in seen_paths:
                files_to_send.append(path)
                seen_paths.add(resolved)

    # Clean response: remove file markers
    cleaned = FILE_MARKER_PATTERN.sub("", response)

    # Clean up lines that are just file paths
    lines = cleaned.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and any(
            stripped == str(f) or stripped.endswith(str(f)) for f in files_to_send
        ):
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned, files_to_send


# ============================================================================
# Tests for explicit file markers
# ============================================================================


class TestExplicitFileMarkers:
    """Tests for <<FILE:path>> marker detection."""

    def test_detects_single_marker(self, tmp_path):
        """Single file marker should be detected."""
        # Create a real file
        test_file = tmp_path / "image.png"
        test_file.write_bytes(b"fake png data")

        response = f"Here's your image: <<FILE:{test_file}>>"
        text, files = extract_files_from_response(response)

        assert len(files) == 1
        assert files[0] == test_file
        assert "<<FILE:" not in text

    def test_detects_multiple_markers(self, tmp_path):
        """Multiple file markers should all be detected."""
        file1 = tmp_path / "image1.png"
        file2 = tmp_path / "image2.jpg"
        file1.write_bytes(b"png")
        file2.write_bytes(b"jpg")

        response = f"<<FILE:{file1}>> and <<FILE:{file2}>>"
        text, files = extract_files_from_response(response)

        assert len(files) == 2
        assert file1 in files
        assert file2 in files

    def test_ignores_nonexistent_files(self, tmp_path):
        """Markers pointing to nonexistent files should be ignored."""
        response = f"<<FILE:{tmp_path}/nonexistent.png>>"
        text, files = extract_files_from_response(response)

        assert len(files) == 0

    def test_cleans_marker_from_text(self, tmp_path):
        """File markers should be removed from response text."""
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"pdf")

        response = f"Here's your file <<FILE:{test_file}>> enjoy!"
        text, files = extract_files_from_response(response)

        assert "<<FILE:" not in text
        assert "Here's your file" in text
        assert "enjoy!" in text

    def test_no_duplicate_files(self, tmp_path):
        """Same file mentioned twice should only appear once."""
        test_file = tmp_path / "image.png"
        test_file.write_bytes(b"png")

        response = f"<<FILE:{test_file}>> duplicate: <<FILE:{test_file}>>"
        text, files = extract_files_from_response(response)

        assert len(files) == 1


# ============================================================================
# Tests for fallback path detection
# ============================================================================


class TestFallbackPathDetection:
    """Tests for automatic detection of absolute file paths."""

    def test_detects_users_path(self, tmp_path):
        """Paths starting with /Users should be detected."""
        # We can't easily create files in /Users, so we test the pattern
        # This test uses a mock approach
        response = "Generated: /Users/test/image.png"

        # The pattern should match even if file doesn't exist
        matches = ABSOLUTE_PATH_PATTERN.findall(response)
        assert len(matches) == 1
        assert "/Users/test/image.png" in matches

    def test_detects_tmp_path(self):
        """Paths starting with /tmp should be detected."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"png data")
            temp_path = f.name

        try:
            response = f"Saved to {temp_path}"
            text, files = extract_files_from_response(response)

            assert len(files) == 1
            assert Path(temp_path) in files
        finally:
            Path(temp_path).unlink()

    def test_detects_home_path(self):
        """Paths starting with /home should be detected."""
        response = "Output: /home/user/downloads/photo.jpg"
        matches = ABSOLUTE_PATH_PATTERN.findall(response)

        assert len(matches) == 1
        assert "/home/user/downloads/photo.jpg" in matches

    def test_detects_var_path(self):
        """Paths starting with /var should be detected."""
        response = "File at /var/tmp/output.pdf"
        matches = ABSOLUTE_PATH_PATTERN.findall(response)

        assert len(matches) == 1
        assert "/var/tmp/output.pdf" in matches

    def test_detects_multiple_extensions(self):
        """Various media extensions should be detected."""
        extensions = ["png", "jpg", "jpeg", "gif", "webp", "pdf", "mp3", "mp4"]
        for ext in extensions:
            response = f"/tmp/file.{ext}"
            matches = ABSOLUTE_PATH_PATTERN.findall(response)
            assert len(matches) == 1, f"Failed for extension: {ext}"

    def test_ignores_relative_paths(self):
        """Relative paths should not be detected."""
        response = "See ./images/photo.png or ../output.jpg"
        matches = ABSOLUTE_PATH_PATTERN.findall(response)

        assert len(matches) == 0

    def test_ignores_urls(self):
        """URLs should not be matched as file paths."""
        response = "Check https://example.com/image.png"
        matches = ABSOLUTE_PATH_PATTERN.findall(response)

        # Should not match URL paths
        assert all(not m.startswith("http") for m in matches)


# ============================================================================
# Tests for response cleaning
# ============================================================================


class TestResponseCleaning:
    """Tests for cleaning the text response after file extraction."""

    def test_removes_bare_path_lines(self, tmp_path):
        """Lines that are just file paths should be removed."""
        test_file = tmp_path / "image.png"
        test_file.write_bytes(b"png")

        response = f"Here's your image:\n{test_file}\nEnjoy!"
        text, files = extract_files_from_response(response)

        assert str(test_file) not in text
        assert "Here's your image:" in text
        assert "Enjoy!" in text

    def test_preserves_other_content(self, tmp_path):
        """Non-file content should be preserved."""
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"pdf")

        response = f"Generated your document <<FILE:{test_file}>>. Let me know if you need changes."
        text, files = extract_files_from_response(response)

        assert "Generated your document" in text
        assert "Let me know if you need changes" in text

    def test_handles_empty_result(self):
        """If response is only file markers, cleaned text should be empty."""
        response = "<<FILE:/nonexistent/file.png>>"
        text, files = extract_files_from_response(response)

        assert text == ""
        assert len(files) == 0

    def test_strips_whitespace(self, tmp_path):
        """Result should have leading/trailing whitespace stripped."""
        test_file = tmp_path / "img.png"
        test_file.write_bytes(b"png")

        response = f"   \n<<FILE:{test_file}>>\n   "
        text, files = extract_files_from_response(response)

        assert text == ""  # Only whitespace after removing marker


# ============================================================================
# Tests for combined scenarios
# ============================================================================


class TestCombinedScenarios:
    """Tests for realistic combined scenarios."""

    def test_explicit_and_fallback_together(self, tmp_path):
        """Both explicit markers and fallback paths should work together."""
        explicit_file = tmp_path / "explicit.png"
        fallback_file = tmp_path / "fallback.jpg"
        explicit_file.write_bytes(b"png")
        fallback_file.write_bytes(b"jpg")

        # Simulate the fallback path being in /tmp
        with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as f:
            f.write(b"gif")
            tmp_file = Path(f.name)

        try:
            response = f"<<FILE:{explicit_file}>> and also saved to {tmp_file}"
            text, files = extract_files_from_response(response)

            assert len(files) == 2
            assert explicit_file in files
            assert tmp_file in files
        finally:
            tmp_file.unlink()

    def test_image_generation_scenario(self, tmp_path):
        """Realistic image generation response."""
        image_file = tmp_path / "generated_image.png"
        image_file.write_bytes(b"PNG image data")

        response = f"""I've generated your image using DALL-E.

<<FILE:{image_file}>>

The image shows a sunset over mountains as requested. Let me know if you'd like any adjustments!"""

        text, files = extract_files_from_response(response)

        assert len(files) == 1
        assert image_file in files
        assert "generated your image" in text
        assert "sunset over mountains" in text
        assert "<<FILE:" not in text

    def test_multiple_files_scenario(self, tmp_path):
        """Multiple files in one response."""
        img1 = tmp_path / "photo1.png"
        img2 = tmp_path / "photo2.png"
        pdf = tmp_path / "report.pdf"
        img1.write_bytes(b"png1")
        img2.write_bytes(b"png2")
        pdf.write_bytes(b"pdf")

        response = f"""Here are the requested files:
- Photo 1: <<FILE:{img1}>>
- Photo 2: <<FILE:{img2}>>
- Report: <<FILE:{pdf}>>

All files have been generated successfully."""

        text, files = extract_files_from_response(response)

        assert len(files) == 3
        assert "requested files" in text
        assert "generated successfully" in text

    def test_no_files_scenario(self):
        """Response with no files should return empty file list."""
        response = "I've analyzed your code and found no issues. Great work!"
        text, files = extract_files_from_response(response)

        assert len(files) == 0
        assert text == response
