# YouTube Link Transcription

**Status**: Implemented
**Implemented**: 2026-01-20
**Updated**: 2026-04-06

## Overview

When users share YouTube links, Valor automatically fetches the transcript and includes it (or a summary for long videos) in the message context. This allows Claude to understand and discuss video content intelligently.

The primary path uses the YouTube caption API (no API key required). If captions are unavailable, it falls back to downloading audio and transcribing with OpenAI Whisper. When both paths fail, the agent receives an actionable failure message instead of the original unaltered text.

## Features

### YouTube URL Detection

Supports multiple YouTube URL formats:
- `youtube.com/watch?v=VIDEO_ID`
- `youtu.be/VIDEO_ID`
- `youtube.com/shorts/VIDEO_ID`
- `youtube.com/embed/VIDEO_ID`
- `youtube.com/v/VIDEO_ID`

### Caption-First Transcription (Primary Path)

Uses `youtube-transcript-api` to fetch auto-generated or manual captions:
- No API key required
- Works for any video with captions (the vast majority of YouTube content)
- Fast and stateless — no audio download needed
- Falls through to Whisper path if captions are unavailable

### Audio Transcription (Whisper Fallback)

If captions are unavailable, falls back to audio download + OpenAI Whisper:
- Uses yt-dlp to download best available audio quality, converted to MP3
- Caches downloaded audio by video ID to avoid re-downloads
- Requires `OPENAI_API_KEY` environment variable

### Smart Summarization

For transcripts longer than 2000 characters:
- Uses GPT-4o-mini to generate concise summaries (500 char max)
- Preserves key information and main points
- Falls back to truncation if summarization fails

## Message Flow

```
User sends: "Check this out https://youtube.com/watch?v=xxx"
    |
    v
Bridge detects YouTube URL
    |
    v
Gets video info (title, duration, live status)
    |
    v
[If live stream] -> Returns "[YouTube Live Stream: title]"
[If too long]    -> Returns "[Video too long to transcribe: title (mm:ss)]"
    |
    v
Try YouTubeTranscriptApi().fetch(video_id) [caption path]
    |
    v
[If captions found] -> Join segments into transcript text, proceed to summarization
[If TranscriptsDisabled / NoTranscriptFound / VideoUnavailable] -> fall through to Whisper
    |
    v
[Whisper path] Download audio via yt-dlp (or use cache)
    |
    v
[If OPENAI_API_KEY set] Transcribe via Whisper API -> proceed to summarization
[If no key or download fails] -> return actionable failure context
    |
    v
[If >2000 chars] -> Summarize with GPT-4o-mini
    |
    v
Passes enriched message to agent:
  "Check this out https://youtube.com/watch?v=xxx

   [YouTube video - Title transcript: The video discusses...]"
    |
    v
Claude can discuss video content intelligently
```

## Enrichment Behavior

`bridge/enrichment.py` always applies the YouTube-enriched text to the agent context, regardless of whether transcription succeeded or failed. This ensures the agent receives either a transcript or an actionable failure explanation — never just the original URL with no context.

## Edge Case Handling

| Case | Behavior |
|------|----------|
| Captions available | Caption path succeeds; no audio download needed |
| Captions unavailable, Whisper configured | Falls back to yt-dlp + Whisper transcription |
| Captions unavailable, no Whisper key | Agent receives actionable failure context with manual workaround suggestion |
| Videos beyond duration limit | Returns message with video title and duration, skips transcription |
| Live streams | Detected and skipped with informative message |
| Private videos | Download/caption fetch fails, returns error message gracefully |
| Age-restricted | May fail, returns error message |
| Network errors | Logs error, continues with original message |
| Already cached (audio) | Uses cached audio file, skips download |
| youtube-transcript-api not installed | Falls through to Whisper path with a warning log |

## Implementation Files

- `tools/link_analysis/__init__.py`: YouTube detection and processing functions
  - `extract_youtube_id()` - Extract video ID from URL
  - `is_youtube_url()` - Check if URL is YouTube
  - `extract_youtube_urls()` - Find all YouTube URLs in text
  - `get_youtube_video_info()` - Get video metadata via yt-dlp
  - `download_youtube_audio()` / `download_youtube_audio_async()` - Download audio via yt-dlp
  - `transcribe_audio_file()` - Transcribe using Whisper API (fallback)
  - `summarize_transcript()` - Summarize long transcripts with GPT-4o-mini
  - `process_youtube_url()` - Full processing pipeline (caption-first)
  - `process_youtube_urls_in_text()` - Process all YouTube URLs in message

- `bridge/enrichment.py`: Deferred message enrichment
  - Calls `process_youtube_urls_in_text()` for transcription
  - Always applies `yt_enriched` text to agent context (no `if successful > 0` guard)

## Dependencies

### Python Packages
- `youtube-transcript-api>=0.6.0` - Caption-based transcription (no API key required, primary path)
- `yt-dlp>=2024.1.0` - YouTube audio download (Whisper fallback only)

### System Requirements
- `ffmpeg` - Required by yt-dlp for audio extraction (only needed for Whisper fallback)
  - macOS: `brew install ffmpeg`
  - Ubuntu: `apt install ffmpeg`
  - Windows: Download from ffmpeg.org

### API Keys
- `OPENAI_API_KEY` - Optional; required only for Whisper transcription and GPT summarization

## Configuration

### Constants (in link_analysis)

```python
# Maximum video duration (env var YOUTUBE_MAX_VIDEO_DURATION, default 10 hours)
MAX_VIDEO_DURATION = int(os.getenv("YOUTUBE_MAX_VIDEO_DURATION", "36000"))

# Audio cache directory (Whisper fallback only)
YOUTUBE_MEDIA_DIR = Path("data/media/youtube")
```

## Testing

1. Send YouTube video link with captions → Valor should include transcript in response context
2. Send YouTube Shorts → Should work the same as regular videos (usually have captions)
3. Send youtu.be link → Should detect and process
4. Send a video known to have no captions and no Whisper key → Agent should receive actionable failure message
5. Send video beyond duration limit → Should return "too long" message with duration
6. Send live stream → Should return "live stream" message
7. Send private video → Should gracefully handle with error message
8. Send same video twice → Should use cached audio file on second request (if Whisper path was used)
