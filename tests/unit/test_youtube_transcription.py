"""Unit tests for YouTube caption-first transcription and enrichment suppression fix.

Tests:
1. Caption success path — mock YouTubeTranscriptApi returns segments
2. All-fail path — captions disabled, Whisper not configured
3. Enrichment always-apply — bridge/enrichment.py sets enriched_text even on failure
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_caption_segments(text: str) -> list[dict]:
    """Produce minimal transcript API segment list from a text string."""
    words = text.split()
    return [{"text": w, "start": i * 1.0, "duration": 0.9} for i, w in enumerate(words)]


# ---------------------------------------------------------------------------
# 1. Caption success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_youtube_url_caption_success():
    """When YouTubeTranscriptApi.fetch() returns snippets, result is successful and context contains transcript."""
    expected_text = "Hello world this is a test transcript"

    mock_snippet = MagicMock()
    mock_snippet.text = expected_text
    mock_fetched = MagicMock()
    mock_fetched.__iter__ = MagicMock(return_value=iter([mock_snippet]))
    mock_api_instance = MagicMock()
    mock_api_instance.fetch.return_value = mock_fetched

    with (
        patch(
            "tools.link_analysis.get_youtube_video_info",
            return_value={"title": "Test Video", "duration": 120, "is_live": False},
        ),
        patch(
            "youtube_transcript_api.YouTubeTranscriptApi",
            return_value=mock_api_instance,
        ),
    ):
        from tools.link_analysis import process_youtube_url

        result = await process_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result["success"] is True
    assert result["transcript"] == expected_text
    assert expected_text in result["context"]
    # Whisper should NOT have been called
    assert "transcription failed" not in result.get("context", "")


@pytest.mark.asyncio
async def test_process_youtube_url_caption_success_long_transcript():
    """Transcripts >2000 chars are passed through summarize_transcript."""
    long_text = "word " * 500  # 2500 chars when joined

    mock_snippet = MagicMock()
    mock_snippet.text = long_text.strip()
    mock_fetched = MagicMock()
    mock_fetched.__iter__ = MagicMock(return_value=iter([mock_snippet]))
    mock_api_instance = MagicMock()
    mock_api_instance.fetch.return_value = mock_fetched

    with (
        patch(
            "tools.link_analysis.get_youtube_video_info",
            return_value={"title": "Long Video", "duration": 600, "is_live": False},
        ),
        patch(
            "youtube_transcript_api.YouTubeTranscriptApi",
            return_value=mock_api_instance,
        ),
        patch(
            "tools.link_analysis.summarize_transcript",
            new_callable=AsyncMock,
            return_value="Short summary of the long video",
        ) as mock_summarize,
    ):
        from tools.link_analysis import process_youtube_url

        result = await process_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result["success"] is True
    assert mock_summarize.called
    assert "Short summary" in result["context"]


# ---------------------------------------------------------------------------
# 2. All-fail path (captions disabled + no Whisper key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_youtube_url_all_fail_path():
    """When captions are disabled and Whisper is not configured, returns actionable failure context."""
    from youtube_transcript_api import TranscriptsDisabled

    mock_api_instance = MagicMock()
    mock_api_instance.fetch.side_effect = TranscriptsDisabled("dQw4w9WgXcQ")

    with (
        patch(
            "tools.link_analysis.get_youtube_video_info",
            return_value={"title": "No Caption Video", "duration": 90, "is_live": False},
        ),
        patch(
            "youtube_transcript_api.YouTubeTranscriptApi",
            return_value=mock_api_instance,
        ),
        patch(
            "tools.link_analysis.download_youtube_audio_async",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        from tools.link_analysis import process_youtube_url

        result = await process_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result["success"] is False
    assert "No Caption Video" in result["context"]
    # Context must be actionable — suggests manual workaround
    assert "paste" in result["context"].lower() or "transcript" in result["context"].lower()
    assert "unavailable" in result["context"].lower()


@pytest.mark.asyncio
async def test_process_youtube_url_caption_import_error_fallback():
    """If youtube-transcript-api is not installed, falls through to Whisper path gracefully."""
    with (
        patch(
            "tools.link_analysis.get_youtube_video_info",
            return_value={"title": "Test Video", "duration": 60, "is_live": False},
        ),
        patch.dict("sys.modules", {"youtube_transcript_api": None}),
        patch(
            "tools.link_analysis.download_youtube_audio_async",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        from tools.link_analysis import process_youtube_url

        result = await process_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    # Should fail gracefully without raising
    assert result["success"] is False
    assert result.get("context") != ""


# ---------------------------------------------------------------------------
# 3. Enrichment always-apply behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrichment_applies_yt_text_even_on_failure():
    """bridge/enrichment.py sets enriched_text = yt_enriched even when successful == 0."""
    failure_context = "[YouTube video: transcript unavailable (captions not found; Whisper API not configured). To discuss this video, paste the transcript or a summary directly into the chat.]"
    original_text = "Check this out https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    enriched_with_failure = original_text + "\n\n" + failure_context

    mock_results = [
        {
            "success": False,
            "video_id": "dQw4w9WgXcQ",
            "context": failure_context,
            "error": "Transcription failed",
        }
    ]

    with patch(
        "tools.link_analysis.process_youtube_urls_in_text",
        new_callable=AsyncMock,
        return_value=(enriched_with_failure, mock_results),
    ):
        from bridge.enrichment import enrich_message

        result = await enrich_message(
            telegram_client=None,
            message_text=original_text,
            youtube_urls=json.dumps([("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ")]),
        )

    # The failure context MUST be in the enriched output
    assert failure_context in result
    assert "paste" in result.lower() or "transcript" in result.lower()


@pytest.mark.asyncio
async def test_enrichment_applies_yt_text_on_success():
    """bridge/enrichment.py sets enriched_text = yt_enriched when successful > 0 (existing behavior preserved)."""
    transcript_context = "[YouTube video - My Video transcript: Great content here]"
    original_text = "Check this out https://www.youtube.com/watch?v=abc123"
    enriched_with_transcript = original_text + "\n\n" + transcript_context

    mock_results = [
        {
            "success": True,
            "video_id": "abc123",
            "context": transcript_context,
        }
    ]

    with patch(
        "tools.link_analysis.process_youtube_urls_in_text",
        new_callable=AsyncMock,
        return_value=(enriched_with_transcript, mock_results),
    ):
        from bridge.enrichment import enrich_message

        result = await enrich_message(
            telegram_client=None,
            message_text=original_text,
            youtube_urls=json.dumps([("https://www.youtube.com/watch?v=abc123", "abc123")]),
        )

    assert transcript_context in result


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_youtube_url_invalid_url():
    """Non-YouTube URL returns early with success=False."""
    from tools.link_analysis import process_youtube_url

    result = await process_youtube_url("https://example.com/not-youtube")

    assert result["success"] is False
    assert result.get("error") == "Not a valid YouTube URL"


@pytest.mark.asyncio
async def test_process_youtube_urls_in_text_no_urls():
    """Text with no YouTube URLs returns unchanged text and empty results list."""
    from tools.link_analysis import process_youtube_urls_in_text

    text = "No YouTube links here, just text."
    result_text, results = await process_youtube_urls_in_text(text)

    assert result_text == text
    assert results == []
