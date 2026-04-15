"""Tests for the YouTube search tool.

Tests marked with @pytest.mark.integration require network access.
Unit tests use mocking to avoid network dependency.
"""

import asyncio
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from tools.youtube_search import (
    format_results,
    youtube_search,
    youtube_search_sync,
)


# --- Unit tests (no network) ---


class TestSearchEmptyQuery:
    """Test empty/invalid query handling."""

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            youtube_search_sync("")

    def test_none_raises(self):
        with pytest.raises((ValueError, TypeError)):
            youtube_search_sync(None)

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            youtube_search_sync("   ")


class TestFormatResults:
    """Test result formatting."""

    def test_empty_results(self):
        assert format_results([]) == "No results found."

    def test_format_with_all_fields(self):
        results = [
            {
                "title": "Test Video",
                "url": "https://www.youtube.com/watch?v=abc123",
                "video_id": "abc123",
                "duration": 125,
                "view_count": 1500000,
                "uploader": "Test Channel",
                "description": "A test video description",
                "upload_date": "20240101",
            }
        ]
        output = format_results(results)
        assert "Test Video" in output
        assert "https://www.youtube.com/watch?v=abc123" in output
        assert "Test Channel" in output
        assert "2:05" in output
        assert "1,500,000" in output
        assert "A test video description" in output

    def test_format_with_none_fields(self):
        """Verify formatter handles None duration/view_count/upload_date without crashing."""
        results = [
            {
                "title": "Minimal Video",
                "url": "https://www.youtube.com/watch?v=xyz",
                "video_id": "xyz",
                "duration": None,
                "view_count": None,
                "uploader": None,
                "description": None,
                "upload_date": None,
            }
        ]
        output = format_results(results)
        assert "Minimal Video" in output
        assert "https://www.youtube.com/watch?v=xyz" in output
        # None fields should not appear
        assert "Duration" not in output
        assert "Views" not in output
        assert "Uploader" not in output
        assert "Description" not in output

    def test_format_long_description_truncated(self):
        results = [
            {
                "title": "Long Desc",
                "url": "https://www.youtube.com/watch?v=abc",
                "video_id": "abc",
                "duration": None,
                "view_count": None,
                "uploader": None,
                "description": "A" * 200,
                "upload_date": None,
            }
        ]
        output = format_results(results)
        assert "..." in output

    def test_format_duration_with_hours(self):
        results = [
            {
                "title": "Long Video",
                "url": "https://example.com",
                "video_id": "abc",
                "duration": 3661,  # 1:01:01
                "view_count": None,
                "uploader": None,
                "description": None,
                "upload_date": None,
            }
        ]
        output = format_results(results)
        assert "1:01:01" in output


class TestSearchWithMock:
    """Test search logic with mocked yt-dlp."""

    @patch("tools.youtube_search._extract_info")
    def test_search_returns_results(self, mock_extract):
        mock_extract.return_value = {
            "entries": [
                {
                    "id": "vid1",
                    "title": "Video 1",
                    "url": "https://www.youtube.com/watch?v=vid1",
                    "duration": 120,
                    "view_count": 1000,
                    "uploader": "Channel",
                    "description": "desc",
                    "upload_date": "20240101",
                },
                {
                    "id": "vid2",
                    "title": "Video 2",
                    "url": "https://www.youtube.com/watch?v=vid2",
                    "duration": None,
                    "view_count": None,
                    "uploader": None,
                    "description": None,
                    "upload_date": None,
                },
            ]
        }
        results = youtube_search_sync("test query", limit=2)
        assert len(results) == 2
        assert results[0]["title"] == "Video 1"
        assert results[0]["video_id"] == "vid1"
        assert results[0]["url"] == "https://www.youtube.com/watch?v=vid1"
        assert results[1]["duration"] is None

    @patch("tools.youtube_search._extract_info")
    def test_search_result_fields(self, mock_extract):
        """Verify each result has guaranteed fields and handles None for best-effort fields."""
        mock_extract.return_value = {
            "entries": [
                {
                    "id": "v1",
                    "title": "Title",
                    "url": "https://www.youtube.com/watch?v=v1",
                }
            ]
        }
        results = youtube_search_sync("query")
        r = results[0]
        # Guaranteed fields
        assert "title" in r
        assert "url" in r
        assert "video_id" in r
        # Best-effort fields present but may be None
        assert "duration" in r
        assert "view_count" in r
        assert "uploader" in r
        assert "description" in r
        assert "upload_date" in r

    @patch("tools.youtube_search._extract_info")
    def test_search_no_results(self, mock_extract):
        mock_extract.return_value = {"entries": []}
        results = youtube_search_sync("nonexistent_query_xyz")
        assert results == []

    @patch("tools.youtube_search._extract_info")
    def test_search_extraction_error(self, mock_extract):
        mock_extract.side_effect = Exception("Network error")
        with pytest.raises(RuntimeError, match="YouTube search failed"):
            youtube_search_sync("test")


class TestAsyncWrapper:
    """Test async wrapper is callable and returns same structure."""

    @patch("tools.youtube_search._extract_info")
    @pytest.mark.asyncio
    async def test_async_wrapper_callable(self, mock_extract):
        mock_extract.return_value = {
            "entries": [
                {
                    "id": "v1",
                    "title": "Async Test",
                    "url": "https://www.youtube.com/watch?v=v1",
                }
            ]
        }
        results = await youtube_search("test", limit=1)
        assert len(results) == 1
        assert results[0]["title"] == "Async Test"


class TestCLI:
    """Test CLI entry point behavior."""

    def test_cli_usage_on_empty_args(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.youtube_search.cli"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Usage" in result.stderr


# --- Integration tests (require network) ---


@pytest.mark.integration
class TestSearchIntegration:
    """Integration tests that hit real YouTube. Require network."""

    def test_real_search_returns_results(self):
        results = youtube_search_sync("python tutorial", limit=3)
        assert len(results) > 0
        for r in results:
            assert r["title"]
            assert r["url"]
            assert r["video_id"]
            assert "youtube.com" in r["url"] or "youtu.be" in r["url"]

    def test_real_search_limit(self):
        results = youtube_search_sync("python", limit=2)
        assert len(results) <= 2
