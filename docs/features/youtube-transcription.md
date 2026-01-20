# YouTube Link Transcription

**Status**: Implemented
**Implemented**: 2026-01-20

## Overview

When users share YouTube links, Valor automatically downloads the audio, transcribes it using OpenAI Whisper, and includes the transcript (or summary for long videos) in the message context. This allows Claude to understand and discuss video content intelligently.

## Features

### YouTube URL Detection

Supports multiple YouTube URL formats:
- `youtube.com/watch?v=VIDEO_ID`
- `youtu.be/VIDEO_ID`
- `youtube.com/shorts/VIDEO_ID`
- `youtube.com/embed/VIDEO_ID`
- `youtube.com/v/VIDEO_ID`

### Audio Extraction

Uses yt-dlp (YouTube-DL fork) to download audio:
- Downloads best available audio quality
- Converts to MP3 format (192K quality)
- Caches downloaded audio by video ID to avoid re-downloads

### Transcription

Uses OpenAI Whisper API for accurate speech-to-text:
- Supports multiple audio formats (MP3, M4A, WAV, WebM, OGG, Opus)
- Handles various languages automatically

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
[If >15 min]     -> Returns "[Video too long to transcribe: title (mm:ss)]"
    |
    v
Downloads audio via yt-dlp (or uses cache)
    |
    v
Transcribes via Whisper API
    |
    v
[If >2000 chars] -> Summarizes with GPT-4o-mini
    |
    v
Passes enriched message to clawdbot:
  "Check this out https://youtube.com/watch?v=xxx

   [YouTube video - Title transcript summary: The video discusses...]"
    |
    v
Claude can discuss video content intelligently
```

## Edge Case Handling

| Case | Behavior |
|------|----------|
| Videos >15 minutes | Returns message with video title and duration, skips transcription |
| Live streams | Detected and skipped with informative message |
| Private videos | Download fails, returns error message gracefully |
| Age-restricted | May fail, returns error message |
| Network errors | Logs error, continues with original message |
| Whisper API failure | Returns "transcription failed" message |
| Already cached | Uses cached audio file, skips download |

## Implementation Files

- `tools/link_analysis/__init__.py`: YouTube detection and processing functions
  - `extract_youtube_id()` - Extract video ID from URL
  - `is_youtube_url()` - Check if URL is YouTube
  - `extract_youtube_urls()` - Find all YouTube URLs in text
  - `get_youtube_video_info()` - Get video metadata via yt-dlp
  - `download_youtube_audio()` - Download audio via yt-dlp
  - `transcribe_audio_file()` - Transcribe using Whisper API
  - `summarize_transcript()` - Summarize long transcripts with GPT-4o-mini
  - `process_youtube_url()` - Full processing pipeline
  - `process_youtube_urls_in_text()` - Process all YouTube URLs in message

- `bridge/telegram_bridge.py`: Integration with message handler
  - Detects YouTube URLs before processing
  - Calls `process_youtube_urls_in_text()` for transcription
  - Enriches message with transcript/summary context

## Dependencies

### Python Packages
- `yt-dlp>=2024.1.0` - YouTube audio download

### System Requirements
- `ffmpeg` - Required by yt-dlp for audio extraction
  - macOS: `brew install ffmpeg`
  - Ubuntu: `apt install ffmpeg`
  - Windows: Download from ffmpeg.org

### API Keys
- `OPENAI_API_KEY` - For Whisper transcription and GPT summarization

## Configuration

### Constants (in link_analysis)

```python
# Maximum video duration (15 minutes)
MAX_VIDEO_DURATION = 900

# Audio cache directory
YOUTUBE_MEDIA_DIR = Path("data/media/youtube")
```

## Testing

1. Send YouTube video link -> Valor should include transcript in response context
2. Send YouTube Shorts -> Should work the same as regular videos
3. Send youtu.be link -> Should detect and process
4. Send video >15 min -> Should return "too long" message with duration
5. Send live stream -> Should return "live stream" message
6. Send private video -> Should gracefully handle with error message
7. Send same video twice -> Should use cached audio on second request
