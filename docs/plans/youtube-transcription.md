# YouTube Link Transcription

**Status**: Planned
**Priority**: P1
**Created**: 2026-01-20

## Problem

When users share YouTube links, Valor stores the URL in the link database but doesn't understand the video content. Users often share videos expecting Valor to understand what's being discussed.

## Current Flow

```
User sends: "Check this out https://youtube.com/watch?v=xxx"
    ↓
Bridge extracts URL, stores in links table
    ↓
Clawdbot receives message with raw URL
    ↓
Claude has no context about video content
```

## Proposed Solution

Automatically transcribe YouTube videos and include the transcript in the message context.

### Components Needed

1. **YouTube Detection**: Identify YouTube URLs (youtube.com, youtu.be)
2. **Audio Extraction**: Use yt-dlp to download audio
3. **Transcription**: Use Whisper API (already have)
4. **Summarization**: Optionally summarize long transcripts

## Implementation

### 1. YouTube URL Detection

```python
YOUTUBE_PATTERNS = [
    r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
    r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
    r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
]

def extract_youtube_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    for pattern in YOUTUBE_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None
```

### 2. Audio Download with yt-dlp

```python
async def download_youtube_audio(video_id: str, output_dir: Path) -> Path | None:
    """Download audio from YouTube video."""
    import yt_dlp

    output_path = output_dir / f"youtube_{video_id}.mp3"

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': str(output_path).replace('.mp3', ''),
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f'https://youtube.com/watch?v={video_id}'])

    return output_path if output_path.exists() else None
```

### 3. Transcription (Reuse Existing)

Already have `transcribe_voice()` function using Whisper API.

### 4. Integration Flow

```python
async def process_youtube_url(url: str) -> str:
    """Process YouTube URL and return transcript summary."""
    video_id = extract_youtube_id(url)
    if not video_id:
        return ""

    # Download audio
    audio_path = await download_youtube_audio(video_id, MEDIA_DIR / "youtube")
    if not audio_path:
        return f"[YouTube video, could not download: {url}]"

    # Transcribe
    transcript = await transcribe_voice(audio_path)
    if not transcript:
        return f"[YouTube video, transcription failed: {url}]"

    # Summarize if too long (>2000 chars)
    if len(transcript) > 2000:
        # Use Ollama or Claude to summarize
        summary = await summarize_text(transcript, max_length=500)
        return f"[YouTube video transcript summary: {summary}]"

    return f"[YouTube video transcript: {transcript}]"
```

## New Flow

```
User sends: "Check this out https://youtube.com/watch?v=xxx"
    ↓
Bridge detects YouTube URL
    ↓
Downloads audio via yt-dlp
    ↓
Transcribes via Whisper API
    ↓
Summarizes if long (>2000 chars)
    ↓
Stores link with transcript/summary in links table
    ↓
Passes enriched message to clawdbot:
  "Check this out https://youtube.com/watch?v=xxx
   [YouTube transcript: The video discusses...]"
    ↓
Claude can discuss video content intelligently
```

## Files to Modify

- `bridge/telegram_bridge.py`: Add YouTube processing in message handler
- `tools/link_analysis/__init__.py`: Add YouTube detection functions
- Create `tools/youtube/` for dedicated YouTube tool (optional)

## Dependencies

- `yt-dlp`: `pip install yt-dlp`
- `ffmpeg`: Required by yt-dlp for audio extraction (brew install ffmpeg)
- OpenAI API key for Whisper (already have)

## Edge Cases

- **Very long videos (>1hr)**: Limit to first 10-15 minutes or skip
- **Live streams**: Skip (can't download)
- **Age-restricted videos**: May fail, return error message
- **Private videos**: Will fail, return error message
- **Rate limiting**: Cache transcripts by video ID to avoid re-processing

## Caching

Store transcripts in the links table:

```sql
-- ai_summary column already exists in links table
UPDATE links SET ai_summary = ? WHERE url = ?
```

## Testing

1. Send YouTube video link → Valor should summarize content
2. Send YouTube Shorts → Should work the same
3. Send youtu.be link → Should detect and process
4. Send very long video → Should summarize first portion
5. Send private video → Should gracefully handle error

## Estimated Effort

3-4 hours for full implementation including caching
