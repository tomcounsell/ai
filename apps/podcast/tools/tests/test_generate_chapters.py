"""Tests for generate_chapters.py pure functions."""

import sys
from pathlib import Path

# Add parent directory to path to import the module
sys.path.insert(0, str(Path(__file__).parent.parent))

from generate_chapters import chunk_segments, format_timestamp


class TestFormatTimestamp:
    """Tests for the format_timestamp function."""

    def test_zero_seconds(self):
        """Test formatting 0 seconds."""
        assert format_timestamp(0) == "00:00"

    def test_under_minute(self):
        """Test formatting seconds less than a minute."""
        assert format_timestamp(45) == "00:45"
        assert format_timestamp(59) == "00:59"

    def test_exact_minute(self):
        """Test formatting exact minutes."""
        assert format_timestamp(60) == "01:00"
        assert format_timestamp(120) == "02:00"

    def test_minutes_and_seconds(self):
        """Test formatting minutes and seconds."""
        assert format_timestamp(75) == "01:15"
        assert format_timestamp(195) == "03:15"

    def test_under_hour(self):
        """Test formatting just under an hour."""
        assert format_timestamp(3599) == "59:59"

    def test_exact_hour(self):
        """Test formatting exactly one hour."""
        assert format_timestamp(3600) == "01:00:00"

    def test_hours_minutes_seconds(self):
        """Test formatting with all three components."""
        assert format_timestamp(3661) == "01:01:01"
        assert format_timestamp(7384) == "02:03:04"

    def test_multiple_hours(self):
        """Test formatting multiple hours."""
        assert format_timestamp(7200) == "02:00:00"
        assert format_timestamp(36000) == "10:00:00"

    def test_decimal_seconds(self):
        """Test that decimal seconds are truncated (not rounded)."""
        assert format_timestamp(65.9) == "01:05"
        assert format_timestamp(3665.7) == "01:01:05"

    def test_large_values(self):
        """Test formatting very large time values."""
        assert format_timestamp(86400) == "24:00:00"  # 24 hours


class TestChunkSegments:
    """Tests for the chunk_segments function."""

    def test_single_segment(self):
        """Test chunking a single segment."""
        segments = [{"start": 0, "end": 30, "text": "Hello world"}]
        chunks = chunk_segments(segments)

        assert len(chunks) == 1
        assert chunks[0]["start"] == 0
        assert chunks[0]["end"] == 30
        assert chunks[0]["text"] == "Hello world"

    def test_multiple_segments_under_duration(self):
        """Test multiple segments that fit within one chunk."""
        segments = [
            {"start": 0, "end": 30, "text": "First"},
            {"start": 30, "end": 60, "text": "Second"},
            {"start": 60, "end": 90, "text": "Third"},
        ]
        chunks = chunk_segments(segments, chunk_duration=120.0)

        assert len(chunks) == 1
        assert chunks[0]["start"] == 0
        assert chunks[0]["end"] == 90
        assert chunks[0]["text"] == "First Second Third"

    def test_segments_exceed_duration(self):
        """Test segments that exceed chunk duration."""
        segments = [
            {"start": 0, "end": 60, "text": "First"},
            {"start": 60, "end": 120, "text": "Second"},
            {"start": 120, "end": 180, "text": "Third"},
        ]
        chunks = chunk_segments(segments, chunk_duration=100.0)

        # Algorithm creates new chunk when segment.end - chunk.start > duration
        # Segment 2: 120 - 0 = 120 > 100, so Second starts new chunk
        # Segment 3: 180 - 60 = 120 > 100, so Third starts new chunk
        assert len(chunks) == 3
        assert chunks[0]["start"] == 0
        assert chunks[0]["end"] == 60
        assert chunks[0]["text"] == "First"
        assert chunks[1]["start"] == 60
        assert chunks[1]["end"] == 120
        assert chunks[1]["text"] == "Second"
        assert chunks[2]["start"] == 120
        assert chunks[2]["end"] == 180
        assert chunks[2]["text"] == "Third"

    def test_exact_boundary(self):
        """Test segments that hit chunk duration exactly."""
        segments = [
            {"start": 0, "end": 60, "text": "First"},
            {"start": 60, "end": 120, "text": "Second"},
            {"start": 120, "end": 180, "text": "Third"},
        ]
        chunks = chunk_segments(segments, chunk_duration=120.0)

        # The third segment starts at 120, which means from chunk start (0)
        # to segment end (180) exceeds 120, so it becomes a new chunk
        assert len(chunks) == 2
        assert chunks[0]["text"] == "First Second"
        assert chunks[1]["text"] == "Third"

    def test_custom_chunk_duration(self):
        """Test with non-default chunk duration."""
        segments = [
            {"start": 0, "end": 50, "text": "A"},
            {"start": 50, "end": 100, "text": "B"},
            {"start": 100, "end": 150, "text": "C"},
            {"start": 150, "end": 200, "text": "D"},
        ]
        chunks = chunk_segments(segments, chunk_duration=80.0)

        # Each segment is 50s, with 80s chunk duration
        # A (0-50): 50 - 0 = 50 ≤ 80, include
        # B (50-100): 100 - 0 = 100 > 80, new chunk
        # C (100-150): 150 - 50 = 100 > 80, new chunk
        # D (150-200): 200 - 100 = 100 > 80, new chunk
        assert len(chunks) == 4
        assert chunks[0]["text"] == "A"
        assert chunks[1]["text"] == "B"
        assert chunks[2]["text"] == "C"
        assert chunks[3]["text"] == "D"

    def test_text_concatenation_spacing(self):
        """Test that text is properly concatenated with spaces."""
        segments = [
            {"start": 0, "end": 20, "text": "Hello"},
            {"start": 20, "end": 40, "text": "there"},
            {"start": 40, "end": 60, "text": "friend"},
        ]
        chunks = chunk_segments(segments, chunk_duration=120.0)

        assert len(chunks) == 1
        assert chunks[0]["text"] == "Hello there friend"

    def test_very_long_segments(self):
        """Test handling segments longer than chunk duration."""
        segments = [
            {"start": 0, "end": 200, "text": "Very long segment"},
            {"start": 200, "end": 220, "text": "Short"},
        ]
        chunks = chunk_segments(segments, chunk_duration=100.0)

        # Even though first segment is 200s (> 100), it's still included
        # Second segment starts new chunk because 220 - 0 > 100
        assert len(chunks) == 2
        assert chunks[0]["text"] == "Very long segment"
        assert chunks[1]["text"] == "Short"

    def test_end_time_updates(self):
        """Test that chunk end time is properly updated."""
        segments = [
            {"start": 0, "end": 30, "text": "A"},
            {"start": 30, "end": 70, "text": "B"},
            {"start": 70, "end": 100, "text": "C"},
        ]
        chunks = chunk_segments(segments, chunk_duration=150.0)

        assert len(chunks) == 1
        assert chunks[0]["start"] == 0
        assert chunks[0]["end"] == 100  # Should be end of last segment
