"""Tests for the thin-transcript watch signpost in YouTube enrichment.

When a YouTube transcript is thin (music-only, silent, or on-screen-only),
``enrich_message`` appends a ``valor-video-watch`` signpost so the agent can
fall back to visual grounding. The decision must gate on ``transcript`` length
only — never on ``context``, which is non-empty even on failure.
"""

import json
from unittest.mock import patch

from bridge import enrichment

SIGNPOST = "valor-video-watch"


def _youtube_urls_arg(*video_ids):
    """Build the JSON-encoded youtube_urls argument enrich_message expects."""
    return json.dumps([[f"https://youtu.be/{vid}", vid] for vid in video_ids])


async def _run(message_text, youtube_urls, results):
    """Invoke enrich_message with process_youtube_urls_in_text patched to
    return controlled results (no network / AI)."""

    async def fake_process(text):
        return text, results

    with patch("tools.link_analysis.process_youtube_urls_in_text", fake_process):
        return await enrichment.enrich_message(
            message_text,
            youtube_urls=youtube_urls,
        )


async def test_thin_transcript_emits_signpost():
    """(a) An empty/short transcript produces the watch signpost."""
    results = [
        {
            "success": True,
            "video_id": "abc123",
            "url": "https://youtu.be/abc123",
            "title": "Silent clip",
            "transcript": "",
            "summary": None,
            "context": "some context",
        }
    ]
    enriched = await _run("watch this", _youtube_urls_arg("abc123"), results)
    assert SIGNPOST in enriched
    assert "https://youtu.be/abc123" in enriched


async def test_healthy_transcript_no_signpost():
    """(b) A healthy long transcript (>= threshold) does NOT signpost."""
    long_transcript = "word " * 100  # comfortably over the threshold
    results = [
        {
            "success": True,
            "video_id": "def456",
            "url": "https://youtu.be/def456",
            "title": "Talky video",
            "transcript": long_transcript,
            "summary": "a summary",
            "context": "some context",
        }
    ]
    enriched = await _run("watch this", _youtube_urls_arg("def456"), results)
    assert SIGNPOST not in enriched


async def test_error_context_but_no_transcript_still_signposts():
    """(c) Non-empty error context with transcript=None STILL signposts —
    proves the gate is on transcript, not context."""
    results = [
        {
            "success": False,
            "video_id": "err789",
            "url": "https://youtu.be/err789",
            "title": None,
            "transcript": None,
            "summary": None,
            # context is non-empty even on failure — must be ignored by the gate
            "context": "Failed to transcribe: some long error context string here",
            "error": "boom",
        }
    ]
    enriched = await _run("watch this", _youtube_urls_arg("err789"), results)
    assert SIGNPOST in enriched
    assert "https://youtu.be/err789" in enriched


async def test_two_thin_urls_emit_two_signposts():
    """(d) Two thin-transcript URLs emit one signpost per URL."""
    results = [
        {
            "success": True,
            "video_id": "v1",
            "url": "https://youtu.be/v1",
            "title": "One",
            "transcript": "",
            "summary": None,
            "context": "ctx1",
        },
        {
            "success": False,
            "video_id": "v2",
            "url": "https://youtu.be/v2",
            "title": None,
            "transcript": None,
            "summary": None,
            "context": "ctx2 error",
            "error": "nope",
        },
    ]
    enriched = await _run("watch these", _youtube_urls_arg("v1", "v2"), results)
    assert enriched.count(SIGNPOST) == 2
    assert "https://youtu.be/v1" in enriched
    assert "https://youtu.be/v2" in enriched
