# Transcription

## Overview

Dual-backend audio transcription tool. Uses **SuperWhisper** (local macOS app) as the primary backend for free, fast transcription. Falls back to **OpenAI Whisper API** when SuperWhisper is unavailable.

**Backends:**
1. **SuperWhisper** (primary) - Local macOS app, no API cost, fast
2. **OpenAI Whisper API** (fallback) - Cloud API, paid per minute

**Capabilities**: `transcribe`, `analyze`

## How It Works

When `transcribe()` is called:

1. Check if SuperWhisper is running (cached for 60s via `pgrep`)
2. If running: send audio via `open -g -a superwhisper`, poll for `meta.json`
3. If not running or times out (30s): fall back to OpenAI Whisper API
4. Return identical format regardless of backend

## Quick Start

```python
from tools.transcribe import transcribe

# Auto-selects best available backend
result = transcribe("audio.ogg")
print(result["text"])

# With timestamps (always uses OpenAI)
result = transcribe("audio.ogg", timestamps=True)
print(result["words"])
```

## Installation

### SuperWhisper (Primary)

1. Install [SuperWhisper](https://superwhisper.com) from the Mac App Store or website
2. Ensure it is running (`pgrep -x superwhisper` should return a PID)
3. Disable auto-paste: SuperWhisper > Preferences > Advanced > disable auto-paste

Recordings are stored at `~/Documents/superwhisper/recordings/` by default.
Override with the `SUPERWHISPER_RECORDINGS_DIR` environment variable.

### OpenAI Whisper API (Fallback)

Set the `OPENAI_API_KEY` environment variable.

```bash
# Verify
python -c "import os; assert os.environ.get('OPENAI_API_KEY')"
```

## Supported Formats

- MP3 (`.mp3`)
- WAV (`.wav`)
- M4A (`.m4a`)
- OGG (`.ogg`) - Telegram voice messages
- FLAC (`.flac`)
- WebM (`.webm`)
- MP4 (`.mp4`)
- MPEG (`.mpeg`, `.mpga`)

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `SUPERWHISPER_RECORDINGS_DIR` | `~/Documents/superwhisper/recordings/` | SuperWhisper recordings directory |
| `OPENAI_API_KEY` | (required for fallback) | OpenAI API key |

## API Reference

### `transcribe(audio_source, language=None, response_format="json", timestamps=False, prompt=None)`

Returns a dict with:
- `text` - Transcribed text
- `language` - Detected language (None for SuperWhisper)
- `duration` - Audio duration in seconds (when available)
- `error` - Error message (only on failure)

### `transcribe_with_timestamps(audio_source, language=None)`

Shortcut for transcription with word-level timestamps. Always uses OpenAI backend.

### `get_supported_formats()`

Returns set of supported audio format extensions.

## Bridge Integration

`bridge/media.py:transcribe_voice()` calls `tools.transcribe.transcribe()` to handle voice messages from Telegram. The bridge does not make direct OpenAI API calls for transcription.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| SuperWhisper not detected | Ensure the app is running: `pgrep -x superwhisper` |
| Transcription times out | SuperWhisper may be busy; falls back to OpenAI automatically |
| No OPENAI_API_KEY | Set the env var for fallback to work |
| Wrong language | Use `language="en"` parameter (OpenAI backend only) |
| Format not supported | Check `get_supported_formats()` for valid extensions |
